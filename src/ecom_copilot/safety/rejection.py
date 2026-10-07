"""拒答门禁（PRD Q12 补全）：

触发条件（should_refuse）：
    ① 检索证据条数为 0 → no_evidence
    ② 核验置信度 < REJECT_CONFIDENCE_THRESHOLD（默认 0.3）→ low_confidence

拒答产物（build_refusal）：无法回答声明 + 缺失资料类型说明 + 客服热线 + 转人工引导，
JSON（dict）与文本（to_text）双形态；每次拒答经 log_rejection 落审计
data/audit/rejections.jsonl（一行一条 JSON：ts/question/reason）。
"""

from __future__ import annotations

import json
import time
from typing import Any, Dict, Optional, Tuple

from ..config import get_settings

HOTLINE = "400-680-9527"

_REASON_BRIEF = {
    "no_evidence": "知识库中未检索到相关资料",
    "low_confidence": "现有证据存在分歧或置信度不足",
}

_MISSING_INFO = {
    "no_evidence": "缺少可支撑回答的商品资料（如官方参数表 / 产品手册 / FAQ 文档）",
    "low_confidence": "需要更多可交叉验证的官方口径资料（参数表 / 适配表 / 售后政策页）",
}


def should_refuse(evidence_count: int, confidence: Optional[float]) -> Tuple[bool, str]:
    """拒答判定：返回 (是否拒答, 原因)；不拒答时原因为空串。"""
    if evidence_count <= 0:
        return True, "no_evidence"
    threshold = get_settings().reject_confidence_threshold
    if confidence is not None and float(confidence) < threshold:
        return True, "low_confidence"
    return False, ""


def build_refusal(question: str, reason: str, detail: str = "") -> Dict[str, Any]:
    """构造结构化拒答（JSON 形态）；文本形态用 to_text() 渲染。"""
    brief = _REASON_BRIEF.get(reason, "无法给出有依据的回答")
    missing = _MISSING_INFO.get(reason, "缺少可支撑回答的官方资料")
    if detail:
        missing = f"{missing}（{detail}）"
    return {
        "refusal": True,
        "reason": reason,
        "message": f"抱歉，{brief}，针对「{question}」暂时无法给出有依据的答案。"
                   "为避免提供不准确的商品信息，本次不作回答。",
        "missing_info": missing,
        "hotline": HOTLINE,
        "escalate": "已为您转人工客服，也可拨打 400-680-9527",
    }


def to_text(refusal: Dict[str, Any]) -> str:
    """渲染拒答的中文文本形态（SSE / 会话记忆 / 报告正文共用）。"""
    return "\n".join([
        str(refusal.get("message", "")).strip(),
        f"缺失资料：{refusal.get('missing_info', '')}",
        f"如您急需帮助：{refusal.get('escalate', '')}（客服热线 {refusal.get('hotline', HOTLINE)}）",
    ])


def log_rejection(question: str, reason: str, task_id: str = "") -> None:
    """拒答审计落盘：data/audit/rejections.jsonl，一行一条 JSON。失败静默。"""
    try:
        settings = get_settings()
        settings.audit_dir.mkdir(parents=True, exist_ok=True)
        payload = {
            "ts": time.time(),
            "question": question,
            "reason": reason,
        }
        if task_id:
            payload["task_id"] = task_id
        with open(settings.audit_dir / "rejections.jsonl", "a", encoding="utf-8") as fh:
            fh.write(json.dumps(payload, ensure_ascii=False, default=str) + "\n")
    except Exception:  # noqa: BLE001 — 审计失败不影响主流程
        pass


__all__ = ["HOTLINE", "build_refusal", "log_rejection", "should_refuse", "to_text"]
