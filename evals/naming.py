"""评测数据集命名登记表：大类、子类、用途与来源（唯一口径源）。

子类英文名必须对齐官方来源术语；无官方术语的显式标记 project_defined，
禁止伪造出处。RAG 与 Agent 各自独立命名空间。
"""
from __future__ import annotations

from typing import Dict, Tuple

TRACKS: Tuple[str, ...] = ("golden", "capability", "regression", "challenge", "heldout", "redteam")
TRACK_LABELS: Dict[str, str] = {
    "golden": "冻结参考集（合成参考解可复算）",
    "capability": "能力提升集（较难，允许低通过率）",
    "regression": "回归集（已修复行为，近 100% 通过）",
    "challenge": "挑战集（当前失败/不稳定，单列）",
    "heldout": "留出集（防过拟合，发布前复核）",
    "redteam": "安全红队集（注入/越权/泄漏/拒答）",
}

SOURCES: Dict[str, Tuple[str, str]] = {
    "anthropic_demystifying": (
        "Anthropic, Demystifying evals for AI agents",
        "https://www.anthropic.com/research/demystifying-evals-for-ai-agents",
    ),
    "anthropic_bloom": (
        "Anthropic, Introducing Bloom: Automated behavioral evals",
        "https://www.anthropic.com/research/bloom",
    ),
    "anthropic_ai_resistant": (
        "Anthropic, Designing AI resistant technical evaluations",
        "https://www.anthropic.com/research/AI-resistant-technical-evaluations",
    ),
    "anthropic_infra_noise": (
        "Anthropic, Infrastructure noise in agentic coding evals",
        "https://www.anthropic.com/research/infrastructure-noise",
    ),
    "anthropic_docs": (
        "Anthropic, Define success criteria and build evaluations",
        "https://platform.claude.com/docs/en/test-and-evaluate/develop-tests",
    ),
    "ragas": ("RAGAS, List of available metrics", "https://docs.ragas.io/en/stable/concepts/metrics/available_metrics/"),
    "ares": ("ARES: An Automated Evaluation Framework for RAG (NAACL 2024)", "https://aclanthology.org/2024.naacl-long.20/"),
    "ragbench": ("RAGBench / TRACe", "https://arxiv.org/abs/2407.11005"),
    "crag": ("CRAG: Comprehensive RAG Benchmark", "https://arxiv.org/abs/2406.04744"),
    "rgb": ("RGB: Benchmarking Large Language Models in RAG (AAAI 2024)", "https://arxiv.org/abs/2309.01431"),
    "longmemeval": ("LongMemEval (ICLR 2025)", "https://arxiv.org/abs/2410.10813"),
    "owasp_llm": ("OWASP Top 10 for LLM Applications", "https://genai.owasp.org/llm-top-10/"),
    "nist_rbac": ("NIST RBAC standard", "https://csrc.nist.gov/projects/role-based-access-control"),
    "spider": ("Spider: Yale Semantic Parsing and Text-to-SQL", "https://yale-lily.github.io/spider"),
    "bird": ("BIRD: A Large-Scale Database for Text-to-SQL", "https://bird-bench.github.io/"),
    "deepeval": ("DeepEval, LLM evaluation metrics", "https://deepeval.com/docs/metrics-introduction"),
    "bfcl": ("Berkeley Function Calling Leaderboard (BFCL)", "https://gorilla.cs.berkeley.edu/leaderboard.html"),
    "agentbench": ("AgentBench", "https://arxiv.org/abs/2308.03688"),
    "tau_bench": ("τ-bench", "https://arxiv.org/abs/2406.12045"),
    "agentharm": ("AgentHarm", "https://arxiv.org/abs/2410.09024"),
    "gsm8k": ("GSM8K: Training Verifiers to Solve Math Word Problems", "https://arxiv.org/abs/2110.14168"),
    "knowledge_conflict": ("Knowledge Conflicts for LLMs: A Survey",
                           "https://arxiv.org/abs/2403.08319"),
    "clarification_questions": (
        "Learning to Ask Good Questions: Ranking Clarification Questions using NEVI",
        "https://arxiv.org/abs/1805.04655",
    ),
    "project_internal": ("本项目判定器与 BENCHMARK 口径",
                         "../../../BENCHMARK.md"),
}

