"""数据采集抽象与通用抓取能力（PRD 模块 A）。"""

from __future__ import annotations

import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

import httpx

from ..config import Settings, get_settings
from ..schemas.common import DocType, SourceDocument


@dataclass
class FetchRequest:
    """一次采集请求。"""

    keywords: List[str] = field(default_factory=list)
    limit: int = 10
    product_line: str = ""
    brand: str = ""
    category: str = ""
    permission: Dict[str, Any] = field(default_factory=dict)

    @property
    def primary_query(self) -> str:
        if self.keywords:
            return self.keywords[0]
        if self.category:
            return self.category
        if self.product_line:
            return self.product_line
        return self.brand


class SourceAdapter(ABC):
    """数据源适配器基类。"""

    name: str = "base"
    label: str = "基础数据源"
    doc_type: DocType = DocType.OTHER
    priority: str = "P1"
    enabled: bool = True
    read_only: bool = True     # spawn 子 Agent 只能调用只读适配器

    def __init__(self, settings: Optional[Settings] = None) -> None:
        self.settings = settings or get_settings()

    # ───── HTTP ─────
    def _get(self, url: str, **kwargs) -> Optional[httpx.Response]:
        timeout = kwargs.pop("timeout", self.settings.request_timeout)
        headers = {"User-Agent": self.settings.user_agent}
        headers.update(kwargs.pop("headers", {}))
        try:
            with httpx.Client(follow_redirects=True, timeout=timeout, verify=False) as client:
                return client.get(url, headers=headers, **kwargs)
        except Exception:  # noqa: BLE001
            return None

    def _post(self, url: str, **kwargs) -> Optional[httpx.Response]:
        timeout = kwargs.pop("timeout", self.settings.request_timeout)
        headers = {"User-Agent": self.settings.user_agent}
        headers.update(kwargs.pop("headers", {}))
        try:
            with httpx.Client(follow_redirects=True, timeout=timeout, verify=False) as client:
                return client.post(url, headers=headers, **kwargs)
        except Exception:  # noqa: BLE001
            return None

    # ───── 采集 ─────
    @abstractmethod
    def fetch(self, request: FetchRequest) -> List[SourceDocument]:
        ...

    def safe_fetch(self, request: FetchRequest) -> List[SourceDocument]:
        """带异常隔离的采集入口，任何数据源异常都不影响整体流程。"""
        started = time.perf_counter()
        try:
            docs = self.fetch(request)
        except Exception as exc:  # noqa: BLE001
            from ..observability import inc, log_event

            inc(f"ingest.error.{self.name}")
            log_event("source_error", source=self.name, error=f"{type(exc).__name__}: {exc}")
            return []
        from ..observability import inc, observe

        inc(f"ingest.docs.{self.name}", len(docs))
        observe(f"ingest.latency.{self.name}", time.perf_counter() - started)
        for doc in docs:
            doc.source = doc.source or self.name
            doc.doc_type = doc.doc_type if doc.doc_type != DocType.OTHER else self.doc_type
            doc.org_tag = doc.org_tag or request.permission.get("org_tag", "")
            doc.owner_id = doc.owner_id or request.permission.get("user_id", "")
        return docs[: request.limit]

    def __repr__(self) -> str:  # pragma: no cover
        return f"<{self.__class__.__name__} name={self.name} enabled={self.enabled}>"
