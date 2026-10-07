"""口径交叉辩论：借鉴 Safe Debate Protocol。

流程：
    1) 官方口径辩护方收集官方论据（以商品手册 / 参数表为准，必须附证据）
    2) 用户反馈质疑方收集质疑论据（以详情页评价 / 客服工单为据，必须附证据）
    3) 冲突检测：同一主题下的口径比对，归因口径分歧根因
    4) 裁决（问答质检主管）给出裁决结论与置信度
"""

from __future__ import annotations

from typing import List, Optional, Sequence

from pydantic import BaseModel, ConfigDict, Field

from ..llm import ModelTier, get_llm
from ..llm.json_utils import dump, extract_json
from ..observability import inc, timer
from ..schemas.common import Evidence, Sentiment
from ..schemas.research import ConflictItem, DebateMatrix, DebatePoint

_BULL_PROMPT = """你是商品知识核验中的【官方口径辩护方】。围绕下面的用户问题，以商品手册 / 官方参数表为准，列出 3 条最有力的官方口径论据。

用户问题：{topic}
已收集证据：
{evidence}

要求：
- 每条论据包含 claim（一句话口径主张）、reasoning（推理链）、strength（0-1）、
  evidence（引用上面证据的序号数组，从 0 开始）、source_org（资料来源，如 商品手册/参数表/详情页）。
- 必须基于证据，禁止凭空编造；证据不足时 strength 调低。
输出 JSON：{{"points":[{{"claim":"","reasoning":"","strength":0.8,"evidence":[0],"source_org":""}}]}}
"""

_BEAR_PROMPT = """你是商品知识核验中的【用户反馈质疑方】。围绕下面的用户问题，以详情页评价 / 客服工单 / 实际使用反馈为据，列出 3 条最有力的质疑论据。

用户问题：{topic}
已收集证据：
{evidence}

要求与输出格式同官方口径辩护方（字段名一致）。
"""

_VERDICT_PROMPT = """你是【问答质检主管】，负责裁决官方口径与用户反馈的口径分歧并给出果断结论。

用户问题：{topic}
官方口径论点：
{bull}
用户反馈论点：
{bear}
口径分歧点：
{conflicts}

请输出 JSON：
{{"verdict":"明确给出裁决结论（以官方口径为准/以用户反馈为准/两者一致/证据不足待核），并说明理由",
"bull_score":0-1,"bear_score":0-1,"confidence":0-1}}
禁止以"双方都有道理"为由回避裁决。
"""


