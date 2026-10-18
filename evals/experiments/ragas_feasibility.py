#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""RAGAS 可行性实验（必须在隔离 venv ~/ragas-probe-venv 运行，禁止进项目 .venv）。

实测内容（按项目"评估框架能不能用框架"的决策需求设计）：
1. 安装/导入成本：ragas 0.4.3 在 py3.10 可装，但 import 即断裂——
   ragas/llms/base.py 顶层 import langchain_community.chat_models.vertexai，
   而 langchain-community 0.4 已移除该模块（仅 ragas 的 isinstance 名单用）。
   本探针以桩模块绕过（仅用于可行性判定，生产采纳需另寻版本组合）。
2. 网关兼容：ragas 全指标走 instructor 结构化输出。sensenova 网关对
   function calling / response_format 的支持度直接决定 ragas 可用性——
   这正是实验 A（structured_output_probe.py）测的东西，这里看 ragas 实际撞到什么。
3. 同样本口径对比：3 条合成 RAG 样本上，
   ragas faithfulness / context_precision / context_recall / answer_relevancy
   vs 我们 evals/metrics.py 的口径（claims 比值 micro/macro + 规则 precision/recall@k）。
4. answer_relevancy 需要 embeddings：用本地 bge /embed 端点适配（数据不出境）。

运行：
    ~/ragas-probe-venv/bin/python evals/experiments/ragas_feasibility.py

实验结论（2026-10-19：ragas 0.4.3 / py3.10 / 隔离 venv ~/ragas-probe-venv）
----------------------------------------------------------------------
1. 导入即裂：ragas/llms/base.py 顶层 import langchain_community.chat_models.vertexai，
   而 langchain-community 0.4 已删该模块。探针靠给 site-packages 打桩才 import 成功
   （仅用于可行性判定，生产不可这么干）。
2. 两套平行 API 互不接受：旧体系（ragas.metrics 单例 + evaluate() +
   LangchainLLMWrapper + V1 列名数据集）与新体系（ragas.metrics.collections +
   InstructorLLM + 用具名参数直调 ascore）。evaluate() 的 isinstance(Metric)
   校验拒绝 collections 实例；collections 的 _validate_llm 拒绝 LangchainLLMWrapper
   （后者已标弃用，推荐 llm_factory）。
3. 新体系 sync/async glue 双坏：同步 score() + 同步客户端报 "Cannot use
   agenerate() with a synchronous client"；同步 score() + AsyncOpenAI 与
   asyncio.run(ascore()) + AsyncOpenAI 均报 "'coroutine' object has no attribute
   'choices'"（instructor 异步调用未被 await，纯客户端 bug，与网关无关）。
   → 现代路径在本环境不可用，两次修复均失败。
4. 同样本对比（3 条合成样本，覆盖全有据/全编造/半据）：
     faithfulness   ragas-L 0.167（逐样本 0.0/0.0/0.5，全有据样本也判 0）
                    vs 我方 0.429（2/2、0/3、1/2，与样本设计真值一致）
     context_precision 1.000 vs 我方 0.833(@2)；context_recall 1.000 vs 1.000
     answer_relevancy 0.447（我方无对应自研指标）
   ragas 的 faithfulness 明显失真——与 structured_output_probe 的网关结构化输出
   不可靠（json_object 38% / function calling 50%）互证：内部 verdict 调用劣化后
   静默按"不支持"计。
5. 成本：ragas 17-19 次 LLM 调用 / 52s vs 我方 3 次 / 12s（同网关同模型）。
6. 依赖重量：pip 安装约 90 个包（langchain 全家桶 + instructor + datasets +
   pandas + pyarrow …），且需要 py3.10 兼容版本组合。
7. 结论：**不采纳**。自研口径已按 RAGAS 定义对齐（micro/macro 声明比值），
   快 4 倍、省 5 倍调用、判定与人工设计真值一致；ragas 的 API 动荡 + 网关摩擦
   是持续维护成本。build_embeddings() 的本地 bge 适配（数据不出境）保留备用。
