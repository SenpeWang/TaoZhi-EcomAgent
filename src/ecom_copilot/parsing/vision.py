"""Qwen3-VL 多模态解析：研报图表 / 截图 → 结构化描述（PRD B2）。

端点不支持视觉输入时自动降级为规则化表格描述，流水线不中断。
"""

from __future__ import annotations

from typing import List, Optional

from langchain_core.messages import HumanMessage

from ..config import Settings, get_settings
from ..llm import ModelTier, get_llm

_CHART_PROMPT = """你是产业研报图表解析专家。请阅读图片内容，输出严格 JSON：
{"chart_type":"柱状图/折线图/饼图/表格/流程图/其他","title":"","key_series":[""],"trend":"","conclusion":"","data_points":[""]}
要求：只输出 JSON；conclusion 用一句话说明该图支撑的产业结论。"""

_TABLE_PROMPT = """你是研报表格还原专家。请把图片中的表格还原为 Markdown 表格，
并输出 JSON：{"markdown":"...","summary":"一句话说明表格结论"}。只输出 JSON。"""


class VisionParser:
    def __init__(self, settings: Optional[Settings] = None) -> None:
        self.settings = settings or get_settings()
        self.enabled = bool(self.settings.api_key)

    def describe_chart(self, image_b64: str) -> Optional[dict]:
        return self._call(_CHART_PROMPT, image_b64)

    def describe_table(self, image_b64: str) -> Optional[dict]:
        return self._call(_TABLE_PROMPT, image_b64)

    def _call(self, prompt: str, image_b64: str) -> Optional[dict]:
        if not self.enabled or not image_b64:
            return None
        from ..llm.json_utils import extract_json

        message = HumanMessage(
            content=[
                {"type": "text", "text": prompt},
                {
                    "type": "image_url",
                    "image_url": {"url": f"data:image/png;base64,{image_b64}"},
                },
            ]
        )
        try:
            raw = get_llm().chat([message], tier=ModelTier.VISION, max_tokens=1500)
        except Exception:  # noqa: BLE001
            return None
        return extract_json(raw, expect="object")

    def describe_batch(self, images: List[str], limit: int = 3) -> List[str]:
        out: List[str] = []
        for image in images[:limit]:
            data = self.describe_chart(image)
            if not data:
                continue
            title = data.get("title") or data.get("chart_type") or "图表"
            out.append(
                f"【{title}】趋势：{data.get('trend', '')}；结论：{data.get('conclusion', '')}"
            )
        return out
