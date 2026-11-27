"""工作台时间顺序列表的分页边界。"""
import re
from datetime import datetime

from taozhi.domain.errors import Denied


def decode_time_cursor(cursor: str | None) -> tuple[datetime, str] | None:
    """解析「创建时间|标识」格式的分页游标。

    Args:
        cursor: 客户端回传的游标字符串；None 表示首页。

    Returns:
        带时区的创建时间与标识二元组；首页返回 None。

    Raises:
        Denied: 游标格式非法或缺少时区时抛出。
    """
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
