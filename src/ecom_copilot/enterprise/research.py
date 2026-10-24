"""平台调研多智能体系统：基于 Antigravity (agy) 多智能体设计模式底座。

架构设计：
1. Coordinator（主协调智能体）：制定多维度调研大纲与执行蓝图；
2. Subagent 1 (web_researcher)：全网市场情报搜索，分析消费趋势、社媒痛点与零售价格带；
3. Subagent 2 (ecommerce_scanner)：1688 批发供应链挖掘，抓取一手出厂价、真实销量与供货源头；
4. Subagent 3 (data_verifier)：交叉核对零售宣传与工厂参数，剔除虚假虚高营销；
5. Synthesis Reporter（研报终审智能体）：基于分层隔离的数据交付物，综合产出专业 Markdown 电商调研报告。
"""

from __future__ import annotations

import os
import time
from typing import Any, Callable, Dict, List, Optional, Tuple

from ..llm.client import LLMClient, ModelTier
from .subagent import (
    MessageBus,
    SubagentContext,
    SubagentRunner,
    WorkspaceMode,
    default_registry,
)

BUILTIN_PLATFORMS: Tuple[Tuple[str, str], ...] = (
    ("web", "全网搜索引擎"),
    ("p1688", "1688批发货源"),
)
_LABEL = dict(BUILTIN_PLATFORMS)


def _endpoint(name: str) -> str:
    """获取指定平台的自定义抓取端点 URL。"""
    return os.environ.get(f"ECOM_RESEARCH_{name.upper()}_URL", "").strip()


def _api_key(name: str) -> str:
    """获取指定平台抓取端点的鉴权密钥。"""
    return os.environ.get(f"ECOM_RESEARCH_{name.upper()}_KEY", "").strip()


def platform_status() -> List[Dict[str, Any]]:
    """内置平台与接入状态（供管理端与前端界面展示）。

    Returns:
        List[Dict[str, Any]]: 平台配置状态列表。
    """
    return [
        {"name": n, "label": label, "configured": True if n in ("web", "p1688") else bool(_endpoint(n))}
        for n, label in BUILTIN_PLATFORMS
    ]


def _plan_outline(keyword: str, llm: LLMClient) -> List[str]:
    """生成调研多维度大纲。

    Args:
        keyword: 调研核心关键词。
        llm: LLMClient 实例。

    Returns:
        List[str]: 4-6 个调研维度文本。
    """
    try:
        raw = llm.chat(
            f"电商调研关键词：{keyword}\n给出 4-6 个调研维度，JSON 数组输出，每项一句话。",
            tier=ModelTier.FAST,
            max_tokens=400,
            system="只输出严格的 JSON 字符串数组，如 [\"维度1\", \"维度2\"]，禁止任何前后缀或解释。",
        )
        from langchain_core.output_parsers import JsonOutputParser

        arr = JsonOutputParser().parse(raw)
        if isinstance(arr, list) and arr:
            return [str(x)[:60] for x in arr[:6]]
    except Exception:
        pass
    return ["全网市场概览与主流品牌", "批发供应链价格带分布", "核心热销卖点与用户痛点", "产品差异化与商业机会点"]


