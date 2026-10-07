"""spawn_agent / wait_agent 原语（继承自根因诊断 Agent）。

命中多个独立商品资料源时，Supervisor 自动 spawn 只读子 Agent 分头取证再合并；
子 Agent 工具白名单由 `ingestion.guard.assert_tool_allowed` 收紧。
"""

from __future__ import annotations

import time
import uuid
from concurrent.futures import ThreadPoolExecutor, wait
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Sequence

from ..ingestion.base import FetchRequest, SourceAdapter
from ..ingestion.guard import assert_tool_allowed, detect_injection
from ..llm import ModelTier, get_llm
from ..observability import inc, log_event, observe
from ..schemas.common import SourceDocument


@dataclass
class SubAgentSpec:
    """只读取证子 Agent 描述。"""

    name: str
    query: str
    adapter: SourceAdapter
    tools: List[str] = field(default_factory=lambda: ["fetch_source"])
    limit: int = 6
    summarize: bool = True
    permission: Dict[str, Any] = field(default_factory=dict)


@dataclass
class SubAgentHandle:
    id: str
    spec: SubAgentSpec
    started_at: float = field(default_factory=time.time)
    status: str = "running"
    documents: List[SourceDocument] = field(default_factory=list)
    summary: str = ""
    error: str = ""
    elapsed_s: float = 0.0


class SubAgentPool:
    """并发 spawn / 统一 wait。"""

    def __init__(self, max_parallel: int = 4) -> None:
        self.max_parallel = max_parallel
        self.handles: List[SubAgentHandle] = []

    def spawn(self, spec: SubAgentSpec) -> SubAgentHandle:
        # 白名单校验：子 Agent 只能持有只读工具
        for tool in spec.tools:
            assert_tool_allowed(tool)
        handle = SubAgentHandle(id=f"sub_{uuid.uuid4().hex[:10]}", spec=spec)
        self.handles.append(handle)
        return handle

    def run(self, handles: Optional[Sequence[SubAgentHandle]] = None,
            timeout: float = 180.0) -> List[SubAgentHandle]:
        targets = list(handles or self.handles)
        if not targets:
            return []
        import copy
        workers = min(self.max_parallel, len(targets))
        pool = ThreadPoolExecutor(max_workers=workers)
        # Workers mutate private handles; late results cannot mutate published state.
        futures = {pool.submit(self._execute, copy.copy(h)): h for h in targets}
        done, pending = wait(futures, timeout=timeout)
        for future, handle in futures.items():
            if future in done:
                try:
                    result = future.result()
                    handle.status, handle.error = result.status, result.error
                    handle.documents, handle.summary = result.documents, result.summary
                    handle.elapsed_s = result.elapsed_s
                except Exception:
                    handle.status, handle.error = "failed", "source_request_failed"
            else:
                future.cancel()
                handle.status, handle.error = "timeout", "source_deadline_exceeded"
        pool.shutdown(wait=False, cancel_futures=True)
        return targets

    def wait(self, handles: Optional[Sequence[SubAgentHandle]] = None,
             timeout: float = 180.0) -> List[SubAgentHandle]:
        """wait_agent：等待全部子 Agent 结束并合并。"""
        return self.run(handles, timeout)

    def _execute(self, handle: SubAgentHandle) -> SubAgentHandle:
        started = time.perf_counter()
        spec = handle.spec
        try:
            # 输入安全校验
            suspicious, hits = detect_injection(spec.query)
            if suspicious:
                handle.error = f"输入命中注入检测：{hits}"
                handle.status = "blocked"
                inc("subagent.blocked")
                log_event("subagent_blocked", query=spec.query[:60], hits=hits)
                return handle

            request = FetchRequest(
                keywords=[spec.query],
                limit=spec.limit,
                permission=spec.permission,
            )
            docs = spec.adapter.safe_fetch(request)
            handle.documents = docs
            if spec.summarize and docs:
                handle.summary = self._summarize(spec.query, docs)
            handle.status = "done" if docs else "empty"
        except Exception as exc:  # noqa: BLE001
            handle.status = "failed"
            handle.error = "source_request_failed"
            inc("subagent.failed")
        finally:
            handle.elapsed_s = round(time.perf_counter() - started, 3)
            observe("subagent.latency", handle.elapsed_s)
            inc("subagent.spawn")
        return handle

    @staticmethod
    def _summarize(query: str, docs: Sequence[SourceDocument]) -> str:
        if not get_llm().settings.has_llm:
            titles = "；".join(d.title for d in docs[:5])
            return f"围绕「{query}」检索到 {len(docs)} 篇资料：{titles}"
        context = "\n".join(f"- {d.title}：{(d.raw_text or '')[:260]}" for d in docs[:6])
        prompt = (
            f"用户问题：{query}\n\n资料列表：\n{context}\n\n"
            "请用 3 句话总结这些商品资料对该问题的共同口径与分歧点，输出纯文本。"
        )
        try:
            return get_llm().chat(prompt, tier=ModelTier.FAST, max_tokens=600)
        except Exception:  # noqa: BLE001
            return f"检索到 {len(docs)} 篇资料：" + "；".join(d.title for d in docs[:5])

    def merge(self, handles: Sequence[SubAgentHandle]) -> List[SourceDocument]:
        """合并子 Agent 取证结果并去重。"""
        merged: List[SourceDocument] = []
        seen = set()
        for handle in handles:
            for doc in handle.documents:
                key = (doc.title, (doc.raw_text or "")[:80])
                if key in seen:
                    continue
                seen.add(key)
                merged.append(doc)
        return merged

    def report(self, handles: Sequence[SubAgentHandle]) -> List[Dict[str, Any]]:
        return [
            {
                "id": h.id,
                "spec": h.spec.name,
                "adapter": h.spec.adapter.name,
                "status": h.status,
                "docs": len(h.documents),
                "elapsed_s": h.elapsed_s,
                "summary": h.summary[:400],
                "error": h.error,
            }
            for h in handles
        ]


def spawn_agent(pool: SubAgentPool, spec: SubAgentSpec) -> SubAgentHandle:
    return pool.spawn(spec)


def wait_agent(pool: SubAgentPool, handles: Sequence[SubAgentHandle],
               timeout: float = 180.0) -> List[SourceDocument]:
    pool.wait(handles, timeout)
    docs = pool.merge(handles)
    inc("subagent.merged_docs", len(docs))
    return docs
