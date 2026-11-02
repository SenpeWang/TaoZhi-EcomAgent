"""会话轨迹双轨日志记录器（Transcript Memory Logger）。

自研双轨记忆日志架构：
1. 紧凑日志 (transcript.jsonl) 与全量日志 (transcript_full.jsonl) 双轨并行；
2. 行号严格对齐（Line-Indexed），紧凑日志按需截断并标记 truncated_fields；
3. 支持高保真回溯、特定步长检索与上下文预算控制；
4. 严格遵循 Google Python Style 规范。
"""

from __future__ import annotations

import datetime
import json
from pathlib import Path
from typing import Any, Dict, List, Optional


class TranscriptStep:
    """表示会话轨迹中的一个原子步骤。"""

    def __init__(
        self,
        step_index: int,
        source: str,
        step_type: str,
        status: str = "DONE",
        content: str = "",
        thinking: Optional[str] = None,
        tool_calls: Optional[List[Dict[str, Any]]] = None,
        media: Optional[List[Dict[str, Any]]] = None,
        created_at: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        """初始化轨迹步骤。

        Args:
            step_index: 步骤序号（从 1 开始递增）。
            source: 触发源（USER_EXPLICIT, MODEL, SYSTEM, SUBAGENT）。
            step_type: 步骤类型（USER_INPUT, PLANNER_RESPONSE, TOOL_CALL, TOOL_RESULT）。
            status: 终态（DONE, ERROR, IN_PROGRESS）。
            content: 文本内容。
            thinking: 思维链或内部规划思考过程。
            tool_calls: 工具调用参数及元数据。
            media: 多媒体附件。
            created_at: ISO-8601 格式时间戳。
            metadata: 附加元数据。
        """
        self.step_index = step_index
        self.source = source
        self.step_type = step_type
        self.status = status
        self.content = content
        self.thinking = thinking
        self.tool_calls = tool_calls or []
        self.media = media or []
        self.created_at = created_at or datetime.datetime.now(datetime.timezone.utc).isoformat()
        self.metadata = metadata or {}

    def to_full_dict(self) -> Dict[str, Any]:
        """导出全量完整字典，保留所有字段原始大小。

        Returns:
            Dict[str, Any]: 完整数据字典。
        """
        data: Dict[str, Any] = {
            "step_index": self.step_index,
            "source": self.source,
            "type": self.step_type,
            "status": self.status,
            "created_at": self.created_at,
            "content": self.content,
        }
        if self.thinking is not None:
            data["thinking"] = self.thinking
        if self.tool_calls:
            data["tool_calls"] = self.tool_calls
        if self.media:
            data["media"] = self.media
        if self.metadata:
            data["metadata"] = self.metadata
        return data

    def to_compact_dict(self, max_content_len: int = 500, max_thinking_len: int = 300) -> Dict[str, Any]:
        """导出紧凑字典，大字段截断并标注 truncated_fields。

        Args:
            max_content_len: 正文最大保留字符数。
            max_thinking_len: 思考链最大保留字符数。

        Returns:
            Dict[str, Any]: 紧凑字典。
        """
        data = self.to_full_dict()
        truncated_fields: List[str] = []

        if len(self.content) > max_content_len:
            data["content"] = self.content[:max_content_len] + f"... [truncated {len(self.content) - max_content_len} chars]"
            truncated_fields.append("content")

        if self.thinking and len(self.thinking) > max_thinking_len:
            data["thinking"] = self.thinking[:max_thinking_len] + f"... [truncated {len(self.thinking) - max_thinking_len} chars]"
            truncated_fields.append("thinking")

        if len(json.dumps(self.tool_calls)) > 600:
            # 工具调用只保留函数名和首个参数摘要
            compact_tools = []
            for tc in self.tool_calls:
                compact_tools.append({
                    "name": tc.get("name", "unknown"),
                    "summary": str(tc.get("parameters", {}))[:120],
                })
            data["tool_calls"] = compact_tools
            truncated_fields.append("tool_calls")

        if truncated_fields:
            data["truncated_fields"] = truncated_fields

        return data


class TranscriptLogger:
    """管理会话轨迹的双轨 JSONL 日志存储与索引查询。"""

    def __init__(self, log_dir: Path) -> None:
        """初始化轨迹记录器。

        Args:
            log_dir: 轨迹存放目录（如 <brain_dir>/.system_generated/logs/）。
        """
        self.log_dir = Path(log_dir)
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self.compact_path = self.log_dir / "transcript.jsonl"
        self.full_path = self.log_dir / "transcript_full.jsonl"
        self._step_counter = self._count_existing_steps()

    def _count_existing_steps(self) -> int:
        """统计已有步骤行数以保证 step_index 连续递增。

        Returns:
            int: 已有行数。
        """
        if not self.full_path.exists():
            return 0
        with open(self.full_path, "r", encoding="utf-8") as f:
            return sum(1 for _ in f)

    @property
    def current_step_count(self) -> int:
        """获取当前已记录的总步数。

        Returns:
            int: 总步数。
        """
        return self._step_counter

    def append_step(
        self,
        source: str,
        step_type: str,
        content: str = "",
        thinking: Optional[str] = None,
        tool_calls: Optional[List[Dict[str, Any]]] = None,
        status: str = "DONE",
        metadata: Optional[Dict[str, Any]] = None,
    ) -> TranscriptStep:
        """追加一条新记录到双轨日志文件中。

        Args:
            source: 动作源。
            step_type: 动作类型。
            content: 文本正文。
            thinking: 思考过程。
            tool_calls: 工具调用明细。
            status: 执行状态。
            metadata: 扩展元数据。

        Returns:
            TranscriptStep: 已持久化的轨迹步骤对象。
        """
        self._step_counter += 1
        step = TranscriptStep(
            step_index=self._step_counter,
            source=source,
            step_type=step_type,
            status=status,
            content=content,
            thinking=thinking,
            tool_calls=tool_calls,
            metadata=metadata,
        )

        full_line = json.dumps(step.to_full_dict(), ensure_ascii=False) + "\n"
        compact_line = json.dumps(step.to_compact_dict(), ensure_ascii=False) + "\n"

        with open(self.full_path, "a", encoding="utf-8") as f_full:
            f_full.write(full_line)

        with open(self.compact_path, "a", encoding="utf-8") as f_compact:
            f_compact.write(compact_line)

        return step

    def read_compact_steps(self, limit: Optional[int] = None) -> List[Dict[str, Any]]:
        """读取紧凑日志列表（用于日常上下文拼装与快速浏览）。

        Args:
            limit: 最多读取尾部若干步。

        Returns:
            List[Dict[str, Any]]: 紧凑字典列表。
        """
        if not self.compact_path.exists():
            return []
        steps = []
        with open(self.compact_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    steps.append(json.loads(line))
        if limit:
            return steps[-limit:]
        return steps

    def read_full_step_by_index(self, step_index: int) -> Optional[Dict[str, Any]]:
        """按行号索引精准读取 full_transcript 中对应行的全量内容。

        Args:
            step_index: 步骤序号（1-indexed）。

        Returns:
            Optional[Dict[str, Any]]: 全量记录数据字典，若不存在返回 None。
        """
        if not self.full_path.exists() or step_index <= 0:
            return None
        with open(self.full_path, "r", encoding="utf-8") as f:
            for idx, line in enumerate(f, start=1):
                if idx == step_index:
                    line = line.strip()
                    return json.loads(line) if line else None
        return None
