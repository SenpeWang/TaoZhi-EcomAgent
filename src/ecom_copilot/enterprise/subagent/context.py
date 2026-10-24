"""子智能体运行上下文与工件隔离存储（Subagent Context & Artifacts System）。

遵循 Antigravity (agy) 设计规范：
1. 隔离上下文（SubagentContext）避免子级海量原始抓取污染父级上下文；
2. Scratch 目录暂存大容量中间态（Raw Scraped Data、临时脚本）；
3. Artifacts 交付区管理高价值成果，附带元数据契约（Summary, UserFacing, RequestFeedback）；
4. 提供规范的 GitHub 提示框（Alerts）、Mermaid 图表、走马灯（Carousel）与行级文件超链接生成器；
5. 严格遵循 Google Style Docstring 规范。
"""

from __future__ import annotations

import json
import os
import shutil
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

from .protocol import WorkspaceMode


class ArtifactMetadata:
    """定义工件元数据属性（符合 agy 产物规范）。"""

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


class SubagentContext:
    """子智能体隔离执行上下文。

    Attributes:
        conversation_id (str): 子会话全局唯一 ID。
        parent_id (Optional[str]): 父智能体 ID（根智能体为 None）。
        role (str): 智能体所扮演的业务角色。
        depth (int): 智能体派生嵌套深度。
        workspace_mode (WorkspaceMode): 工作区隔离模式。
        base_dir (Path): 该子智能体专用的存储根目录。
        scratch_dir (Path): 暂存大容量原始抓取数据的 scratch 目录。
        artifacts_dir (Path): 存放最终交付工件的 artifacts 目录。
        logs_dir (Path): 存放双轨日志的 logs 目录。
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
        self.logs_dir = self.base_dir / ".system_generated" / "logs"

        # 分支工作区（仅在 BRANCH 模式下建立独立目录）
        self.workspace_dir: Optional[Path] = None
        if self.workspace_mode == WorkspaceMode.BRANCH:
            self.workspace_dir = self.base_dir / "workspace"

        self._ensure_dirs()

    @property
    def artifact_dir(self) -> Path:
        """工件目录属性别名。"""
        return self.artifacts_dir

    def _ensure_dirs(self) -> None:
        """确保子智能体所需的隔离工作区目录存在。"""
        self.scratch_dir.mkdir(parents=True, exist_ok=True)
        self.artifacts_dir.mkdir(parents=True, exist_ok=True)
        self.logs_dir.mkdir(parents=True, exist_ok=True)
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
        """清理临时工作区（保留 artifacts 与 logs，删除大容量 scratch 与分支副本）。"""
        if self.scratch_dir.exists():
            shutil.rmtree(self.scratch_dir, ignore_errors=True)
        if self.workspace_dir and self.workspace_dir.exists():
            shutil.rmtree(self.workspace_dir, ignore_errors=True)

    # ---------------- 产物 Markdown 格式化规范辅助器 ----------------

    @staticmethod
    def build_alert(alert_type: str, content: str) -> str:
        """构建 GitHub 风格的 Markdown 提示块。

        Supported alert_type: NOTE, TIP, IMPORTANT, WARNING, CAUTION.

        Args:
            alert_type: 提示类型。
            content: 提示内容。

        Returns:
            str: 格式化的 alert markdown。
        """
        tag = alert_type.upper()
        if tag not in {"NOTE", "TIP", "IMPORTANT", "WARNING", "CAUTION"}:
            tag = "NOTE"
        lines = [f"> [!{tag}]"]
        for line in content.splitlines():
            lines.append(f"> {line}")
        return "\n".join(lines)

    @staticmethod
    def build_mermaid(diagram_type: str, body: str) -> str:
        """构建 Antigravity 规范支持的 Mermaid 图表代码块。

        支持图表：flowchart TD/LR, sequenceDiagram, stateDiagram-v2, erDiagram, xychart-beta.

        Args:
            diagram_type: 图表声明类型。
            body: 图表内部定义。

        Returns:
            str: Mermaid fenced code block.
        """
        return f"```{diagram_type}\n{body.strip()}\n```"

    @staticmethod
    def build_carousel(slides: List[str]) -> str:
        """构建支持图片与代码比对的走马灯（Carousel）组件。

        Args:
            slides: 各个轮播卡片的 markdown 文本内容列表。

        Returns:
            str: 格式化的 ````carousel 块。
        """
        inner = "\n<!-- slide -->\n".join(s.strip() for s in slides)
        return f"````carousel\n{inner}\n````"

    @staticmethod
    def format_file_link(
        file_path: Path | str,
        start_line: Optional[int] = None,
        end_line: Optional[int] = None,
        label: Optional[str] = None,
    ) -> str:
        """生成符合 agy 规范的带行号锚点绝对路径超链接。

        Args:
            file_path: 目标文件路径。
            start_line: 起始行号。
            end_line: 结束行号。
            label: 链接展示文字，默认采用 basename。

        Returns:
            str: [label](file:///absolute/path#L1-L10) 格式文本。
        """
        p = Path(file_path).resolve()
        display = label or p.name
        uri = f"file://{p}"
        if start_line is not None:
            if end_line is not None and end_line != start_line:
                uri += f"#L{start_line}-L{end_line}"
            else:
                uri += f"#L{start_line}"
        return f"[{display}]({uri})"