RAG_CAPABILITIES: Tuple[str, ...] = (
    "retrieval",
    "generation_quality",
    "abstention_and_boundary",
    "multi_turn_and_long_context",
    "security_and_authorization",
    "business_task_completion",
)

RAG_SUBCATEGORIES: Dict[str, Dict[str, Tuple[str, str, str]]] = {
    "retrieval": {
        "context_recall": ("ragas", "adopted", "product/compatibility 检索部分"),
        "context_precision": ("ragas", "adopted", "检索排序"),
        "context_relevance": ("ares", "adopted", "检索相关性"),
        "noise_robustness": ("rgb", "adopted", "robustness"),
        "information_integration": ("rgb", "adopted", "multi_document 合并"),
    },
    "generation_quality": {
        "faithfulness": ("ragas", "adopted", "有据率"),
        "response_relevancy": ("ragas", "adopted", "answer_relevancy"),
        "factual_correctness": ("ragas", "adopted", "calculation/after_sales"),
        "adherence": ("ragbench", "adopted", "citation 支撑"),
        "completeness": ("ragbench", "adopted", "覆盖度"),
        "utilization": ("ragbench", "adopted", "召回未用"),
    },
    "abstention_and_boundary": {
        "negative_rejection": ("rgb", "adopted", "insufficient_evidence"),
        "counterfactual_robustness": ("rgb", "adopted", "conflict/edge"),
        "false_premise": ("crag", "adopted", "conflict/edge"),
        "abstention": ("longmemeval", "adopted", "资料不足拒答"),
        "knowledge_conflict": ("knowledge_conflict", "adapted", "conflict"),
        "out_of_scope": ("project_internal", "project_defined", "exclusion"),
    },
    "multi_turn_and_long_context": {
        "multi_turn": ("bfcl", "adopted", "multi_turn"),
        "multi_session_reasoning": ("longmemeval", "adopted", "multi_turn 跨会话"),
        "temporal_reasoning": ("longmemeval", "adopted", "timeliness"),
        "knowledge_updates": ("longmemeval", "adopted", "政策版本更新"),
        "information_extraction": ("longmemeval", "adopted", "long_context"),
    },
    "security_and_authorization": {
        "prompt_injection": ("owasp_llm", "adopted", "injection"),
        "sensitive_information_disclosure": ("owasp_llm", "adopted", "leakage"),
        "system_prompt_leakage": ("owasp_llm", "adopted", "新增"),
        "excessive_agency": ("owasp_llm", "adopted", "越权动作"),
        "role_based_access_control": ("nist_rbac", "adopted", "role"),
        "revocation": ("nist_rbac", "adopted", "revocation"),
    },
    "business_task_completion": {
        "execution_accuracy": ("spider", "adopted", "chatbi"),
        "task_completion": ("deepeval", "adopted", "流程完成"),
        "policy_compliance": ("project_internal", "project_defined", "after_sales/operations"),
        "constraint_satisfaction": ("project_internal", "project_defined", "calculation 门槛"),
    },
}

AGENT_CAPABILITIES: Tuple[str, ...] = (
    "task_completion",
    "tool_use",
    "planning_and_adaptation",
    "security_and_governance",
    "interaction_and_memory",
    "efficiency_and_reliability",
)

