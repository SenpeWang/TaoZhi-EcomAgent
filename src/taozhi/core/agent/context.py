"""管理调研子任务的独立工作区、临时文件与交付工件。"""

from __future__ import annotations

import json
import shutil
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

from taozhi.observability.tracing import record_diagnostic

from .protocol import WorkspaceMode


class ArtifactMetadata:
    """定义工件元数据属性契约。"""

    def __init__(
        self,
        summary: str,
        user_facing: bool = True,
        request_feedback: bool = False,
    ) -> None:
        """初始化工件元数据。

        Args:
            summary: 详细多行摘要说明。
            user_facing: 是否向终端用户直接展示。
            request_feedback: 是否请求用户确认/反馈。
        """
        self.summary = summary
        self.user_facing = user_facing
        self.request_feedback = request_feedback

    def to_dict(self) -> Dict[str, Any]:
        """序列化为字典。

        Returns:
            Dict[str, Any]: 元数据字典。
        """
        return {
            "Summary": self.summary,
            "UserFacing": self.user_facing,
            "RequestFeedback": self.request_feedback,
        }


class AgentContext:
    """智能体隔离执行上下文。

    Attributes:
        conversation_id (str): 子会话全局唯一 ID。
        parent_id (Optional[str]): 父智能体 ID（根智能体为 None）。
        role (str): 智能体所扮演的业务角色。
        depth (int): 智能体派生嵌套深度。
        workspace_mode (WorkspaceMode): 工作区隔离模式。
        base_dir (Path): 该智能体专用的存储根目录。
        scratch_dir (Path): 暂存大容量原始抓取数据的 scratch 目录。
        artifacts_dir (Path): 存放最终交付工件的 artifacts 目录。
        workspace_dir (Optional[Path]): 分支隔离工作区（BRANCH 模式）。
    """

    def __init__(
        self,
        conversation_id: Optional[str] = None,
        parent_id: Optional[str] = None,
        role: str = "Assistant",
        depth: int = 1,
        workspace_mode: WorkspaceMode = WorkspaceMode.BRANCH,
        root_dir: Optional[Path] = None,
    ) -> None:
        """初始化智能体执行上下文。

        Args:
            conversation_id: 自定义会话 ID，默认随机生成。
            parent_id: 父级智能体 ID。
            role: 业务角色名称。
            depth: 派生嵌套层级。
            workspace_mode: 隔离模式。
            root_dir: 自定义数据存储根路径。
        """
        self.conversation_id = conversation_id or f"sub_{uuid.uuid4().hex[:12]}"
        self.parent_id = parent_id
        self.role = role
        self.depth = depth
        self.workspace_mode = workspace_mode

        if root_dir is None:
            proj_root = Path(__file__).resolve().parents[4]
            root_dir = proj_root / "data/runtime/brain"

        self.base_dir = root_dir / self.conversation_id
        self.scratch_dir = self.base_dir / "scratch"
        self.artifacts_dir = self.base_dir / "artifacts"

        self.workspace_dir: Optional[Path] = None
        if self.workspace_mode == WorkspaceMode.BRANCH:
            self.workspace_dir = self.base_dir / "workspace"

        self._ensure_dirs()

    def _ensure_dirs(self) -> None:
        """确保智能体所需的隔离工作区目录存在。"""
        self.scratch_dir.mkdir(parents=True, exist_ok=True)
        self.artifacts_dir.mkdir(parents=True, exist_ok=True)
        if self.workspace_dir:
            self.workspace_dir.mkdir(parents=True, exist_ok=True)

    def write_scratch(self, filename: str, content: str) -> Path:
        """向 scratch 暂存区写入原始数据或临时快照。

        Args:
            filename: 文件名（如 raw_search_1688.json）。
            content: 文本正文或原始 JSON 字符串。

        Returns:
            Path: 写入后的绝对路径。
        """
        target = self.scratch_dir / filename
        target.write_text(content, encoding="utf-8")
        return target

    def write_artifact(
        self,
        filename: str,
        content: str,
        metadata: Optional[ArtifactMetadata] = None,
    ) -> Path:
        """向 artifacts 交付区写入正式成果文档，并附带元数据。

        Args:
            filename: 工件文件名（如 report.md）。
            content: 正文内容。
            metadata: 工件元数据配置。

        Returns:
            Path: 写入后的绝对路径。
        """
        target = self.artifacts_dir / filename
        target.write_text(content, encoding="utf-8")

        if metadata:
            meta_path = self.artifacts_dir / f"{filename}.meta.json"
            meta_path.write_text(
                json.dumps(metadata.to_dict(), ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        return target

    def get_scratch_files(self) -> List[str]:
        """获取当前 scratch 目录下的全部文件路径列表。

        Returns:
            List[str]: 文件绝对路径字符串列表。
        """
        if not self.scratch_dir.exists():
            return []
        return [str(p.resolve()) for p in self.scratch_dir.iterdir() if p.is_file()]

    def get_artifacts(self) -> List[str]:
        """获取当前 artifacts 目录下的全部文件路径列表（过滤元数据文件）。

        Returns:
            List[str]: 文件绝对路径字符串列表。
        """
        if not self.artifacts_dir.exists():
            return []
        return [
            str(p.resolve())
            for p in self.artifacts_dir.iterdir()
            if p.is_file() and not p.name.endswith(".meta.json")
        ]

    def cleanup(self) -> None:
        """清理临时工作区并保留交付件，清理异常关联本次执行的诊断日志。"""
        for temporary in (self.scratch_dir, self.workspace_dir):
            if temporary is None:
                continue
            try:
                shutil.rmtree(temporary)
            except FileNotFoundError:
                continue
            except OSError as exc:
                record_diagnostic("storage.cleanup", "skipped", exc)