"""
from __future__ import annotations

import asyncio
import json
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List

PROJECT_ROOT = Path("/home/sp/TaoZhi-EcomAgent")
sys.path.insert(0, str(PROJECT_ROOT))  # evals.metrics 只依赖 numpy

import httpx

# ───────────────────────── 网关凭据（手工解析 .env，不引项目依赖） ─────────────────────────

def load_env() -> Dict[str, str]:
    out: Dict[str, str] = {}
    for line in (PROJECT_ROOT / ".env").read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        out[k.strip()] = v.strip()
    return out


ENV = load_env()
BASE_URL = ENV.get("BASE_URL", "https://token.sensenova.cn/v1")
API_KEY = ENV.get("API_KEY", "")
MODEL = ENV.get("MODEL", "sensenova-6.8-flash-lite")

# ───────────────────────── 合成样本（中文电商域，覆盖 有据/编造/半据 三态） ─────────────────────────

SAMPLES = [
    {
        "id": "S1-grounded",
        "question": "LX-20 65W 氮化镓充电器双口同充时单口功率是多少？",
        "contexts": [
            "LX-20 总输出功率 65W，接口为双 C 口加单 A 口；双口同充时单口功率降为 45W。",
            "LX-20 支持 PD3.0 与 QC4+ 快充协议，壳体材质为 VO 级阻燃 PC。",
        ],
        "answer": "双口同充时单口功率降为 45W，充电器总功率 65W。",
        "reference": "双口同充时单口降为 45W",
        "relevant_idx": [0, 1],
    },
    {
        "id": "S2-hallucinated",
        "question": "LX-20 充电器的输入电压范围是多少？",
        "contexts": [
            "LX-20 支持 100-240V 宽电压输入，适用于多国电网环境。",
        ],
        "answer": "输入电压范围为 200-240V，并具备过压保护与雷击浪涌保护功能。",
        "reference": "支持 100-240V 宽电压输入",
        "relevant_idx": [0],
    },
    {
        "id": "S3-partial",
        "question": "用 LX-20 给 iPhone 15 充电，30 分钟能充到多少？会员有优惠吗？",
        "contexts": [
            "LX-20 搭配 iPhone 15 已确认兼容，PD 快充峰值 65W，约 30 分钟充至 50%。",
            "本店会员日满减活动：下单立减 20 元，活动截止 2026-10-31。",
        ],
        "answer": "约 30 分钟可充至 50%。会员日期间下单可享 95 折优惠。",
        "reference": "约 30 分钟充至 50%",
        "relevant_idx": [0],
    },
]


# ───────────────────────── 本地 bge embeddings 适配（数据不出境） ─────────────────────────

def build_embeddings():
    """ragas 0.4.3 现代接口 BaseRagasEmbedding：只需 embed_text/aembed_text，
    批量方法顺手覆盖以打满本地 /embed 的 256 条批处理能力。

    踩坑记录：ragas 0.4.3 同时存在旧接口 BaseRagasEmbeddings（embed_query/
    embed_documents，pydantic dataclass 化）与新接口 BaseRagasEmbedding（ABC）。
    旧接口与普通类多重继承会触发 "Can't instantiate abstract class"——
    pydantic 元类丢弃了第二个基类的方法实现。直接用新接口，勿搞多重继承。
    """
    from ragas.embeddings.base import BaseRagasEmbedding

    class LocalBge(BaseRagasEmbedding):
        def __init__(self, url: str = "http://127.0.0.1:18555"):
            super().__init__()
            self._client = httpx.Client(timeout=60, trust_env=False)
            self._url = url

        def _embed(self, texts: List[str]) -> List[List[float]]:
            r = self._client.post(f"{self._url}/embed", json={"texts": texts})
            r.raise_for_status()
            return r.json()["vectors"]

        def embed_text(self, text: str, **kwargs: Any) -> List[float]:
            return self._embed([text])[0]

        async def aembed_text(self, text: str, **kwargs: Any) -> List[float]:
            return await asyncio.to_thread(self.embed_text, text)

        def embed_texts(self, texts: List[str], **kwargs: Any) -> List[List[float]]:
            return self._embed(list(texts))

        async def aembed_texts(self, texts: List[str], **kwargs: Any) -> List[List[float]]:
            return await asyncio.to_thread(self.embed_texts, list(texts))

    return LocalBge()


# ───────────────────────── 我方口径：claims 判定 + evals.metrics ─────────────────────────

CLAIM_SYSTEM = """你是评测判定器。给定问题、实际答案与引用文档，按 RAGAS 口径判定忠实度。

1. 把【实际答案】拆成原子声明（claims）：每条是一个可独立判断真假的断言；
   客套话、过渡句、格式说明不计入。
