#!/usr/bin/env python3
"""把评估集与逐例评估结果同步到 Arize Phoenix。

为什么需要单独一个脚本
----------------------
evals/ 的产物一直是文件：``datasets/generated/latest/cases.jsonl`` 是用例集，
``reports/*.json`` 是逐例判定结果。Phoenix 只被动接收应用上报的 span，而本项目
代码里没有 OTel 埋点，所以 Phoenix 界面上既看不到评估集，也看不到分数。

职责边界不变：评估在 ``evals/run_evals.py`` 跑（纯离线计算，不依赖 Phoenix），
本脚本只把已有产物搬进 Phoenix 供人查看与横向对比。Phoenix 没起时评估照常产出
报告，本脚本报一句警告退出 0——观测是旁路，不能反过来卡住评估。

两个入口
--------
``--dataset``  用例集 → Phoenix Datasets（每例 input / output / metadata）
``--report``   一次报告的逐例结果 → Phoenix Experiment（run + evaluation 分数）

幂等设计
--------
- 数据集按 ``metadata.case_id`` 去重，只补新增用例，重复跑不会重复插
- experiment 按名字去重，同名已存在则跳过（``--force`` 另建 ``-r2`` 后缀）
- evaluation 由服务端按 ``(experiment_run_id, name)`` upsert，重复推送即覆盖

运行环境
--------
phoenix 客户端装在独立 venv，不在项目 .venv 里（避免把观测依赖塞进生产环境）::

    ~/.local/share/ecom-agent/phoenix-venv/bin/python evals/sync_phoenix.py --all

环境变量
--------
``ECOM_PHOENIX_URL``      默认 http://127.0.0.1:6006
``ECOM_PHOENIX_DATASET``  默认 taozhi-eval-generated-v2
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
CASES_PATH = REPO_ROOT / "evals/datasets/generated/latest/cases.jsonl"
REPORTS_DIR = REPO_ROOT / "evals/reports"

DEFAULT_URL = os.environ.get("ECOM_PHOENIX_URL", "http://127.0.0.1:6006")
DEFAULT_DATASET = os.environ.get("ECOM_PHOENIX_DATASET", "taozhi-eval-generated-v2")

# 单条 answer 入库上限：报告里已截断过，这里再兜一层，避免 payload 撑爆
ANSWER_CHARS = 2000
# 引用正文上限：报告侧已截到 400 字（run_evals.EVIDENCE_QUOTE_CHARS），这里略放宽
EVIDENCE_CHARS = 500
# 单次请求超时。一次同步要发上千次串行调用（107 例 × 多条 evaluation），
# 服务端卡住时不能无限等——客户端各方法都支持 timeout，统一从这里取。
HTTP_TIMEOUT = 60


# ─────────────────────── 读取本地产物 ───────────────────────

def load_cases() -> list:
    if not CASES_PATH.is_file():
        sys.exit(f"找不到用例集：{CASES_PATH}\n先跑 `python -m evals.dataset.generate`。")
    return [json.loads(line) for line in CASES_PATH.read_text(encoding="utf-8").splitlines()
            if line.strip()]


def latest_report() -> Path:
    files = sorted(REPORTS_DIR.glob("*.json"))
    if not files:
        sys.exit(f"{REPORTS_DIR} 下没有报告，先跑一次评估。")
    return files[-1]


def case_to_example(c: dict) -> dict:
    """用例 → Phoenix dataset example。

    input 放"问什么"，output 放"答成什么样算对"，metadata 放可用于筛选的维度。
    这三者的划分决定了界面上能不能按类别筛、能不能看出金标准文档——
    output 里刻意保留 expected_documents / expected_facts，否则人在界面上
    没法判断某条答案到底错在哪。
    """
    return {
        "input": {
            "question": c["question"],
            "turns": c.get("turns") or [],
            "test_identity": c.get("test_identity") or {},
        },
        "output": {
            "expected_behavior": c.get("expected_behavior", ""),
            "expected_documents": c.get("expected_documents") or [],
            "expected_facts": c.get("expected_facts") or [],
            "pass_criteria": c.get("pass_criteria", ""),
            "forbidden_disclosure": c.get("forbidden_disclosure") or [],
        },
        "metadata": {
            "case_id": c["case_id"],
            "template": c.get("template"),
            "category": c.get("category"),
            "difficulty": c.get("difficulty"),
            "answerability": c.get("answerability"),
            "qtype": c.get("qtype"),
            "risk": c.get("risk"),
            "source": c.get("source"),
            "evidence_location": c.get("evidence_location"),
            "dataset": c.get("dataset"),
        },
    }


# ─────────────────────── 报告 → 逐例结果 ───────────────────────

def collect_results(report: dict) -> dict:
    """把三层报告拍平成 {case_id: {output, evaluations}}。

    三层的载体不同：行为层在 ``behavior.detail``（含答案与引用），检索层在
    ``retrieval.per_case``（只有分数），对抗层在 ``adversarial.detail``。
    合并到同一个 case_id 下，Phoenix 里一个用例就能看到"检索第几、答得对不对"。
    """
    out: dict = {}

    def slot(cid: str) -> dict:
        return out.setdefault(cid, {"output": {}, "evaluations": []})

    for d in (report.get("behavior") or {}).get("detail") or []:
        cid = d.get("id")
        if not cid:
            continue
        s = slot(cid)
        verdict = d.get("verdict") or {}
        s["output"].update({
            "answer": (d.get("answer") or "")[:ANSWER_CHARS],
            "verdict": verdict,
            "claims": d.get("claims"),
            "supported": d.get("supported"),
            # quote 必须带上：界面上判断"这句话有没有依据"靠的就是引用原文。
            # 只留标题只能看到"引了哪篇"，看不到"引了哪段"，归因会退回重跑。
            "citations": [{"title": c.get("title"), "chunk_id": c.get("chunk_id"),
                           "document_id": c.get("document_id"),
                           "quote": (c.get("quote") or "")[:EVIDENCE_CHARS]}
                          for c in (d.get("citations") or [])],
        })
        passed = d.get("pass")
        s["evaluations"].append({
            "name": "answer_pass", "kind": "LLM",
            "score": 1.0 if passed else 0.0,
            "label": "PASS" if passed else "FAIL",
            "explanation": (d.get("why") or "")[:500],
        })
        for name, key in (("answer_relevant", "relevant"), ("abstain", "abstain")):
            if key in verdict:
                val = bool(verdict.get(key))
                s["evaluations"].append({
                    "name": name, "kind": "LLM",
                    "score": 1.0 if val else 0.0,
                    "label": ("YES" if val else "NO") if key != "abstain"
                             else ("ABSTAIN" if val else "ANSWER"),
                    "explanation": None,
                })
        claims = d.get("claims") or 0
        supported = d.get("supported") or 0
        if claims:
            s["evaluations"].append({
                "name": "faithfulness_decl", "kind": "LLM",
                "score": supported / claims,
                "label": None,
                "explanation": f"{supported}/{claims} 条声明有引用原文支撑",
            })

    for c in (report.get("retrieval") or {}).get("per_case") or []:
        cid = c.get("case_id")
        if not cid:
            continue
        s = slot(cid)
        metrics = c.get("metrics") or {}
        s["output"].setdefault("retrieval", metrics)
        for m, v in metrics.items():
            if isinstance(v, (int, float)):
                s["evaluations"].append({
                    "name": f"retrieval.{m}", "kind": "CODE",
                    "score": float(v), "label": None, "explanation": None,
                })

    # 对抗层刻意不在此同步：它跑的是 evals/datasets/golden_adversarial.json，
    # id 形如 Z1/N1/C1，与评估集 case_id（GEN-T-*）实测零交集（18/18 全不匹配）。
    # 拍平进来只会全部落进"未匹配"，看起来同步了其实一条都没写进去。
    # 要让它可见，得单独建一个对抗数据集，那是另一件事。
    return out


# ─────────────────────── Phoenix 同步 ───────────────────────

def _attr(obj, key, default=None):
    """客户端里 Dataset 是对象、list() 元素是 dict，两种都得能取。"""
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


def _existing_case_ids(client, name: str) -> tuple:
    """(数据集对象, 已存在的 case_id 集合)；数据集不存在时返回 (None, set())。

    只捕 ValueError——那是 get_dataset 在"名字查不到"时抛的类型（实测）。
    连接失败、认证失败会抛别的异常，必须继续往上冒：把它们当成"不存在"会让
    后面走 create_dataset 分支，把一次网络抖动变成重复建集。
    """
    try:
        ds = client.datasets.get_dataset(dataset=name, timeout=HTTP_TIMEOUT)
    except ValueError:
        return None, set()
    ids = {(_attr(e, "metadata") or {}).get("case_id") for e in ds}
    return ds, {i for i in ids if i}


def sync_dataset(client, cases: list, name: str, dry_run: bool) -> None:
    examples = [case_to_example(c) for c in cases]
    ds, known = _existing_case_ids(client, name)

    if ds is None:
        print(f"[dataset] 新建：{name}（{len(examples)} 例）")
        if not dry_run:
            client.datasets.create_dataset(
                name=name, examples=examples, timeout=HTTP_TIMEOUT,
                dataset_description="淘智电商 Agent 评估集：input=问题/身份/多轮，"
                                    "output=预期行为/金标准文档/必备事实/禁披露，"
                                    "metadata=类别/难度/风险等筛选维度")
        return

    todo = [e for e in examples if e["metadata"]["case_id"] not in known]
    skipped = len(examples) - len(todo)
    if not todo:
        print(f"[dataset] {name} 已是最新：{len(known)} 例，无新增")
        return
    print(f"[dataset] {name} 追加 {len(todo)} 例（已存在 {skipped} 例，跳过）")
    if not dry_run:
        client.datasets.add_examples_to_dataset(dataset=name, examples=todo,
                                               timeout=HTTP_TIMEOUT)


def _experiment_exists(client, dataset_id: str, name: str) -> bool:
    """同名实验是否已存在。

    ``experiments.list()`` 的 dataset_id 是必填关键字参数（不传直接 TypeError）。
    这里刻意**不吞异常**：上一版用 ``except Exception: return False`` 兜底，结果
    list() 一抛错就被当成"不存在"，重复跑会静默建出两个同名实验。查不到就让它
    报错，别把"判断失败"伪装成"判断为否"。
    """
    items = client.experiments.list(dataset_id=dataset_id, timeout=HTTP_TIMEOUT)
    return any(_attr(x, "name") == name for x in items)


def sync_report(client, report_path: Path, report: dict, name: str,
                url: str, force: bool, dry_run: bool) -> None:
    results = collect_results(report)
    if not results:
        print("[report] 该报告没有逐例数据（旧格式），无从同步")
        return

    try:
        ds = client.datasets.get_dataset(dataset=name, timeout=HTTP_TIMEOUT)
    except ValueError:
        # --report 单独跑时数据集可能还没建。这里不能抛栈：观测是旁路，
        # 缺前提只该给出下一步怎么做。
        print(f"[report] Phoenix 里没有数据集 {name}，先跑一次 `--dataset` 再同步报告")
        return
    by_case = {}
    for e in ds:
        cid = (_attr(e, "metadata") or {}).get("case_id")
        if cid:
            by_case[cid] = _attr(e, "node_id")

    matched = sorted(set(results) & set(by_case))
    missing = sorted(set(results) - set(by_case))
    print(f"[report] {report_path.name}：可匹配 {len(matched)} 例"
          f"，报告里有但评估集没有 {len(missing)} 例")
    if missing:
        print("         未匹配（可能已从用例集移除）：", ", ".join(missing[:5]),
              "..." if len(missing) > 5 else "")

    at = report.get("at") or ""
    try:
        start = datetime.strptime(at, "%Y-%m-%d %H:%M:%S")
    except ValueError:
        start = datetime.fromtimestamp(report_path.stat().st_mtime)
    end = datetime.fromtimestamp(report_path.stat().st_mtime)
    if end < start:
        end = start

    base = f"eval-{start:%Y%m%d-%H%M%S}-{(report.get('git') or 'nogit')[:7]}"
    dataset_id = _attr(ds, "id")
    exp_name, seq = base, 1
    while _experiment_exists(client, dataset_id, exp_name):
        if not force:
            print(f"[experiment] {exp_name} 已存在，跳过（--force 可另建一份）")
            return
        # 用序号而不是往名字后面累加：连按三次 --force 会得到 -r2-r2-r2
        seq += 1
        exp_name = f"{base}-r{seq}"
    print(f"[experiment] 新建 {exp_name}（{len(matched)} 条 run）")
    if dry_run:
        sample = matched[0] if matched else None
        if sample:
            print("         样例 evaluation:", json.dumps(
                results[sample]["evaluations"][:2], ensure_ascii=False)[:220])
        return

    six = report.get("six_metrics") or {}
    exp = client.experiments.create(
        dataset_id=dataset_id,
        experiment_name=exp_name,
        experiment_description=f"评估报告 {report_path.name}，git {report.get('git')}",
        experiment_metadata={"git": report.get("git"), "at": at,
                             "report": report_path.name,
                             "faithfulness_micro": (six.get("faithfulness") or {}).get("micro")},
        timeout=HTTP_TIMEOUT,
    )
    exp_id = _attr(exp, "id")

    runs = evals = 0
    for cid in matched:
        payload = results[cid]
        run = client.experiments.log_run(
            experiment_id=exp_id, dataset_example_id=by_case[cid],
            output=payload["output"], start_time=start, end_time=end,
            timeout=HTTP_TIMEOUT,
        )
        runs += 1
        for ev in payload["evaluations"]:
            client.experiments.log_evaluation(
                experiment_run_id=_attr(run, "id"), name=ev["name"],
                annotator_kind=ev["kind"], score=ev["score"],
                label=ev["label"], explanation=ev["explanation"],
                start_time=start, end_time=end, timeout=HTTP_TIMEOUT,
            )
            evals += 1
    print(f"[experiment] 完成：{runs} 条 run / {evals} 条 evaluation")
    print(f"[url] {url}/datasets/{_attr(ds, 'id')}/experiments")


# ─────────────────────── 入口 ───────────────────────

def main() -> int:
    ap = argparse.ArgumentParser(description="同步评估集与评估结果到 Phoenix")
    ap.add_argument("--dataset", action="store_true", help="同步用例集到 Datasets")
    ap.add_argument("--report", nargs="?", const="latest", metavar="PATH",
                    help="把一次评估报告同步为 Experiment（缺省取最新）")
    ap.add_argument("--all", action="store_true", help="数据集 + 最新报告")
    ap.add_argument("--dry-run", action="store_true", help="只打印计划，不写入")
    ap.add_argument("--force", action="store_true", help="同名 experiment 已存在时另建")
    ap.add_argument("--strict", action="store_true", help="连接失败时退出码 1")
    ap.add_argument("--url", default=DEFAULT_URL)
    ap.add_argument("--dataset-name", default=DEFAULT_DATASET)
    args = ap.parse_args()

    if not (args.dataset or args.report or args.all):
        ap.print_help()
        return 0

    os.environ.setdefault("NO_PROXY", "*")   # WSL 里有 127.0.0.1:7890 代理，别让它拦本机请求
    os.environ.setdefault("no_proxy", "*")

    try:
        from phoenix.client import Client
    except ImportError:
        print("缺少 phoenix 客户端。用独立环境运行：\n"
              "  ~/.local/share/ecom-agent/phoenix-venv/bin/python evals/sync_phoenix.py --all",
              file=sys.stderr)
        return 1 if args.strict else 0

    # Client 不接受全局 timeout，改在各调用点显式传 HTTP_TIMEOUT
    client = Client(base_url=args.url)
    try:
        client.datasets.list(timeout=HTTP_TIMEOUT)
    except Exception as exc:
        print(f"⚠ Phoenix 不可用（{args.url}）：{exc}", file=sys.stderr)
        print("  评估产物不受影响，本次跳过同步。", file=sys.stderr)
        return 1 if args.strict else 0

    try:
        if args.dataset or args.all:
            sync_dataset(client, load_cases(), args.dataset_name, args.dry_run)

        if args.report or args.all:
            path = latest_report() if args.report in (None, "latest") else Path(args.report)
            if not path.is_file():
                sys.exit(f"报告不存在：{path}")
            sync_report(client, path, json.loads(path.read_text(encoding="utf-8")),
                        args.dataset_name, args.url, args.force, args.dry_run)
    except Exception as exc:
        # 中途断网/服务重启会从这里出去。打一行摘要而不是整页栈：观测失败时
        # 读者最需要知道的是"评估没坏"，栈只会让人以为评估炸了。
        print(f"⚠ 同步中断：{type(exc).__name__}: {exc}", file=sys.stderr)
        print("  评估报告本身不受影响，修好 Phoenix 后重跑本命令即可（幂等）。",
              file=sys.stderr)
        return 1 if args.strict else 0

    if args.dry_run:
        print("\n（--dry-run：以上均未写入）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
