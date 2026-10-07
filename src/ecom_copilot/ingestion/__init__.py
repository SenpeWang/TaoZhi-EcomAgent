"""数据采集层（PRD 模块 A）。"""

from .base import FetchRequest, SourceAdapter  # noqa: F401
from .guard import (  # noqa: F401
    assert_tool_allowed,
    allowed_tools,
    detect_injection,
    is_safe,
    sanitize,
)
from .sources import (  # noqa: F401
    LocalCorpusAdapter,
    ManualSourceAdapter,
    PolicyNoticeAdapter,
    ProductPageAdapter,
    ReviewFeedAdapter,
    SyntheticCorpusAdapter,
    adapters_by_name,
    default_adapters,
)

__all__ = [
    "FetchRequest", "SourceAdapter",
    "assert_tool_allowed", "allowed_tools", "detect_injection", "is_safe", "sanitize",
    "ProductPageAdapter", "ReviewFeedAdapter", "PolicyNoticeAdapter",
    "ManualSourceAdapter", "LocalCorpusAdapter", "SyntheticCorpusAdapter",
    "adapters_by_name", "default_adapters",
]
