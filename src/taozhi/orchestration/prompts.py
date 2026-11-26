"""通用编排决策 Prompt 目录。"""
from __future__ import annotations

from taozhi.ai.prompting import PromptCatalog, PromptSpec

ORCHESTRATION_PROMPT_VERSION = "v1"

_CHOICE_SYSTEM = """你是智能体意图与决策路由引擎（Decision Primitive: Choice）。
请根据输入上下文，从候选项中做出强类型离散裁决。

【候选项集合】
{{options_desc}}

【决策要求】
1. {{select_mode}}；
2. 必须只输出严格的 JSON 对象，禁止输出任何代码块标记或额外字符；
3. 输出格式：
{"selected": ["opt_key"], "confidence": 0.95, "rationale": "简明决策依据", "distribution": {"opt_key": 0.85}}
"""

ORCHESTRATION_PROMPTS = PromptCatalog(
    [
        PromptSpec(
            id="orchestration.choice",
            owner="runtime",
            version=ORCHESTRATION_PROMPT_VERSION,
            purpose="约束型离散决策",
            model_tier="fast",
            system_template=_CHOICE_SYSTEM,
            user_template="上下文：{{context}}",
            output_schema="json:choice",
        )
    ]
)

__all__ = ["ORCHESTRATION_PROMPTS", "ORCHESTRATION_PROMPT_VERSION"]