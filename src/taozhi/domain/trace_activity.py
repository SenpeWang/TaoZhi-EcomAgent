"""Agent 运行时业务操作与 OpenInference 链路节点的对应关系。"""
from typing import Literal


SpanKind = Literal["CHAIN", "AGENT", "LLM", "TOOL", "RETRIEVER", "EMBEDDING", "RERANKER", "INTERNAL"]
ActivityCategory = SpanKind | Literal["CONTEXT", "GUARDRAIL"]

CHAIN_OPERATIONS = {
    "CONTEXT": {
        "pipeline.business_snapshot": "核对实时业务记录",
        "pipeline.prepare_evidence": "汇集授权上下文并选择答复路径",
        "context.recall_conversation": "读取会话历史证据",
        "context.prepare_specialist": "组装问答专家上下文",
        "context.prepare_coordinator": "组装协调员决策上下文",
        "context.prepare_subagent": "组装子智能体观测上下文",
        "context.prepare_report": "组装报告证据上下文",
        "context.trim_observation": "裁剪工具观测以供下轮使用",
    },
    "GUARDRAIL": {
        "research.authorize_tool": "检查工具调用权限",
        "pipeline.quality": "校验事实与引用",
    },
}
CHAIN_CATEGORY_BY_NAME = {
    name: category for category, operations in CHAIN_OPERATIONS.items() for name in operations
}


def activity_category(kind: str, name: str) -> str:
    """仅对已采集的具体操作细分业务类型，历史节点沿用其实际语义。"""
    return CHAIN_CATEGORY_BY_NAME.get(name, kind) if kind == "CHAIN" else kind
