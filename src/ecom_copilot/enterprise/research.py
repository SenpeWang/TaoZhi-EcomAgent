"""平台调研多智能体协同系统（E-Commerce Multi-Agent Research System）。

自研电商多智能体协同中台核心能力：
1. 协同总指挥 (coordinator_agent) 规划调研蓝图与大纲；
2. 全网情报分析专员 (web_researcher_agent) 抓取互联网零售价格带与消费痛点；
3. 货源比价专员 (ecommerce_scanner_agent) 深入 1688 批发渠道挖掘一手工厂底价；
4. 真实性验真专员 (data_verifier_agent) 交叉比对供应链与零售端，测算毛利并识别夸大宣传；
5. 研报架构专家 (synthesis_reporter_agent) 整合各智能体交付物，生成 Markdown 终审研报；
6. 注释与接口遵循 Google Python Style 规范。
"""

from __future__ import annotations

import json
import time
from typing import Any, Callable, Dict, List, Optional

from ..llm.client import LLMClient, ModelTier
from .subagent.bus import MessageBus
from .subagent.context import SubagentContext
from .subagent.protocol import WorkspaceMode
from .subagent.runner import SubagentRunner

_LABEL = {
    "web": "全网实时搜索",
    "p1688": "1688 批发货源",
}


def _plan_outline(keyword: str, llm: LLMClient) -> List[str]:
    """生成电商市场调研的大纲章节。

    Args:
        keyword (str): 商品检索词。
        llm (LLMClient): 大语言模型客户端。

    Returns:
        List[str]: 大纲章节标题列表。
    """
    sys_prompt = (
        "你是一名电商商品调研总监。请针对给定的调研品类，规划 3-4 个最关键的调研维度。\n"
        "要求：\n"
        "1. 必须覆盖：市场概况、零售价格与消费者诉求、供应链工厂出厂成本、真实性与风险分析；\n"
        "2. 仅输出一个标准的 JSON 数组，例如：[\"市场概况与竞品格局\", \"供应链成本与批发价格带\", \"核心卖点与风险提示\"]；\n"
        "3. 严禁输出任何 markdown 标记、解释或无关废话。"
    )
    user_prompt = f"调研品类关键词：{keyword}"

    try:
        reply = llm.chat(
            user_prompt,
            tier=ModelTier.FAST,
            system=sys_prompt,
            max_tokens=256,
        )
        clean = reply.strip()
        if clean.startswith("```"):
            clean = clean.split("\n", 1)[1]
            if clean.endswith("```"):
                clean = clean.rsplit("```", 1)[0]
        data = json.loads(clean.strip())
        if isinstance(data, list) and data:
            return [str(x) for x in data[:5]]
    except Exception:
        pass

    return [
        "全网市场概况与热销卖点分析",
        "1688 一手货源与工厂出厂批发价格带",
        "零售溢价与供应链工艺交叉验真",
        "品类差异化突围与选品决策建议",
    ]


def run_research(
    keyword: str,
    platforms: Optional[List[str]] = None,
    emit: Optional[Callable[[str, int], None]] = None,
) -> Dict[str, Any]:
    """执行自研规范的多智能体协同电商全景调研。

    流水线包括：
    1. 全网实时情报抓取与舆情分析；
    2. 1688 工厂出厂批发底价与真实出货量排查；
    3. 供应链出厂价与零售端溢价交叉比对；
    4. 汇总为标准结构化研报交付物。

    Args:
        keyword (str): 电商调研关键词（如“磁吸支架手机壳”）。
        platforms (Optional[List[str]]): 目标平台过滤列表（可选）。
        emit (Optional[Callable[[str, int], None]]): 阶段与进度百分比上报回调。

    Returns:
        Dict[str, Any]: 结构化研报字典：
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
        role="调研协同总指挥",
        depth=0,
        workspace_mode=WorkspaceMode.INHERIT,
    )

    emit_fn("制定多智能体调研大纲与执行蓝图", 10)
    outline = _plan_outline(keyword, llm)

    # 2. 构造并发子智能体任务列表（统一采用 _agent 后缀）
    tasks: List[Dict[str, Any]] = [
        {
            "type_name": "web_researcher_agent",
            "role": "全网市场情报分析专员",
            "prompt": f"调研关键词：【{keyword}】。通过全网实时搜索，归纳当前电商市场主流品牌热销卖点、消费者痛点及全网零售价格带。",
            "model_tier": "fast",
        },
        {
            "type_name": "ecommerce_scanner_agent",
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
        p_name = "web" if "web" in res.type_name else "p1688"
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
        type_name="data_verifier_agent",
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
        type_name="synthesis_reporter_agent",
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
