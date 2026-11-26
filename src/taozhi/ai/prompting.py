"""运行期与评测期共用的 Prompt 目录、渲染与校验工具。"""
from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import re
from typing import Dict, Iterable, Literal, Mapping, Optional, Tuple

_PLACEHOLDER = re.compile(r"\{\{([a-zA-Z_][a-zA-Z0-9_]*)\}\}")


class PromptVariableError(ValueError):
    """渲染 Prompt 时缺少模板变量。"""


def _render(template: Optional[str], values: Mapping[str, object]) -> Optional[str]:
    """用 ``{{name}}`` 占位符渲染模板；保留 JSON 花括号原样。"""
    if template is None:
        return None

    missing: set[str] = set()

    def replace(match: re.Match[str]) -> str:
        name = match.group(1)
        if name not in values:
            missing.add(name)
            return match.group(0)
        return str(values[name])

    rendered = _PLACEHOLDER.sub(replace, template)
    if missing:
        raise PromptVariableError(f"Prompt 缺少变量：{', '.join(sorted(missing))}")
    return rendered


@dataclass(frozen=True)
class PromptSpec:
    """一个可版本化、可校验的 Prompt 资产。"""

    id: str
    owner: Literal["runtime", "eval"]
    version: str
    purpose: str
    model_tier: str = "fast"
    system_template: Optional[str] = None
    user_template: Optional[str] = None
    output_schema: str = "text"

    def __post_init__(self) -> None:
        if not self.id.strip() or "." not in self.id:
            raise ValueError("Prompt id 必须使用点分命名")
        if self.owner not in {"runtime", "eval"}:
            raise ValueError("Prompt owner 只能是 runtime 或 eval")
        if not self.version.strip():
            raise ValueError("Prompt version 不能为空")
        if self.system_template is None and self.user_template is None:
            raise ValueError("Prompt 至少需要 system_template 或 user_template")

    def render(self, values: Mapping[str, object]) -> Tuple[Optional[str], Optional[str]]:
        """返回渲染后的 ``(system, user)``；缺失变量直接报错。"""
        return _render(self.system_template, values), _render(self.user_template, values)

    def checksum(self) -> str:
        """返回模板文本的稳定摘要，用于冻结评测算法版本。"""
        raw = f"{self.id}\n{self.system_template or ''}\n{self.user_template or ''}"
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()


class PromptCatalog:
    """按 ID 注册、渲染和列出 Prompt。"""

    def __init__(self, specs: Iterable[PromptSpec]) -> None:
        self._specs: Dict[str, PromptSpec] = {}
        for spec in specs:
            if spec.id in self._specs:
                raise ValueError(f"重复的 Prompt id：{spec.id}")
            self._specs[spec.id] = spec

    def get(self, prompt_id: str) -> PromptSpec:
        """按 ID 取 Prompt；未知 ID 直接报错。"""
        try:
            return self._specs[prompt_id]
        except KeyError as exc:
            raise KeyError(f"未注册的 Prompt：{prompt_id}") from exc

    def render(self, prompt_id: str, values: Mapping[str, object]) -> Tuple[Optional[str], Optional[str]]:
        """按 ID 渲染 Prompt。"""
        return self.get(prompt_id).render(values)

    def all(self) -> Tuple[PromptSpec, ...]:
        """按 ID 排序返回全部 Prompt。"""
        return tuple(self._specs[key] for key in sorted(self._specs))

    def checksums(self) -> Dict[str, str]:
        """返回 ID 到模板摘要的映射。"""
        return {spec.id: spec.checksum() for spec in self.all()}

    def catalog_checksum(self) -> str:
        """返回整个目录的摘要，适合作算法版本元数据。"""
        raw = "\n".join(f"{key}:{value}" for key, value in self.checksums().items())
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()


__all__ = [
    "PromptCatalog",
    "PromptSpec",
    "PromptVariableError",
]