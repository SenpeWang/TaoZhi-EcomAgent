"""Publish gate: never manufacture citations; uncertain advice requires review."""
from __future__ import annotations
import re
from langgraph.types import interrupt
from ..schemas.research import TaskStatus
from .specialists import trusted_evidence

def assess(state):
    report=state.get("report")
    if report is None:return {"passed":False,"reasons":["没有生成报告"]}
    evidence=trusted_evidence(state)
    reasons=[]
    if state.get("metrics",{}).get("refusal_reason"):
        return {"passed":True,"refused":True,"reasons":[]}
    if not evidence:reasons.append("缺少可信原文")
    valid_quotes=[e.quote for e in evidence]
    for c in report.citations:
        if not c.quote or not any(c.quote in q for q in valid_quotes):
            reasons.append("引用无法追溯到已授权原文");break
    allowed={c.index for c in report.citations}
    for section in report.sections:
        inline={int(i) for i in re.findall(r"\[(\d+)\]",section.content)}
        if not section.citation_indices or any(i not in allowed for i in section.citation_indices) or not inline.issubset(allowed):
            reasons.append("段落缺少有效引用");break
    text=report.executive_summary+"\n"+"\n".join(s.content for s in report.sections)
    if any(int(i) not in allowed for i in re.findall(r"\[(\d+)\]",text)):
        reasons.append("正文含不存在的引用")
    facts=" ".join(e.quote for e in evidence)
    numbers=set(re.findall(r"\d+(?:\.\d+)?",re.sub(r"\[\d+\]","",text)))
    if not numbers.issubset(set(re.findall(r"\d+(?:\.\d+)?",facts))):
        reasons.append("答案含原文未支持的数字")
    q=state["task"].question
    model_ids=re.findall(r"\b(?:MC|UV)-\d+\b|\b[A-Z]\d{1,3}\b",q)
    if any(model not in facts for model in model_ids):
        reasons.append("精确型号未出现在已授权资料中，需澄清型号或补充资料")
    stock=state.get("business",{}).get("stock",{})
    if any(k in q for k in ("下单","退款","赔付")) or (
       any(k in q for k in ("库存","实时价格","当前价格")) and not stock.get("as_of")):
        reasons.append("需要订单/库存/价格系统实时核实；知识文档不能执行交易")
    relevant={"spec_query":"product","sku_compare":"product","compat_recommend":"compatibility","after_sales":"after_sales"}
    intent=getattr(state.get("plan"),"intent","")
    for specialist in state.get("specialists",[]):
        if specialist["verdict"] in ("failed","timeout","conflict") or (
            specialist["role"]==relevant.get(intent) and specialist["verdict"]=="unknown"):
            reasons.append(specialist["role"]+"专家未收敛")
    if report.review_required:reasons.append("置信度或引用覆盖率需要人工复核")
    return {"passed":not reasons,"reasons":list(dict.fromkeys(reasons))}

def quality_assessment_node(state,config):
    quality=assess(state)
    quality["manual_review_requested"]=state["task"].require_human_review
    return {"quality":quality}

def quality_gate_node(state,config):
    report=state.get("report")
    quality=state.get("quality") or assess(state)
    if report is None:return {"quality":quality}
    needs=not quality["passed"] or state["task"].require_human_review
    decision=state.get("review_decision","")
    if needs and not decision:
        decision=interrupt({"type":"publish_review","task_id":state["task"].task_id,
                            "quality":quality,"question":state["task"].question})
    if decision=="approve":
        quality={**quality,"publication":"human_approved"}
        report.review_required=False
        report.risk_warnings=list(dict.fromkeys(report.risk_warnings+quality["reasons"]+["已人工复核；仍须遵守业务核实限制"]))
    elif decision:
        state["task"].status=TaskStatus.REJECTED
    return {"quality":quality,"report":report,"review_decision":str(decision)}
