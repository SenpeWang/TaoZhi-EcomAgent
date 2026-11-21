"""工作台时间顺序列表的分页边界。"""
import re
from datetime import datetime

from taozhi.domain.errors import Denied


def decode_time_cursor(cursor: str | None) -> tuple[datetime, str] | None:
    if cursor is None:
        return None
    timestamp, _, identifier = cursor.partition("|")
    try:
        created_at = datetime.fromisoformat(timestamp)
        if created_at.tzinfo is None or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", identifier):
            raise ValueError
    except (ValueError, OverflowError) as exc:
        raise Denied("分页游标无效", "INVALID_CURSOR", 422) from exc
    return created_at, identifier
