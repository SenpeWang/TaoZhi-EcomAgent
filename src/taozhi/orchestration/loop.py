"""编排层 5 节点迭代闭环状态机：决定 -> 检索 -> 评估 -> 改写 -> 回答。"""
from __future__ import annotations

import logging
import time
from typing import Any, Dict, List, Optional

from ..agent.tools.base import ToolContext
from ..agent.tools.retrieval_tool import KnowledgeRetrievalTool
from ..agent.tools.web_search_tool import WebSearchFallbackTool
from ..config import get_settings
from ..observability.tracer import get_current_trace
from ..rag.retrieval.context_builder import ContextBuilder
from ..schemas.rag import CitationItem, RetrievalCandidate
from .controller import DeterministicController
from .router import QueryRoute, QueryRouter
from .state import EvaluationResult, OrchestrationState

logger = logging.getLogger(__name__)


class AgenticRAGOrchestrator:
    """显式 5 节点状态机驱动的 Agentic RAG 编排引擎。"""

    def __init__(
        self,
        retrieval_tool: Optional[KnowledgeRetrievalTool] = None,
        web_search_tool: Optional[WebSearchFallbackTool] = None,
        router: Optional[QueryRouter] = None,
        controller: Optional[DeterministicController] = None,
        context_builder: Optional[ContextBuilder] = None,
    ) -> None:
        self.retrieval_tool = retrieval_tool or KnowledgeRetrievalTool()
        self.web_search_tool = web_search_tool or WebSearchFallbackTool()
        self.router = router or QueryRouter()
        self.controller = controller or DeterministicController(max_iterations=3)
        self.context_builder = context_builder or ContextBuilder()

    def run(self, state: OrchestrationState) -> OrchestrationState:
        """运行 5 节点状态机主驱动循环。由确定性计数器严格受控。"""
        start_time = time.time()

        while state.next_node != "end":
            # 检查硬超时
            ok, time_reason = self.controller.check_timeout(start_time)
            if not ok:
                state.is_fallback = True
                state.answer = "任务编排处理超时，已终止后续探索。"
                state.next_node = "end"
                break

            current_node = state.next_node
            if current_node == "decide":
                self._node_decide(state)
            elif current_node == "retrieve":
                self._node_retrieve(state)
            elif current_node == "evaluate":
                self._node_evaluate(state)
            elif current_node == "rewrite":
                self._node_rewrite(state)
            elif current_node == "answer":
                self._node_answer(state)
            else:
                state.next_node = "end"

        return state

    def _node_decide(self, state: OrchestrationState) -> None:
        """节点 1：决定 (Decide) - 路由与策略规划。"""
        trace = get_current_trace()
        span_ctx = trace.span("orchestration.node.decide", kind="agent") if trace else None

        route = self.router.route(state.current_query)
        if span_ctx:
            with span_ctx as s:
                s.output_data = {"route": route.value}

        if route == QueryRoute.DIRECT_ANSWER:
            state.answer = "您好！我是淘智电商协同助手，请问有什么可以帮您？"
            state.next_node = "end"
        elif route == QueryRoute.EXTERNAL_SEARCH:
            # 直接走外部搜索
            state.next_node = "retrieve_external"
            self._node_retrieve_external(state)
        else:
            state.next_node = "retrieve"

    def _node_retrieve(self, state: OrchestrationState) -> None:
        """节点 2：检索 (Retrieve) - 调用受控 RAG 检索工具。"""
        tool_ctx = ToolContext(
            tenant_id=state.tenant_id,
            user_id=state.user_id,
            security_level=state.security_level,
        )
        res = self.retrieval_tool(
            {"query": state.current_query, "top_k": 5},
            context=tool_ctx,
        )

        candidates: List[RetrievalCandidate] = []
        if res.success and isinstance(res.output, dict):
            raw_cands = res.output.get("candidates", [])
            for c in raw_cands:
                candidates.append(RetrievalCandidate(**c))

        state.candidates = candidates
        state.next_node = "evaluate"

    def _node_retrieve_external(self, state: OrchestrationState) -> None:
        """外部搜索兜底分支。"""
        tool_ctx = ToolContext(
            tenant_id=state.tenant_id,
            user_id=state.user_id,
            security_level=0,
        )
        res = self.web_search_tool({"query": state.current_query, "limit": 4}, context=tool_ctx)
        candidates: List[RetrievalCandidate] = []
        if res.success and isinstance(res.output, dict):
            raw_cands = res.output.get("candidates", [])
            for c in raw_cands:
                candidates.append(RetrievalCandidate(**c))

        state.candidates = candidates
        state.is_fallback = True
        state.next_node = "answer"

    def _node_evaluate(self, state: OrchestrationState) -> None:
        """节点 3：评估 (Evaluate) - 判断切片相关性与充分性。"""
        trace = get_current_trace()
        span_ctx = trace.span("orchestration.node.evaluate", kind="agent") if trace else None

        candidates = state.candidates
        if not candidates:
            eval_res = EvaluationResult(
                is_sufficient=False,
                score=0.0,
                reason="未检索到任何切片候选",
            )
        else:
            top_score = max(c.score for c in candidates)
            # 基础相关性阈值判断
            if top_score >= 0.05 or any(len(c.chunk.content) > 20 for c in candidates):
                eval_res = EvaluationResult(
                    is_sufficient=True,
                    score=top_score,
                    reason="切片相关性与内容密度满足生成条件",
                )
            else:
                eval_res = EvaluationResult(
                    is_sufficient=False,
                    score=top_score,
                    reason="候选切片相关性偏弱",
                )

        state.evaluation = eval_res
        if span_ctx:
            with span_ctx as s:
                s.output_data = eval_res.model_dump()

        if eval_res.is_sufficient:
            state.next_node = "answer"
        else:
            # 检查迭代预算
            can_iterate, _ = self.controller.check_iteration_budget(state)
            if can_iterate:
                state.next_node = "rewrite"
            else:
                # 预算耗尽，触发外部搜索兜底
                self._node_retrieve_external(state)

    def _node_rewrite(self, state: OrchestrationState) -> None:
        """节点 4：改写 (Rewrite) - 提炼核心意图并防死锁熔断。"""
        trace = get_current_trace()
        span_ctx = trace.span("orchestration.node.rewrite", kind="agent") if trace else None

        # 确定性核心词提取与意图提炼改写
        from ..core.tools.search_web import extract_search_keyword
        extracted = extract_search_keyword(state.current_query)
        if extracted and extracted != state.current_query:
            rewritten = extracted
        else:
            # 剥离多余标点与停用虚词
            rewritten = state.current_query.replace("请问", "").replace("有没有", "").replace("？", "").strip()
            if rewritten == state.current_query:
                rewritten = f"{state.current_query} 规则 规范 说明"

        # 死锁检测
        is_deadlock = self.controller.detect_rewrite_deadlock(rewritten, state.rewrite_history)
        if span_ctx:
            with span_ctx as s:
                s.output_data = {
                    "original": state.current_query,
                    "rewritten": rewritten,
                    "deadlock_detected": is_deadlock,
                    "iteration": state.iteration_count + 1,
                }

        if is_deadlock:
            # 死锁熔断：直接降级至外部搜索或兜底生成
            self._node_retrieve_external(state)
        else:
            state.rewrite_history.append(state.current_query)
            state.current_query = rewritten
            state.iteration_count += 1
            state.next_node = "retrieve"  # 循环回到检索节点！

    def _node_answer(self, state: OrchestrationState) -> None:
        """节点 5：回答 (Answer) - 严密引用生成与异常容灾。"""
        trace = get_current_trace()
        span_ctx = trace.span("orchestration.node.answer", kind="llm") if trace else None

        if not state.candidates:
            state.answer = "很抱歉，在您当前授权的企业知识库中未检索到相关可靠资料，无法为您提供确凿解答。"
            state.is_fallback = True
            state.next_node = "end"
            return

        context_text, citations = self.context_builder.build_context(state.candidates)
        settings = get_settings()

        prompt = (
            "你是由淘智驱动的企业级电商智能专家助手。请严格基于以下提供的参考资料回答问题。\n"
            "【严格约束】：\n"
            "1. 绝不凭空推测或编造事实；\n"
            "2. 每一个核心事实句后必须显式标注其对应的来源资料锚点，格式为 [doc_id:chunk_id]；\n"
            "3. 若资料中未明确提及某细节，请直白告知资料未记录。\n\n"
            f"{context_text}\n\n"
            f"用户问题: {state.original_query}\n"
            "请给出专业、严谨且带引用锚点的回答:"
        )

        answer = ""
        if settings.has_llm:
            from ..ai.client import get_llm
            client = get_llm()
            try:
                answer = client.chat(prompt, temperature=0.2)
                if trace:
                    trace.record_llm_call(tokens=len(prompt) // 2 + len(answer) // 2)
            except Exception as exc:
                logger.warning("上游 LLM 调用异常，自动安全保底降级: %s", exc)
                answer = ""

        if not answer:
            top_cand = state.candidates[0].chunk
            anchor = f"[{top_cand.doc_id}:{top_cand.chunk_id}]"
            answer = f"根据《{top_cand.doc_title}》记载 {anchor}：{top_cand.content[:150]}..."

        state.answer = answer
        state.citations = self.context_builder.filter_citations_used_in_text(answer, citations)
        if span_ctx:
            with span_ctx as s:
                s.output_data = {"citations_count": len(state.citations)}
        state.next_node = "end"