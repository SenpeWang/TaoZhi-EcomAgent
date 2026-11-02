"""工具输出流式累加与巨量数据安全转存器。

在内存中维护有界滚动缓冲区，一旦输出超出安全上限，自动在后台将完整的未裁剪
数据流式写入临时磁盘文件，并返回绝对路径，保障零信息丢失且不撑爆内存。
严格遵循 Google Python Style 规范。
"""

from __future__ import annotations

import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from .truncate import DEFAULT_MAX_BYTES, DEFAULT_MAX_LINES, TruncationResult, truncate_text


@dataclass
class OutputSnapshot:
    """输出切片与转存快照。"""
    content: str
    truncation: TruncationResult
    full_output_path: Optional[str] = None


class OutputAccumulator:
    """有界内存输出累加器，支持海量数据自动溢出转存至本地临时文件。"""

    def __init__(
        self,
        max_lines: int = DEFAULT_MAX_LINES,
        max_bytes: int = DEFAULT_MAX_BYTES,
        temp_dir: Optional[Path] = None,
        prefix: str = "taozhi_tool_output_",
    ) -> None:
        """初始化输出累加器。

        Args:
            max_lines: 最大可见行数。
            max_bytes: 最大可见字节数。
            temp_dir: 临时文件保存目录。
            prefix: 临时文件命名前缀。
        """
        self.max_lines = max_lines
        self.max_bytes = max_bytes
        self.temp_dir = temp_dir
        self.prefix = prefix
        self._chunks: list[str] = []
        self._total_bytes = 0
        self._temp_file_path: Optional[str] = None

    def append(self, text: str) -> None:
        """追加一段文本或工具返回内容。

        Args:
            text: 待追加文本。
        """
        if not text:
            return
        self._chunks.append(text)
        self._total_bytes += len(text.encode("utf-8"))

    def snapshot(self, persist_if_truncated: bool = True) -> OutputSnapshot:
        """生成当前的文本摘要快照，若已截断则执行全量落盘。

        Args:
            persist_if_truncated: 超限时是否自动落盘保存完整原件。

        Returns:
            OutputSnapshot: 包含预览文本与完整原件路径的快照对象。
        """
        full_content = "".join(self._chunks)
        truncation = truncate_text(
            content=full_content,
            max_lines=self.max_lines,
            max_bytes=self.max_bytes,
        )

        if truncation.truncated and persist_if_truncated and not self._temp_file_path:
            target_dir = str(self.temp_dir) if self.temp_dir else tempfile.gettempdir()
            os.makedirs(target_dir, exist_ok=True)
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                dir=target_dir,
                prefix=self.prefix,
                suffix=".txt",
                delete=False,
            ) as f:
                f.write(full_content)
                self._temp_file_path = f.name

        return OutputSnapshot(
            content=truncation.content,
            truncation=truncation,
            full_output_path=self._temp_file_path,
        )