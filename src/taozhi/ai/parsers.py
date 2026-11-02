"""结构化输出与 JSON 健壮解析器。

提供 Pydantic 模式校验与 JSON 容错解析，支持自动重试机制。
严格遵循 Google Python Style 规范。
"""

from __future__ import annotations

from typing import Any, Optional, Type
from langchain_core.output_parsers import JsonOutputParser
from pydantic import BaseModel

_JSON_RULES = """【输出格式要求】
1. 只输出一个 JSON 对象，不要输出任何解释、标题、前言或 Markdown 代码块围栏；
2. 不要使用单引号、注释或尾随逗号；
3. 所有字符串使用双引号，必须严格符合下面给出的字段结构；
4. 若某项确无信息，使用空字符串或空数组，禁止省略字段。
"""


def get_pydantic_parser(schema: Optional[Type[BaseModel]]) -> Any:
    """获取 PydanticOutputParser 实例，解析失败时优雅降级。

    Args:
        schema: 预期的 Pydantic 数据模型类。

    Returns:
        Any: 解析器实例，无法加载时返回 None。
    """
    if schema is None:
        return None
    try:
        from langchain_core.output_parsers import PydanticOutputParser
        return PydanticOutputParser(pydantic_object=schema)
    except Exception:
        return None


def clean_model_output(text: str) -> str:
    """剥离大模型返回的思维链标签与 Markdown 围栏代码块。

    Args:
        text: 模型原始输出文本。

    Returns:
        str: 清洗后的纯净文本。
    """
    if not text:
        return ""
    cleaned = text.strip()
    for marker in ("<think>", "<thinking>"):
        if marker in cleaned:
            tail = cleaned.split(marker, 1)[1]
            for end in ("</think>", "</thinking>"):
                if end in tail:
                    tail = tail.split(end, 1)[1]
            cleaned = tail.strip()

    if cleaned.startswith("```"):
        lines = cleaned.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        cleaned = chr(10).join(lines).strip()

    return cleaned