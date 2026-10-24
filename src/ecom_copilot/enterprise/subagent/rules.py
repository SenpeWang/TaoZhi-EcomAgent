"""分层规则发现与渐进注入引擎（Hierarchical Rules Engine）。

遵循 Antigravity (agy) 设计规范：
1. 向上遍历工作区发现项目级 AGENTS.md、GEMINI.md 与 .agents/rules/*.md；
2. 规范化路径并严格去重（Deduplication）；
3. 支持渐进式披露（Progressive Disclosure：always_on 始终加载，model_decision 按需激活）；
4. 格式化生成标准的 <user_rules> 提示词上下文；
5. 严格遵循 Google Style Docstring 规范。
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Dict, List, Optional, Set


class RuleDocument:
    """表示一个已发现并解析的规则文件。"""

    def __init__(
        self,
        path: Path,
        content: str,
        is_always_on: bool = True,
        priority: int = 100,
    ) -> None:
        """初始化规则文档。

        Args:
            path: 规则文件绝对路径。
            content: 文件文本全文。
            is_always_on: 是否无条件常驻注入上下文。
            priority: 优先级数值（数值越高优先级越高）。
        """
        self.path = Path(path).resolve()
        self.content = content
        self.is_always_on = is_always_on
        self.priority = priority

    @property
    def filename(self) -> str:
        """获取规则文件名。

        Returns:
            str: 文件名。
        """
        return self.path.name


class RulesEngine:
    """管理多层级规则的发现、加载与上下文格式化拼装。"""

    def __init__(self, start_dir: Optional[Path] = None) -> None:
        """初始化规则引擎。

        Args:
            start_dir: 起始扫描目录，默认为当前工作目录。
        """
        self.start_dir = Path(start_dir or Path.cwd()).resolve()
        self._loaded_rules: List[RuleDocument] = []
        self._scanned_paths: Set[str] = set()

    def discover_rules(self) -> List[RuleDocument]:
        """向上追溯目录树直至根目录，发现所有生效的规范文档。

        Returns:
            List[RuleDocument]: 按优先级排序的规则文档列表。
        """
        self._loaded_rules.clear()
        self._scanned_paths.clear()

        current: Optional[Path] = self.start_dir
        visited_dirs: Set[Path] = set()

        while current and current not in visited_dirs:
            visited_dirs.add(current)

            # 1. 检测同级 AGENTS.md / GEMINI.md
            for target_name in ["AGENTS.md", "GEMINI.md", "CLAUDE.md"]:
                cand = current / target_name
                self._load_if_exists(cand, priority=100)

            # 2. 检测 .agents/rules/*.md
            rules_dir = current / ".agents" / "rules"
            if rules_dir.is_dir():
                for rule_file in rules_dir.glob("*.md"):
                    self._load_if_exists(rule_file, priority=80)

            # 向上冒泡一轮
            parent = current.parent
            if parent == current:
                break
            current = parent

        # 3. 扫描全局配置 ~/.gemini/config/rules/
        global_rules = Path.home() / ".gemini" / "config" / "rules"
        if global_rules.is_dir():
            for rule_file in global_rules.glob("*.md"):
                self._load_if_exists(rule_file, priority=50)

        # 按优先级升序，方便高优先级的后加载或按规范呈现
        self._loaded_rules.sort(key=lambda r: r.priority, reverse=True)
        return self._loaded_rules

    def _load_if_exists(self, file_path: Path, priority: int) -> None:
        """检查并加载单个规则文件（执行路径去重）。

        Args:
            file_path: 目标路径。
            priority: 优先级评分。
        """
        try:
            resolved = file_path.resolve()
            canon_str = str(resolved)
            if canon_str in self._scanned_paths:
                return

            if resolved.is_file() and os.access(resolved, os.R_OK):
                content = resolved.read_text(encoding="utf-8")
                # 简单解析头部是否含有 trigger: model_decision
                is_always = True
                if "trigger: model_decision" in content:
                    is_always = False

                self._loaded_rules.append(
                    RuleDocument(
                        path=resolved,
                        content=content,
                        is_always_on=is_always,
                        priority=priority,
                    )
                )
                self._scanned_paths.add(canon_str)
        except Exception:
            pass

    def build_rules_prompt_block(self) -> str:
        """构建标准的 <user_rules> 提示词上下文代码块。

        Returns:
            str: 包含所有生效规则的 Markdown 上下文块。
        """
        if not self._loaded_rules:
            self.discover_rules()

        active_rules = [r for r in self._loaded_rules if r.is_always_on]
        if not active_rules:
            return ""

        parts = [
            "<user_rules>",
            "The following are user-defined rules that you MUST ALWAYS FOLLOW WITHOUT ANY EXCEPTION. "
            "These rules take precedence over any other instructions.",
            "Review them carefully and always take them into account when you generate responses and code:",
        ]

        for r in active_rules:
            parts.append(f"<RULE[{r.path}]>")
            parts.append(r.content.strip())
            parts.append(f"</RULE[{r.path}]>")

        parts.append("</user_rules>")
        return "\n".join(parts)
