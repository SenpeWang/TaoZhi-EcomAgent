"""结构化工具输出安全截断器。

提供行限制与字节限制竞争截断机制，严格保障换行行边界完整性（不切断中间行），
并输出截断统计元数据供智能体感知。
严格遵循 Google Python Style 规范。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

DEFAULT_MAX_LINES = 2000
DEFAULT_MAX_BYTES = 50 * 1024  # 50KB


@dataclass
class TruncationResult:
    """截断结果数据结构。"""
    content: str
    truncated: bool
    truncated_by: Optional[str]  # "lines" | "bytes" | None
    total_lines: int
    total_bytes: int
    output_lines: int
    output_bytes: int
    max_lines: int
    max_bytes: int


def truncate_text(
    content: str,
    max_lines: int = DEFAULT_MAX_LINES,
    max_bytes: int = DEFAULT_MAX_BYTES,
    from_tail: bool = False,
) -> TruncationResult:
    """按行数与字节数双重阈值对文本进行安全截断。

    行数与字节数哪一个先达到上限即生效。截断时始终按换行符对齐，
    绝不返回破损的不完整行。

    Args:
        content: 原始长文本。
        max_lines: 允许的最大保留行数。
        max_bytes: 允许的最大保留字节数。
        from_tail: 是否从尾部截断（保留最新内容）。默认 False（保留头部）。

    Returns:
        TruncationResult: 包含截断后内容与元数据的对象。
    """
    raw_bytes = content.encode("utf-8")
    total_bytes = len(raw_bytes)
    lines = content.splitlines(keepends=True)
    total_lines = len(lines)

    if total_lines <= max_lines and total_bytes <= max_bytes:
        return TruncationResult(
            content=content,
            truncated=False,
            truncated_by=None,
            total_lines=total_lines,
            total_bytes=total_bytes,
            output_lines=total_lines,
            output_bytes=total_bytes,
            max_lines=max_lines,
            max_bytes=max_bytes,
        )

    selected_lines: list[str] = []
    current_bytes = 0
    truncated_by: str = "lines"

    candidate_lines = list(reversed(lines)) if from_tail else lines

    for idx, line in enumerate(candidate_lines):
        line_bytes = len(line.encode("utf-8"))
        if idx >= max_lines:
            truncated_by = "lines"
            break
        if current_bytes + line_bytes > max_bytes:
            truncated_by = "bytes"
            break
        selected_lines.append(line)
        current_bytes += line_bytes

    if from_tail:
        selected_lines.reverse()

    result_text = "".join(selected_lines)
    return TruncationResult(
        content=result_text,
        truncated=True,
        truncated_by=truncated_by,
        total_lines=total_lines,
        total_bytes=total_bytes,
        output_lines=len(selected_lines),
        output_bytes=len(result_text.encode("utf-8")),
        max_lines=max_lines,
        max_bytes=max_bytes,
    )


# 向后兼容别名
preserve_line_boundary_truncate = truncate_text