def run_research(
    keyword: str,
    platforms: Optional[List[str]] = None,
    emit: Optional[Callable[[str, int], None]] = None,
) -> Dict[str, Any]:
    """执行一次基于 agy 规范的多智能体协同电商调研。

    调用主智能体拆解任务，并通过反应式总线并发唤醒多个专职子智能体分别执行：
    1. 全网实时情报抓取与舆情分析；
    2. 1688 工厂出厂批发底价与真实出货量排查；
    3. 供应链出厂价与零售端溢价交叉比对；
    4. 汇总为标准结构化研报交付物。

    Args:
        keyword (str): 电商调研关键词（如“磁吸支架手机壳”）。
        platforms (Optional[List[str]]): 目标平台过滤列表（可选）。
        emit (Optional[Callable[[str, int], None]]): 阶段与进度百分比上报回调。

    Returns:
        Dict[str, Any]: 兼容老接口并富含多智能体工件的结构化字典：
            - report: Markdown 调研报告正文；
            - outline: 调研维度大纲；
            - platforms: 各平台智能体发现与数据清单；
            - model_calls: 模型调用总次数；
            - elapsed: 运行总用时（秒）；
            - artifacts: 各子智能体生成的正式工件路径；
            - subagents: 各子智能体的运行状态摘要。
    """
    started = time.perf_counter()
    emit_fn = emit or (lambda stage, pct: None)
    llm = LLMClient()
    bus = MessageBus()
    runner = SubagentRunner(bus=bus, llm_client=llm)

    # 1. 建立根协调智能体上下文
    root_ctx = SubagentContext(
        conversation_id=f"research_root_{int(time.time())}",
        role="电商市场调研主官",
        depth=0,
        workspace_mode=WorkspaceMode.INHERIT,
    )

    emit_fn("制定多智能体调研大纲与执行蓝图", 10)
    outline = _plan_outline(keyword, llm)

    # 2. 构造并发子智能体任务列表
    tasks: List[Dict[str, Any]] = [
        {
            "type_name": "web_researcher",
            "role": "全网市场情报分析专员",
            "prompt": f"调研关键词：【{keyword}】。通过全网实时搜索，归纳当前电商市场主流品牌热销卖点、消费者痛点及全网零售价格带。",
            "model_tier": "fast",
        },
        {
            "type_name": "ecommerce_scanner",
            "role": "1688货源比价专员",
            "prompt": f"调研关键词：【{keyword}】。深入 1688 批发货源渠道，提取真实的工厂出厂价格分布、批发起订量及主力供应商分布。",
            "model_tier": "fast",
        },
    ]

    emit_fn("派出专职子智能体并发执行全网搜索与货源挖掘", 30)
    subagent_results = runner.run_parallel_subagents(
        tasks=tasks,
        parent_context=root_ctx,
        max_workers=2,
    )

    # 3. 抽取各智能体的结构化发现与数据清单
    platform_results: List[Dict[str, Any]] = []
    collected_artifacts: List[str] = []
    collected_data_summary: List[str] = []

    for res in subagent_results:
        collected_artifacts.extend(res.artifacts)
        p_name = "web" if res.type_name == "web_researcher" else "p1688"
        p_label = _LABEL.get(p_name, res.role)

        items = res.data[:20]
        state_str = "ok" if res.state.value == "completed" else "error"
        platform_results.append({
            "name": p_name,
            "label": p_label,
            "state": state_str,
            "state_label": "已完成" if state_str == "ok" else "执行失败",
            "error": res.error,
            "brief": res.summary,
            "items": items,
            "items_count": len(items),
            "subagent_id": res.subagent_id,
            "scratch_files": res.scratch_files,
        })
        collected_data_summary.append(f"### 【{res.role}】提交的调研发现：\n{res.summary}")

    emit_fn("启动数据验真智能体核实价格与虚假宣传", 60)
    # 派出第三个子智能体进行交叉比对
    verifier_prompt = (
        f"商品品类：【{keyword}】\n"
        f"前方智能体采集到的各方数据如下：\n" + "\n\n".join(collected_data_summary) + "\n\n"
        "请交叉核对：\n"
        "1. 零售端宣称的产品卖点与 1688 工厂实际制造工艺是否存在夸大或虚假宣传；\n"
        "2. 出厂批发价到终端零售价的毛利空间是否合理；\n"
        "3. 提炼出 3 条真实性与合规风险警示。"
    )
    verifier_res = runner.invoke_subagent(
        type_name="data_verifier",
        role="商品真实性验真专员",
        prompt=verifier_prompt,
        model_tier="fast",
        parent_context=root_ctx,
    )
    collected_artifacts.extend(verifier_res.artifacts)
    collected_data_summary.append(f"### 【{verifier_res.role}】真实性研判发现：\n{verifier_res.summary}")

    emit_fn("汇总多智能体成果，撰写专业市场研报", 80)
    # 4. 派发终审智能体撰写完整 Markdown 调研报告
    findings_block = "\n\n".join(collected_data_summary)
    synthesis_prompt = (
        f"调研关键词：【{keyword}】\n"
        f"调研维度大纲：{'；'.join(outline)}\n\n"
        f"各专职子智能体提交的原始成果：\n{findings_block}\n\n"
        "请综合以上多智能体调查事实，撰写一份结构化、数据详实且条理分明的电商市场调研报告。\n"
        "报告必须包含：\n"
        "1. 市场概览与竞争态势；\n"
        "2. 供应链出厂成本与零售价格带剖析；\n"
        "3. 用户高频痛点与热销卖点拆解；\n"
        "4. 产品差异化破局策略与选品操盘建议。\n"
        "【严格要求】：所有结论必须依据上面智能体发现，禁止无中生有编造虚假数字。"
    )

    synthesis_res = runner.invoke_subagent(
        type_name="synthesis_reporter",
        role="电商市场调研研报专家",
        prompt=synthesis_prompt,
        model_tier="strong",
        parent_context=root_ctx,
    )
    collected_artifacts.extend(synthesis_res.artifacts)
    final_report = synthesis_res.summary

    # 保存最终交付物到根目录 artifacts
    final_artifact_file = root_ctx.write_artifact("market_research_report.md", final_report)
    collected_artifacts.append(str(final_artifact_file))

    emit_fn("多智能体电商调研报告已就绪", 100)
    elapsed = round(time.perf_counter() - started, 1)

    return {
        "report": final_report,
        "outline": outline,
        "platforms": platform_results,
        "model_calls": len(subagent_results) + 2,
        "elapsed": elapsed,
        "artifacts": collected_artifacts,
        "subagents": [
            {
                "id": r.subagent_id,
                "role": r.role,
                "type": r.type_name,
                "state": r.state.value,
                "elapsed": r.elapsed_seconds,
            }
            for r in (subagent_results + [verifier_res, synthesis_res])
        ],
    }