class DebateProtocol:
    """安全辩论协议：双边强制举证，质检主管必须给出裁决结论。"""

    def __init__(self, max_rounds: int = 2) -> None:
        self.max_rounds = max_rounds

    def run(self, topic: str, evidence: Sequence[Evidence],
            rounds: int = 1) -> DebateMatrix:
        matrix = DebateMatrix(topic=topic, rounds=0)
        if not get_llm().settings.has_llm:
            return self._rule_based(topic, list(evidence))

        evidence_text = self._format_evidence(evidence)
        for _ in range(max(1, rounds)):
            bull = self._side(_BULL_PROMPT, topic, evidence_text, Sentiment.SUPPORT)
            bear = self._side(_BEAR_PROMPT, topic, evidence_text, Sentiment.REFUTE)
            # 任一方论证失败时按证据立场兜底，保证分歧矩阵是"双侧"的
            if not bull:
                bull = self._points_from_evidence(evidence, Sentiment.SUPPORT)
            if not bear:
                bear = self._points_from_evidence(evidence, Sentiment.REFUTE)
            if not bull and not bear:
                bull = self._points_from_evidence(evidence, None)
            matrix.bull_points = bull
            matrix.bear_points = bear
            matrix.rounds += 1
            inc("debate.round")
        matrix.conflicts = self.detect_conflicts(matrix.bull_points, matrix.bear_points)
        matrix = self._verdict(topic, matrix)
        return matrix

    # ───────── 单边论证 ─────────
    def _side(self, template: str, topic: str, evidence_text: str,
              sentiment: Sentiment) -> List[DebatePoint]:
        prompt = template.format(topic=topic, evidence=evidence_text or "（暂无外部证据，请基于公开常识谨慎论证）")
        with timer("debate.side"):
            try:
                raw = get_llm().chat(prompt, tier=ModelTier.FAST, max_tokens=2500)
            except Exception:  # noqa: BLE001
                return []
        data = extract_json(raw, expect="object")
        if not isinstance(data, dict):
            return []
        points: List[DebatePoint] = []
        for item in (data.get("points") or [])[:4]:
            if not isinstance(item, dict) or not item.get("claim"):
                continue
            refs = item.get("evidence") or []
            picked: List[Evidence] = []
            if isinstance(refs, list):
                for ref in refs:
                    if isinstance(ref, int) and 0 <= ref < len(self._last_evidence):
                        picked.append(self._last_evidence[ref])
            points.append(
                DebatePoint(
                    claim=str(item["claim"]).strip(),
                    reasoning=str(item.get("reasoning", "")).strip()[:600],
                    evidence=picked,
                    strength=float(item.get("strength", 0.5) or 0.5),
                    source_org=str(item.get("source_org", "")),
                )
            )
        return points

    @staticmethod
    def _points_from_evidence(evidence: Sequence[Evidence],
                              sentiment: Optional[Sentiment]) -> List[DebatePoint]:
        """论证生成失败时，按证据立场构造论点，保证辩论双侧完整。"""
        picked = [e for e in evidence if sentiment is None or e.sentiment == sentiment]
        picked = picked[:3] or list(evidence)[:3]
        label = {Sentiment.SUPPORT: "官方口径", Sentiment.REFUTE: "用户质疑"}.get(
            sentiment, "中性观察")
        return [
            DebatePoint(
                claim=f"{label}依据：{e.quote[:80]}",
                reasoning="由证据立场自动构造（LLM 论证未成功返回）",
                evidence=[e],
                strength=min(0.9, 0.3 + 0.2 * (i + 1)),
                source_org=e.doc_name,
            )
            for i, e in enumerate(picked)
        ]

    _last_evidence: List[Evidence] = []

    @staticmethod
    def _format_evidence(evidence: Sequence[Evidence]) -> str:
        DebateProtocol._last_evidence = list(evidence)
        if not evidence:
            return ""
        lines = []
        for index, item in enumerate(evidence[:12]):
            page = f"P{item.page}" if item.page else "全文"
            lines.append(f"[{index}] {item.doc_name} {page}：{item.quote[:160]}")
        return "\n".join(lines)

    # ───────── 口径分歧点检测 ─────────
    def detect_conflicts(self, bull: Sequence[DebatePoint],
                         bear: Sequence[DebatePoint]) -> List[ConflictItem]:
        """同一主题下官方口径与用户反馈的口径比对，产出分歧点列表。"""
        conflicts: List[ConflictItem] = []
        for b in bull[:3]:
            for k in bear[:3]:
                if _claims_conflict(b.claim, k.claim):
                    conflicts.append(
                        ConflictItem(
                            topic=_common_topic(b.claim, k.claim),
                            bull_claim=b.claim,
                            bear_claim=k.claim,
                            root_cause=_guess_root_cause(b, k),
                            resolvable=bool(b.evidence and k.evidence),
                        )
                    )
        if conflicts:
            inc("debate.conflicts", len(conflicts))
        return conflicts[:3]

    # ───────── 裁决 ─────────
    def _verdict(self, topic: str, matrix: DebateMatrix) -> DebateMatrix:
        matrix.bull_score = round(
            sum(p.strength for p in matrix.bull_points) / max(1, len(matrix.bull_points)), 3
        )
        matrix.bear_score = round(
            sum(p.strength for p in matrix.bear_points) / max(1, len(matrix.bear_points)), 3
        )
        prompt = _VERDICT_PROMPT.format(
            topic=topic,
            bull="\n".join(f"- {p.claim}（强度 {p.strength}）" for p in matrix.bull_points),
            bear="\n".join(f"- {p.claim}（强度 {p.strength}）" for p in matrix.bear_points),
            conflicts="\n".join(
                f"- {c.topic}：官方『{c.bull_claim}』vs 用户『{c.bear_claim}』" for c in matrix.conflicts
            ) or "（无显著口径分歧）",
        )
        with timer("debate.verdict"):
            try:
                raw = get_llm().chat(prompt, tier=ModelTier.STRONG, max_tokens=1200)
                data = extract_json(raw, expect="object") or {}
            except Exception:  # noqa: BLE001
                data = {}
        matrix.verdict = str(data.get("verdict", "")).strip() or self._fallback_verdict(matrix)
        matrix.confidence = float(data.get("confidence", 0.5) or 0.5)
        if isinstance(data.get("bull_score"), (int, float)):
            matrix.bull_score = float(data["bull_score"])
        if isinstance(data.get("bear_score"), (int, float)):
            matrix.bear_score = float(data["bear_score"])
        for conflict in matrix.conflicts:
            conflict.resolution = matrix.verdict[:120]
        return matrix

    @staticmethod
    def _fallback_verdict(matrix: DebateMatrix) -> str:
        if matrix.bull_score > matrix.bear_score + 0.1:
            return f"官方口径占优（{matrix.bull_score:.2f} vs {matrix.bear_score:.2f}），倾向以官方参数表 / 手册为准"
        if matrix.bear_score > matrix.bull_score + 0.1:
            return f"用户反馈占优（{matrix.bear_score:.2f} vs {matrix.bull_score:.2f}），需核实详情页评价与客服工单"
        return "官方口径与用户反馈基本一致，维持中性结论并在答案中标注口径分歧"

    def _rule_based(self, topic: str, evidence: List[Evidence]) -> DebateMatrix:
        """无 LLM 时的规则化辩论（保证流水线可用）。"""
        matrix = DebateMatrix(topic=topic, rounds=1)
        support = [e for e in evidence if e.sentiment == Sentiment.SUPPORT]
        refute = [e for e in evidence if e.sentiment == Sentiment.REFUTE]
        matrix.bull_points = [
            DebatePoint(claim=f"{topic}：官方口径存在 {len(support)} 条支持性证据",
                        reasoning="基于检索到的官方口径证据",
                        evidence=support[:3], strength=min(1.0, 0.3 + 0.2 * len(support)))
        ] if support else []
        matrix.bear_points = [
            DebatePoint(claim=f"{topic}：用户反馈存在 {len(refute)} 条质疑/反驳证据",
                        reasoning="基于检索到的用户反馈证据",
                        evidence=refute[:3], strength=min(1.0, 0.3 + 0.2 * len(refute)))
        ] if refute else []
        matrix.bull_score = min(1.0, 0.3 + 0.2 * len(support))
        matrix.bear_score = min(1.0, 0.3 + 0.2 * len(refute))
        matrix.confidence = 0.4
        matrix.verdict = self._fallback_verdict(matrix)
        return matrix


_NEGATIONS = ("不会", "难以", "无法", "推迟", "延期", "受限", "下滑", "下降", "不足", "风险", "承压")


def _claims_conflict(bull_claim: str, bear_claim: str) -> bool:
    if not bull_claim or not bear_claim:
        return False
    shared = set(bull_claim) & set(bear_claim)
    if len(shared) < 6:
        return False
    return any(n in bear_claim for n in _NEGATIONS) or any(n in bull_claim for n in ("将", "会", "加速"))


def _common_topic(a: str, b: str) -> str:
    common = "".join(sorted(set(a) & set(b)))
    return common[:20] or "综合判断"


def _guess_root_cause(bull: DebatePoint, bear: DebatePoint) -> str:
    if bull.source_org and bear.source_org and bull.source_org != bear.source_org:
        return f"来源口径差异（{bull.source_org} vs {bear.source_org}）"
    if not bull.evidence or not bear.evidence:
        return "证据充分性差异"
    return "资料版本或批次口径不同"
