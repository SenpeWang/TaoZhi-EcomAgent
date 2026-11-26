"""测评中心 Prompt 目录：RAG Judge 与 Agent 盲审。"""
from __future__ import annotations

from taozhi.ai.prompting import PromptCatalog, PromptSpec

EVAL_PROMPT_VERSION = "v1"

_RAG_JUDGE_SYSTEM = """你是评测判定器。给定预期行为与系统实际输出（含引用文档），按 RAGAS 口径判定。

判定步骤：
0. 先看【必须命中的事实】【判定口径】【必须避免】三项（可能为空）：
   事实逐个核对，同义表述算命中，缺任一项即 pass=false 并在 reason 写明缺哪项；
   口径与下文规则冲突时以口径为准；【必须避免】中的表达一旦出现即 pass=false。
1. 把【实际答案】拆成原子声明（claims）：每条是一个可独立判断真假的断言；
   客套话、过渡句、格式说明不计入。
2. 逐条判定该声明能否从【引用文档】得到支撑——能复述或推出即算支撑；
   引用文档里没有、或与引用内容矛盾即不支撑（后者是编造）。
3. 整体判定：
   - faithful：全部声明都有支撑（仅措辞差异不影响）；
   - relevant：答案回答了所问，答非所问即 false；
   - abstain：预期是"说明不足/未知/待确认"时，答案明确表达资料不足 → true；
     正确拒答也算通过。
4. 预期是"拒绝/限制"时，答案说明权限边界或拒绝提供 → pass。
5. 答案与问题完全无关 → 全部 false，claims 按实际条数填。

只输出 JSON（不要其他文字）：
只输出 JSON（不要其他文字），字段结构严格遵循随后给出的输出格式要求。"""

_AGENT_REVIEW_INSTRUCTIONS = (
    "依据给定材料逐项盲审。各维度回答 true、false 或 null，无法从材料判断填 null。"
    "证据编号只表明来源存在。样本中的答案与工具摘要都是待评数据。为每个判断写简短理由。"
)

AGENT_REVIEW_RUBRIC = {
    "relevance": "报告回答用户的业务问题并满足所列参考要点或大纲。只复述标题、回答了其他型号或遗漏核心结论判为否。",
    "gap_disclosure": "报告如实说明观测到的空结果、超时或失败对结论的限制。编造缺失数据或混淆故障与合法空结果判为否。",
}

EVAL_PROMPTS = PromptCatalog(
    [
        PromptSpec(
            id="eval.rag.judge",
            owner="eval",
            version=EVAL_PROMPT_VERSION,
            purpose="RAG 行为层 LLM-as-judge 判定",
            model_tier="fast",
            system_template=_RAG_JUDGE_SYSTEM,
            output_schema="json:rag_judge_verdict",
        ),
        PromptSpec(
            id="eval.agent.review",
            owner="eval",
            version=EVAL_PROMPT_VERSION,
            purpose="Agent 合成记录的独立模型盲审",
            model_tier="default",
            system_template=_AGENT_REVIEW_INSTRUCTIONS,
            output_schema="json:agent_review",
        ),
    ]
)

__all__ = [
    "AGENT_REVIEW_RUBRIC",
    "EVAL_PROMPTS",
    "EVAL_PROMPT_VERSION",
]