"""渐进式技能系统（Progressive Disclosure Skills System）。

自研技能架构设计：
1. 技能以文件夹承载，核心由带 YAML Frontmatter 的 SKILL.md 定义；
2. 初始阶段仅向提示词注入名称与描述摘要（极低 Token 占用）；
3. 执行时按需调阅 SKILL.md 完整指令并运行其 scripts/ 辅助脚本；
4. 内置电商专属技能与工作区外部技能动态扫描；
5. 严格遵循 Google Python Style 规范。
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Dict, List, Optional


class Skill:
    """表示一个已发现并解析的技能实体。"""

    def __init__(
        self,
        name: str,
        description: str,
        skill_dir: Path,
        instruction_file: Path,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        """初始化技能。

        Args:
            name: 技能唯一标识符。
            description: 简明功用描述。
            skill_dir: 技能所在的根目录。
            instruction_file: SKILL.md 文件绝对路径。
            metadata: 扩展元数据。
        """
        self.name = name
        self.description = description
        self.skill_dir = Path(skill_dir).resolve()
        self.instruction_file = Path(instruction_file).resolve()
        self.metadata = metadata or {}

    def read_instructions(self) -> str:
        """读取该技能的完整详细操作手册与工作流规则。

        Returns:
            str: SKILL.md 的完整内容。
        """
        if self.instruction_file.exists():
            return self.instruction_file.read_text(encoding="utf-8")
        return ""

    def list_scripts(self) -> List[Path]:
        """列出该技能自带的辅助运行脚本。

        Returns:
            List[Path]: 脚本文件路径列表。
        """
        scripts_dir = self.skill_dir / "scripts"
        if scripts_dir.is_dir():
            return [p for p in scripts_dir.iterdir() if p.is_file()]
        return []


class SkillRegistry:
    """管理技能的跨目录发现、渐进式提示词拼装与按需加载。"""

    def __init__(self, workspace_root: Optional[Path] = None) -> None:
        """初始化技能注册中心。

        Args:
            workspace_root: 当前项目工作区根目录。
        """
        self.workspace_root = Path(workspace_root or Path.cwd()).resolve()
        self._skills: Dict[str, Skill] = {}
        self._discover_builtin_and_local()

    def _discover_builtin_and_local(self) -> None:
        """自动发现项目内以及用户主目录下的技能包。"""
        scan_paths = [
            # 1. 项目本地技能
            self.workspace_root / ".agents" / "skills",
            self.workspace_root / ".agent" / "skills",
            # 2. 用户全局技能
            Path.home() / ".agents" / "skills",
        ]

        for s_dir in scan_paths:
            if not s_dir.is_dir():
                continue
            for item in s_dir.iterdir():
                if item.is_dir():
                    skill_md = item / "SKILL.md"
                    if skill_md.is_file():
                        self._parse_and_register(skill_md, item)

    def _parse_and_register(self, skill_md: Path, skill_dir: Path) -> None:
        """解析 SKILL.md 文件的 YAML frontmatter 并注册。

        Args:
            skill_md: SKILL.md 路径。
            skill_dir: 所在文件夹路径。
        """
        try:
            content = skill_md.read_text(encoding="utf-8")
            # 匹配 YAML frontmatter: --- \n name: ... \n description: ... \n ---
            name = skill_dir.name
            desc = ""

            fm_match = re.match(r"^---\s*\n(.*?)\n---\s*\n", content, re.DOTALL)
            if fm_match:
                fm_text = fm_match.group(1)
                for line in fm_text.splitlines():
                    if line.startswith("name:"):
                        name = line.split("name:", 1)[1].strip().strip("'\"")
                    elif line.startswith("description:"):
                        desc = line.split("description:", 1)[1].strip().strip("'\"")
            else:
                desc = f"Specialized skill in {skill_dir.name}"

            if not desc:
                # 提取第一段正文
                lines = [ln.strip() for ln in content.splitlines() if ln.strip() and not ln.startswith("#")]
                desc = lines[0] if lines else f"Skill {name}"

            self._skills[name] = Skill(
                name=name,
                description=desc,
                skill_dir=skill_dir,
                instruction_file=skill_md,
            )
        except Exception:
            pass

    def register(self, skill: Skill) -> None:
        """显式注册一个新技能。

        Args:
            skill: Skill 实例。
        """
        self._skills[skill.name] = skill

    def get(self, name: str) -> Optional[Skill]:
        """按名称查找技能。

        Args:
            name: 技能名称。

        Returns:
            Optional[Skill]: 技能对象。
        """
        return self._skills.get(name)

    def list_skills(self) -> List[Skill]:
        """获取全部已发现的技能清单。

        Returns:
            List[Skill]: 技能列表。
        """
        return list(self._skills.values())

    def build_skills_prompt_block(self) -> str:
        """构建自研规范的 <skills> 渐进披露上下文块。

        Returns:
            str: 格式化的 <skills> 文本。
        """
        if not self._skills:
            return ""

        lines = [
            "<skills>",
            "You can use specialized 'skills' to help you with complex tasks. Each skill has a name and a description listed below.",
            "",
            "If a skill seems relevant to your current task, you MUST read its `SKILL.md` instructions before proceeding.",
            "",
            "Available skills:",
        ]

        for s in sorted(self._skills.values(), key=lambda x: x.name):
            lines.append(f"- {s.name} ({s.instruction_file}): {s.description}")

        lines.append("</skills>")
        return "\n".join(lines)
