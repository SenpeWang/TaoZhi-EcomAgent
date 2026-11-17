"""商品型号与实体标识抽取纯领域规则（零 IO 依赖）。"""
from __future__ import annotations

import re
from typing import List

_ENTITY_RE = re.compile(
    r"(?<![A-Za-z0-9.])[A-Za-z]{1,8}[-_]\d{1,6}[A-Za-z]?(?![A-Za-z0-9.])"
    r"|(?<![A-Za-z0-9.])[A-Z]{1,3}\d{1,4}[A-Z]?(?![A-Za-z0-9.])"
)
_IGNORED_PREFIXES = re.compile(r"^(?:V\d+|L[1-3]|H\d+|T\d+|PD\d+|QC\d+)$", re.IGNORECASE)


def extract_entities(text: str) -> List[str]:
    """从文本或提问中提取去重后的商品/型号标识，排除版本号与密级标记。"""
    if not text:
        return []
    seen = set()
    out: List[str] = []
    for raw in _ENTITY_RE.findall(text):
        token = raw.strip()
        if not token or _IGNORED_PREFIXES.match(token):
            continue
        key = token.upper()
        if key not in seen:
            seen.add(key)
            out.append(token)
    return out
