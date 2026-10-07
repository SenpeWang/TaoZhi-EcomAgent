"""结构化拒答门禁（Q12）：证据为空或置信度过低时不编造，输出文本+JSON 双形态拒答并落审计。"""

from .rejection import (  # noqa: F401
    HOTLINE,
    build_refusal,
    log_rejection,
    should_refuse,
    to_text,
)

__all__ = ["HOTLINE", "build_refusal", "log_rejection", "should_refuse", "to_text"]