AGENT_SUBCATEGORIES: Dict[str, Dict[str, Tuple[str, str, str]]] = {
    "task_completion": {
        "task_completion": ("deepeval", "adopted", "task_completion"),
        "groundedness": ("ragas", "adopted", "evidence_attribution"),
        "hallucination": ("bfcl", "adopted", "hallucination_control"),
        "plan_quality": ("deepeval", "adopted", "报告完整性"),
    },
    "tool_use": {
        "tool_correctness": ("deepeval", "adopted", "tool_selection"),
        "argument_correctness": ("deepeval", "adopted", "argument_correctness"),
        "tool_call_accuracy": ("ragas", "adopted", "工具选择准确率"),
        "tool_call_f1": ("ragas", "adopted", "工具调用 F1"),
        "parallel_tool_calls": ("bfcl", "adopted", "并行调用"),
    },
    "planning_and_adaptation": {
        "plan_adherence": ("deepeval", "adopted", "dependency_ordering"),
        "step_efficiency": ("deepeval", "adopted", "cost_efficiency 步数部分"),
        "error_recovery": ("project_internal", "project_defined", "error_recovery"),
        "mid_trajectory_steering": ("project_internal", "project_defined", "dynamic_steering"),
    },
    "security_and_governance": {
        "prompt_injection": ("owasp_llm", "adopted", "prompt_injection"),
        "sensitive_information_disclosure": ("owasp_llm", "adopted", "数据外泄"),
        "system_prompt_leakage": ("owasp_llm", "adopted", "新增"),
        "excessive_agency": ("owasp_llm", "adopted", "access_control"),
        "harmful_task_refusal": ("agentharm", "adopted", "refusal"),
    },
    "interaction_and_memory": {
        "multi_turn": ("bfcl", "adopted", "multi_turn"),
        "memory": ("bfcl", "adopted", "跨会话记忆"),
        "information_extraction": ("longmemeval", "adopted", "记忆抽取"),
        "multi_session_reasoning": ("longmemeval", "adopted", "跨会话推理"),
        "knowledge_updates": ("longmemeval", "adopted", "状态更新"),
        "abstention": ("longmemeval", "adopted", "empty_result/拒答"),
        "irrelevance": ("bfcl", "adopted", "不相关工具/不调用"),
        "clarification": ("clarification_questions", "adapted", "ambiguity_clarification"),
    },
    "efficiency_and_reliability": {
        "latency": ("bfcl", "adopted", "时延"),
        "cost": ("bfcl", "adopted", "成本"),
        "numerical_reasoning": ("gsm8k", "adopted", "numerical_reasoning"),
        "temporal_reasoning": ("longmemeval", "adopted", "temporal_reasoning"),
        "consistency": ("tau_bench", "adapted", "pass^k"),
    },
}


def rag_subcategory_to_capability() -> Dict[str, str]:
    """返回 RAG 子类到大类的反查表。"""
    return {sub: cap for cap, subs in RAG_SUBCATEGORIES.items() for sub in subs}


def agent_subcategory_to_capability() -> Dict[str, str]:
    """返回 Agent 子类到大类的反查表。"""
    return {sub: cap for cap, subs in AGENT_SUBCATEGORIES.items() for sub in subs}


def validate_registry() -> None:
    """自检登记表：来源存在、状态合法、子类唯一、大类非空。"""
    valid_states = {"adopted", "adapted", "project_defined"}
    for label, caps, subs in (("RAG", RAG_CAPABILITIES, RAG_SUBCATEGORIES),
                              ("Agent", AGENT_CAPABILITIES, AGENT_SUBCATEGORIES)):
        assert set(subs) == set(caps), f"{label} 大类与子类表不一致"
        seen = set()
        for capability, entries in subs.items():
            assert entries, f"{label}/{capability} 没有子类"
            for sub, (source, state, v2_name) in entries.items():
                assert source in SOURCES, f"{label}/{sub} 来源未登记：{source}"
                assert state in valid_states, f"{label}/{sub} 状态非法：{state}"
                assert sub not in seen, f"{label} 子类重复：{sub}"
                seen.add(sub)


validate_registry()