2. 逐条判定该声明能否从【引用文档】得到支撑——能复述或推出即算支撑；
   引用文档里没有、或与引用内容矛盾即不支撑（后者是编造）。

只输出 JSON（不要其他文字）：
{"claims":声明总条数,"supported":有支撑的声明条数,"reason":"不超过40字"}"""


def our_faithfulness() -> Dict[str, Any]:
    """现状口径：普通 chat + 正则抠 JSON（run_evals.py 行为层同款解析）。"""
    import re
    from openai import OpenAI
    from evals.metrics import faithfulness_report

    oai = OpenAI(base_url=BASE_URL, api_key=API_KEY, timeout=180)
    verdicts = []
    for s in SAMPLES:
        payload = json.dumps({"问题": s["question"], "实际答案": s["answer"],
                              "引用文档": s["contexts"]}, ensure_ascii=False)
        t0 = time.time()
        try:
            text = oai.chat.completions.create(
                model=MODEL, max_tokens=2048, temperature=0.2,
                messages=[{"role": "system", "content": CLAIM_SYSTEM},
                          {"role": "user", "content": payload}],
            ).choices[0].message.content or ""
            m = re.search(r"\{.*\}", text, re.S)
            v = json.loads(m.group(0)) if m else {}
            v.setdefault("claims", 0)
            v.setdefault("supported", 0)
        except Exception as exc:
            v = {"claims": 0, "supported": 0, "reason": f"异常 {exc}"[:40]}
        v["latency_s"] = round(time.time() - t0, 2)
        verdicts.append({"id": s["id"], **v})
        print(f"  [我方] {s['id']} claims={v['claims']} supported={v['supported']} "
              f"{v['latency_s']}s reason={v.get('reason', '')[:30]}", flush=True)
    return {"detail": verdicts, "report": faithfulness_report(verdicts)}


def our_retrieval_metrics() -> Dict[str, Any]:
    """规则口径：二值标题相关（本探针用上下文序号当标题）。"""
    from evals.metrics import hit_at_k, precision_at_k, recall_at_k

    out = {}
    for k in (1, 2):
        rows = [([f"ctx{i}" for i in range(len(s["contexts"]))],
                 [f"ctx{i}" for i in s["relevant_idx"]]) for s in SAMPLES]
        out[f"precision@{k}"] = sum(precision_at_k(r, e, k) for r, e in rows) / len(rows)
        out[f"recall@{k}"] = sum(recall_at_k(r, e, k) for r, e in rows) / len(rows)
        out[f"hit@{k}"] = sum(hit_at_k(r, e, k) for r, e in rows) / len(rows)
    return out


# ───────────────────────── ragas 侧 ─────────────────────────

def run_ragas() -> Dict[str, Any]:
    """两条路都测：

    Path L（旧体系）：ragas.metrics 预建单例 + evaluate() + LangchainLLMWrapper
      —— 弃用告警但可用；LLM 经 LangChain ChatOpenAI 到网关。
    Path M（新体系）：ragas.metrics.collections 实例 + InstructorLLM（llm_factory）
      —— 0.4.3 主推；instructor 默认 function calling 模式直连网关。

    两套体系互不兼容（evaluate() 的 isinstance(Metric) 校验拒绝 collections 实例；
    collections 的 _validate_llm 拒绝 LangchainLLMWrapper）——本身就是 findings。
    """
    from langchain_core.embeddings import Embeddings
    from langchain_openai import ChatOpenAI
    from openai import OpenAI
    import openai.resources.chat.completions as _comp
    from ragas import EvaluationDataset, SingleTurnSample, evaluate
    from ragas.embeddings import LangchainEmbeddingsWrapper
    from ragas.llms import LangchainLLMWrapper, llm_factory
    from ragas.metrics import answer_relevancy, context_precision, context_recall, faithfulness
    from ragas.metrics.collections import Faithfulness
    from ragas.run_config import RunConfig

    calls = {"n": 0}

    def _wrap(cls):
        orig = cls.create

        def create(self, *a, **kw):
            calls["n"] += 1
            return orig(self, *a, **kw)

        cls.create = create

    _wrap(_comp.Completions)
    _wrap(_comp.AsyncCompletions)

    class LangchainBge(Embeddings):
        def _embed(self, texts: List[str]) -> List[List[float]]:
            r = httpx.post("http://127.0.0.1:18555/embed", json={"texts": texts}, timeout=60)
            r.raise_for_status()
            return r.json()["vectors"]

        def embed_documents(self, texts: List[str]) -> List[List[float]]:
            return self._embed(list(texts))

        def embed_query(self, text: str) -> List[float]:
            return self._embed([text])[0]

        async def aembed_documents(self, texts: List[str]) -> List[List[float]]:
            return await asyncio.to_thread(self.embed_documents, list(texts))

        async def aembed_query(self, text: str) -> List[float]:
            return await asyncio.to_thread(self.embed_query, text)

    samples = [SingleTurnSample(question=s["question"], answer=s["answer"],
                                contexts=s["contexts"], reference=s["reference"])
               for s in SAMPLES]
    dataset = EvaluationDataset(samples=samples)
    # 旧体系 evaluate() 走 V1 列名（user_input/response/retrieved_contexts/reference），
    # 内部 remap 成 V2；直接喂 V2 列名的 EvaluationDataset 会报缺列。
    from datasets import Dataset as HFDataset
    v1_dataset = HFDataset.from_list([
        {"user_input": s["question"], "response": s["answer"],
         "retrieved_contexts": s["contexts"], "reference": s["reference"]}
        for s in SAMPLES
    ])
    out: Dict[str, Any] = {"llm_calls_total": 0, "path_L": {}, "path_M": {}}

    # ─── Path L：旧体系四指标一次跑完 ───
    t0 = time.time()
    before = calls["n"]
    try:
        chat = ChatOpenAI(model=MODEL, base_url=BASE_URL, api_key=API_KEY,
                          temperature=0, max_tokens=2048, timeout=180)
        result = evaluate(dataset=v1_dataset,
                          metrics=[faithfulness, context_precision, context_recall, answer_relevancy],
                          llm=LangchainLLMWrapper(chat),
                          embeddings=LangchainEmbeddingsWrapper(LangchainBge()),
                          run_config=RunConfig(timeout=240, max_retries=1))
        df = result.to_pandas()
        cols = [c for c in ("faithfulness", "context_precision", "context_recall",
                            "answer_relevancy") if c in df.columns]
        out["path_L"] = {"ok": True, "wall_s": round(time.time() - t0, 1),
                         "llm_calls": calls["n"] - before,
                         "scores": {c: [None if str(v) == "nan" else round(float(v), 4)
                                        for v in df[c].tolist()] for c in cols},
                         "means": {c: round(float(df[c].astype(float).mean()), 4) for c in cols}}
    except Exception as exc:
        out["path_L"] = {"ok": False, "error": f"{type(exc).__name__}: {exc}"[:300],
                         "wall_s": round(time.time() - t0, 1), "llm_calls": calls["n"] - before}
    print(f"  [ragas-L] {out['path_L']}", flush=True)

    # ─── Path M：新体系 faithfulness + answer_relevancy 直调 ───
    t0 = time.time()
    before = calls["n"]
    try:
        from openai import AsyncOpenAI
        from ragas.metrics.collections import AnswerRelevancy

        # collections 的 score() 内部走 agenerate（异步），必须配 AsyncOpenAI；
        # 传同步 OpenAI 会报 "Cannot use agenerate() with a synchronous client"
        inst = llm_factory(MODEL, client=AsyncOpenAI(base_url=BASE_URL, api_key=API_KEY, timeout=180),
                           temperature=0, max_tokens=2048)
        local_emb = build_embeddings()
        specs = [("faithfulness", Faithfulness(llm=inst)),
                 ("answer_relevancy", AnswerRelevancy(llm=inst, embeddings=local_emb))]
        rows: List[Dict[str, Any]] = []
        for name, metric in specs:
            for s in SAMPLES:
                # collections 的 ascore 用具名参数且各指标不同：faithfulness 要
                # retrieved_contexts，answer_relevancy 只要 user_input/response——
                # 与旧体系 SingleTurnSample 又是两套形状。
                # 必须直接 asyncio.run(ascore())：同步 score() 包装异步客户端有 bug
                # （'coroutine' object has no attribute 'choices'，ragas 0.4.3）。
                kw = {"user_input": s["question"], "response": s["answer"]}
                if name == "faithfulness":
                    kw["retrieved_contexts"] = s["contexts"]
                r = asyncio.run(metric.ascore(**kw))
                val = r.value if getattr(r, "value", None) is not None else None
                rows.append({"metric": name, "id": s.reference,
                             "value": round(float(val), 4) if val is not None else None,
                             "reason": str(getattr(r, "reason", ""))[:80]})
        means = {}
        for name, _ in specs:
            vals = [r["value"] for r in rows if r["metric"] == name and r["value"] is not None]
            means[name] = round(sum(vals) / len(vals), 4) if vals else None
        out["path_M"] = {"ok": True, "wall_s": round(time.time() - t0, 1),
                         "llm_calls": calls["n"] - before, "scores": rows, "means": means}
    except Exception as exc:
        out["path_M"] = {"ok": False, "error": f"{type(exc).__name__}: {exc}"[:300],
                         "wall_s": round(time.time() - t0, 1), "llm_calls": calls["n"] - before}
    print(f"  [ragas-M] {out['path_M']}", flush=True)

    out["llm_calls_total"] = calls["n"]
    return out


def main() -> None:
    if not API_KEY:
        raise SystemExit("API_KEY 为空：检查 .env")
    print(f"网关 {BASE_URL}  模型 {MODEL}", flush=True)

    report: Dict[str, Any] = {"at": datetime.now().isoformat(timespec="seconds"),
                              "ragas_version": None, "our": {}, "ragas": {}}
    import ragas
    report["ragas_version"] = ragas.__version__
    print(f"ragas {ragas.__version__}（隔离 venv，vertexai 桩模块已就位）", flush=True)

    print("== 我方口径：claims 忠实度 ==", flush=True)
    report["our"]["faithfulness"] = our_faithfulness()
    print("== 我方口径：规则检索指标 ==", flush=True)
    report["our"]["retrieval"] = our_retrieval_metrics()

    print("== ragas 四指标（同样本） ==", flush=True)
    report["ragas"] = run_ragas()

    print("\n" + "=" * 92)
    print(f"{'指标':<22}{'ragas-L(旧体系)':>18}{'ragas-M(新体系)':>18}{'我方口径':>18}")
    print("-" * 92)
    pl = report["ragas"].get("path_L") or {}
    pm = report["ragas"].get("path_M") or {}
    our_f = report["our"]["faithfulness"]["report"]
    our_r = report["our"]["retrieval"]

    def l_cell(key: str) -> str:
        v = (pl.get("means") or {}).get(key)
        if v is not None:
            return f"{v:.3f}"
        return "FAIL" if not pl.get("ok") else "-"

    if pm.get("mean") is not None:
        m_cell = f"{pm['mean']:.3f}"
    else:
        m_cell = "FAIL" if not pm.get("ok") else "-"
    our_faith = f"{our_f['micro']:.3f}" if our_f.get("micro") is not None else "FAIL"
    pm_means = pm.get("means") or {}

    def m_cell_for(key: str) -> str:
        v = pm_means.get(key)
        if v is not None:
            return f"{v:.3f}"
        return "FAIL" if not pm.get("ok") else "-"

    print(f"{'faithfulness':<22}{l_cell('faithfulness'):>18}{m_cell_for('faithfulness'):>18}{our_faith:>18}  (micro)")
    for rk, ok in (("context_precision", "precision@2"), ("context_recall", "recall@2")):
        ov = our_r.get(ok)
        our_cell = f"{ov:.3f}" if ov is not None else "FAIL"
        print(f"{rk:<22}{l_cell(rk):>18}{m_cell_for(rk):>18}{our_cell:>18}  ({ok})")
    print(f"{'answer_relevancy':<22}{l_cell('answer_relevancy'):>18}"
          f"{m_cell_for('answer_relevancy'):>18}{'(无对应自研指标)':>18}")
    print(f"\nragas LLM 调用总数: {report['ragas'].get('llm_calls_total')}  "
          f"我方 claims 判定调用: {len(SAMPLES)}")
    print(f"Path L 错误: {(pl.get('error') or '-')[:150]}")
    print(f"Path M 错误: {(pm.get('error') or '-')[:150]}")
    out = PROJECT_ROOT / "evals" / "reports" / f"ragas_feasibility_{datetime.now().strftime('%Y%m%d-%H%M%S')}.json"
    out.write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"报告已存 {out}")


if __name__ == "__main__":
    main()
