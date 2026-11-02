"""确定性事实与改动硬兜底守卫。

在触发上下文压缩摘要前，静态提取被压缩区间产生的文件产物、出厂底价结论
与实体三元组，拼装为防失忆事实块，防止大模型泛化摘要时丢失核心业务约束。
严格遵循 Google Python Style 规范。
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Set


def extract_artifact_files(steps: List[Dict[str, Any]]) -> List[str]:
    """静态提取历史轨迹中生成或引用的全部工件文件路径。

    Args:
        steps: 待压缩的历史步骤列表。

    Returns:
        List[str]: 去重后的工件文件路径列表。
    """
    files: Set[str] = set()
    for s in steps:
        meta = s.get("metadata") or {}
        if "file" in meta and meta["file"]:
            files.add(str(meta["file"]))
        content = s.get("content", "")
        for m in re.finditer(r"([a-zA-Z0-9_\-\./\\]+\.(?:md|json|txt|csv|png))", content):
            matched = m.group(1).strip()
            if "/" in matched or "\\" in matched or matched.endswith(".md"):
                files.add(matched)
    return sorted(list(files))


def format_state_guard_block(
    artifacts: List[str],
    facts: List[Dict[str, Any]],
) -> str:
    """生成确定性状态与事实硬兜底提示文本块。

    Args:
        artifacts: 工件列表。
        facts: 租户实体事实列表。

    Returns:
        str: 结构化事实块文本。
    """
    lines: List[str] = ["# Deterministic Fact Anchors (Preserved State)"]

    if artifacts:
        lines.append("- Generated Artifacts:")
        for af in artifacts[:10]:
            lines.append(f"  * {af}")

    if facts:
        lines.append("- Verified Knowledge Facts:")
        for f in facts[:10]:
            subj = f.get("subject", "")
            pred = f.get("predicate", "")
            obj = f.get("object", "")
            lines.append(f"  * 【{subj}】{pred}: {obj}")

    if len(lines) == 1:
        return ""

    return "\n".join(lines)