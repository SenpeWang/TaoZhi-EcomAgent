"""读取指定时区的当前系统时间。"""
from datetime import datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .outcome import ToolInvocationError

DEFAULT_TIMEZONE = "Asia/Shanghai"


def get_current_time(timezone: str = DEFAULT_TIMEZONE) -> dict[str, str]:
    """返回 IANA 时区名称和精确到秒、包含 UTC 偏移的 ISO 8601 时间。"""
    try:
        zone = ZoneInfo(timezone)
    except (TypeError, ValueError, ZoneInfoNotFoundError) as exc:
        raise ToolInvocationError("tool.invalid_timezone") from exc
    return {"current_time": datetime.now(zone).isoformat(timespec="seconds"), "timezone": zone.key}
