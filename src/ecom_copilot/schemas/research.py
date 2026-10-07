"""商品问答任务 / 假设追踪 / 口径交叉核验 / 报告的数据模型。"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field

from .common import Citation, Evidence, Sentiment, new_id
from .graph import EntityMention, SupplyTriple


class ResearchDepth(str, Enum):
    QUICK = "quick"          # 1 轮取证，不做辩论
    STANDARD = "standard"    # 取证 + 假设验证 + 单轮辩论
    DEEP = "deep"            # 多轮假设收敛 + 多轮辩论 + 人工复核


class TaskStatus(str, Enum):
    PENDING = "pending"
    PROCESSING = "processing"
    AWAITING_REVIEW = "awaiting_review"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    REJECTED = "rejected"


class ResearchTask(BaseModel):
    """POST /api/qa/task 的请求体。"""

    model_config = ConfigDict(extra="forbid")

    task_id: str = Field(default_factory=lambda: new_id("task"))
    question: str = Field(min_length=1, max_length=4000)
    category: str = ""                     # 商品类目（钢化膜/手机壳/膜切机/UV打印机…）
    depth: ResearchDepth = ResearchDepth.STANDARD
    user_id: str = "anonymous"
    org_tag: str = "default"
    product_line: str = ""                 # 产品线（膜切机系列/UV打印机系列…）
    brand: str = ""                        # 品牌（如膜法工坊 MofaLab）
    sku_id: str = Field(default="",max_length=64,pattern=r"^[A-Za-z0-9_-]*$")
    order_id: str = Field(default="",max_length=64,pattern=r"^[A-Za-z0-9_-]*$")
    max_sources: int = Field(default=12,ge=1,le=30)
    require_human_review: bool = False     # 低置信度结论强制人工复核（human-in-the-loop）
    created_at: datetime = Field(default_factory=datetime.utcnow)
    status: TaskStatus = TaskStatus.PENDING
    estimated_time: int = 900
    error: str = ""

    @property
    def keywords(self) -> List[str]:
        kws = [self.category, self.product_line, self.brand]
        kws.append(self.question)
        return [k for k in kws if k]


class TaskPlan(BaseModel):
    """Supervisor 的任务拆解结果。"""

    model_config = ConfigDict(extra="forbid")

    intent: str = ""                       # spec_query | sku_compare | compat_recommend | after_sales
    sub_questions: List[str] = Field(default_factory=list)
    target_sources: List[str] = Field(default_factory=list)
    focus_areas: List[str] = Field(default_factory=list)
    route: List[str] = Field(default_factory=list)   # 需要执行的 Agent 序列
    rationale: str = ""


# ───────────────────────── 假设追踪（hypothesis_tracker） ─────────────────────────


class HypothesisStatus(str, Enum):
    """pending → investigating → confirmed / refuted / inconclusive"""

    PENDING = "pending"
    INVESTIGATING = "investigating"
    CONFIRMED = "confirmed"
    REFUTED = "refuted"
    INCONCLUSIVE = "inconclusive"


ALLOWED_TRANSITIONS: Dict[str, List[str]] = {
    HypothesisStatus.PENDING.value: [
        HypothesisStatus.INVESTIGATING.value,
        HypothesisStatus.INCONCLUSIVE.value,
    ],
    HypothesisStatus.INVESTIGATING.value: [
        HypothesisStatus.CONFIRMED.value,
        HypothesisStatus.REFUTED.value,
        HypothesisStatus.INCONCLUSIVE.value,
        HypothesisStatus.INVESTIGATING.value,
    ],
    HypothesisStatus.CONFIRMED.value: [HypothesisStatus.INVESTIGATING.value],
    HypothesisStatus.REFUTED.value: [HypothesisStatus.INVESTIGATING.value],
    HypothesisStatus.INCONCLUSIVE.value: [
        HypothesisStatus.INVESTIGATING.value,
        HypothesisStatus.CONFIRMED.value,
        HypothesisStatus.REFUTED.value,
    ],
}


class Hypothesis(BaseModel):
    id: str = Field(default_factory=lambda: new_id("hyp"))
    task_id: str = ""
    hypothesis_text: str = ""
    category: str = ""                      # 参数口径 | 兼容适配 | 售后政策
    status: HypothesisStatus = HypothesisStatus.PENDING
    evidence: List[Evidence] = Field(default_factory=list)
    confidence: float = 0.0
    reasoning: str = ""
    round_created: int = 0
    round_updated: int = 0
    transitions: List[Dict[str, Any]] = Field(default_factory=list)
    needs_human_review: bool = False
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)

    def support_count(self) -> int:
        return sum(1 for e in self.evidence if e.sentiment == Sentiment.SUPPORT)

    def refute_count(self) -> int:
        return sum(1 for e in self.evidence if e.sentiment == Sentiment.REFUTE)

    def coverage(self) -> float:
        """引用溯源覆盖率：带完整出处的证据占比。"""
        if not self.evidence:
            return 0.0
        ok = sum(1 for e in self.evidence if e.doc_name and e.quote)
        return ok / len(self.evidence)


# ───────────────────────── 口径交叉核验（多来源说法对比） ─────────────────────────


class DebatePoint(BaseModel):
    model_config = ConfigDict(extra="forbid")

    claim: str = ""
    reasoning: str = ""
    evidence: List[Evidence] = Field(default_factory=list)
    strength: float = 0.5        # 0-1
    source_org: str = ""


class ConflictItem(BaseModel):
    """观点冲突检测（PRD E4）。"""

    model_config = ConfigDict(extra="forbid")

    topic: str = ""
    bull_claim: str = ""
    bear_claim: str = ""
    root_cause: str = ""          # 口径差异 / 时间窗口 / 数据样本 / 立场差异
    resolvable: bool = True
    resolution: str = ""


class DebateMatrix(BaseModel):
    """口径分歧与交叉核验矩阵 + 证据溯源。"""

    model_config = ConfigDict(extra="forbid")

    topic: str = ""
    rounds: int = 0
    bull_points: List[DebatePoint] = Field(default_factory=list)
    bear_points: List[DebatePoint] = Field(default_factory=list)
    conflicts: List[ConflictItem] = Field(default_factory=list)
    verdict: str = ""
    bull_score: float = 0.0
    bear_score: float = 0.0
    confidence: float = 0.0


# ───────────────────────── 报告 ─────────────────────────


class ReportSection(BaseModel):
    model_config = ConfigDict(extra="forbid")

    heading: str = ""
    content: str = ""
    citation_indices: List[int] = Field(default_factory=list)


class KeyProduct(BaseModel):
    """报告中的重点商品条目。"""

    model_config = ConfigDict(extra="forbid")

    name: str = ""
    model: str = ""
    highlights: str = ""
    citation_indices: List[int] = Field(default_factory=list)


class ResearchReport(BaseModel):
    """结构化问答/对比报告（Pydantic 强 Schema 约束，PRD F1/F2/F3）。"""

    model_config = ConfigDict(extra="forbid")

    report_id: str = Field(default_factory=lambda: new_id("rpt"))
    task_id: str = ""
    title: str = ""
    question: str = ""
    executive_summary: str = ""
    sections: List[ReportSection] = Field(default_factory=list)
    chain_analysis: Dict[str, Any] = Field(default_factory=dict)   # 参数对比 / 适配结论
    key_products: List[KeyProduct] = Field(default_factory=list)
    hypotheses_summary: List[Dict[str, Any]] = Field(default_factory=list)
    debate_matrix: Optional[DebateMatrix] = None
    risk_warnings: List[str] = Field(default_factory=list)
    recommendations: List[str] = Field(default_factory=list)
    citations: List[Citation] = Field(default_factory=list)
    entities: List[EntityMention] = Field(default_factory=list)
    triples: List[SupplyTriple] = Field(default_factory=list)
    confidence: float = 0.0
    citation_coverage: float = 0.0
    review_required: bool = False
    generated_at: datetime = Field(default_factory=datetime.utcnow)
    model_used: str = ""
    elapsed_seconds: float = 0.0

    def field_fill_rate(self) -> float:
        """结构化字段填充率（PRD 7.2 ≥ 95%）。"""
        fields = [
            self.title,
            self.executive_summary,
            self.sections,
            self.key_products,
            self.risk_warnings,
            self.recommendations,
            self.citations,
        ]
        filled = sum(1 for f in fields if f)
        return filled / len(fields)

    def to_markdown(self) -> str:
        lines: List[str] = [f"# {self.title}", ""]
        if self.executive_summary:
            lines += ["## 摘要", self.executive_summary, ""]
        for sec in self.sections:
            lines += [f"## {sec.heading}", sec.content, ""]
            if sec.citation_indices:
                refs = " ".join(
                    f"[{i}]" for i in sorted(set(sec.citation_indices))
                )
                lines += [f"> 引用：{refs}", ""]
        if self.key_products:
            lines += ["## 重点商品", ""]
            lines.append("| 商品 | 型号 | 亮点 |")
            lines.append("| --- | --- | --- | --- |")
            for c in self.key_products:
                lines.append(
                    f"| {c.name} | {c.model} | {c.highlights} |"
                )
            lines.append("")
        if self.debate_matrix and (self.debate_matrix.bull_points or self.debate_matrix.bear_points):
            dm = self.debate_matrix
            lines += ["## 口径分歧与交叉核验", ""]
            lines.append("| 维度 | 口径A | 口径B |")
            lines.append("| --- | --- | --- |")
            for i in range(max(len(dm.bull_points), len(dm.bear_points))):
                b = dm.bull_points[i].claim if i < len(dm.bull_points) else "-"
                k = dm.bear_points[i].claim if i < len(dm.bear_points) else "-"
                lines.append(f"| 说法{i + 1} | {b} | {k} |")
            lines += ["", f"**核验结论**：{dm.verdict}", ""]
        if self.hypotheses_summary:
            lines += ["## 假设验证结论", ""]
            lines.append("| 假设 | 状态 | 置信度 | 关键证据 |")
            lines.append("| --- | --- | --- | --- |")
            for h in self.hypotheses_summary:
                lines.append(
                    f"| {h.get('hypothesis_text', '')} | {h.get('status', '')} | "
                    f"{h.get('confidence', 0):.2f} | {h.get('key_evidence', '')} |"
                )
            lines.append("")
        if self.risk_warnings:
            lines += ["## 风险提示", ""]
            lines += [f"- {r}" for r in self.risk_warnings]
            lines.append("")
        if self.recommendations:
            lines += ["## 建议", ""]
            lines += [f"- {r}" for r in self.recommendations]
            lines.append("")
        if self.citations:
            lines += ["## 引用来源", ""]
            for c in self.citations:
                page = f"P{c.page}" if c.page else "全文"
                link = f" ({c.url})" if c.url else ""
                lines.append(f"[{c.index}] {c.doc_name} {page}{link}")
        return "\n".join(lines)
