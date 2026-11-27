"""专职智能体并发推理与事实/数字/引文核验节点。"""
from __future__ import annotations

import json
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing

from openinference.semconv.trace import SpanAttributes
from opentelemetry import context as otel_context
from opentelemetry.trace import StatusCode
from pydantic import BaseModel, Field

from taozhi.ai.client import LLMClient, ModelTier
from taozhi.ai.observability import usage_scope
from taozhi import config as taozhi_config
from taozhi.domain.agent_catalog import agent_display_name
from taozhi.domain.privacy import redact
from taozhi.domain.rag_verification import SPECIALIST_AGENTS, cited_paragraphs, conflicting_findings, fact_subjects, is_finance_evidence, missing_models, norm_text, supported_finding_text
from taozhi.domain.text_guard import detect_injection
from taozhi.infrastructure.persistence.job_repo import count_model_calls, insert_model_call, lock_running_job, update_model_call
from taozhi.infrastructure.persistence.database import database_connection
from taozhi.observability.tracing import node_span, record_diagnostic
from taozhi.application.common.task_guard import authorized_running_task, empty_token_usage, merge_token_usage, validate_task_execution
from taozhi.application.services.task_output import publish_answer_delta
from .phases import RagPhase
from .prompts import RAG_PROMPTS
from .state import State

class _SpecialistFinding(BaseModel):
    """单个核验结论：证据下标、逐字原句与可核验事实。"""
    evidence_index: int = Field(description="证据下标，从 0 开始，必须对应给定证据条目")
    quote: str = Field(min_length=1, max_length=4000, description="能直接回答子问的完整原句，逐字保留适用机型、条件、否定及例外")
    claim: str = Field(min_length=1, max_length=4000, description="普通事实填与 quote 相同的原句，金额或比例换算仅填四则算式及等号后的结果")


class SpecialistOutput(BaseModel):
    """专家智能体结构化输出契约。"""

    findings: list[_SpecialistFinding] = Field(default_factory=list, description="经原文逐字引用支撑的核验结论列表")
    missing: list[str] = Field(default_factory=list, description="给定资料中缺失且无法核验的要点")


def extract_claims(state: State) -> dict:
    """并发调度专家，从各自获准使用的资料中选取待核验的候选结论。"""
    validate_task_execution(state, "专业智能体多角色协同核验", 45)
    evidence = state["evidence"]
    settings = taozhi_config.get_settings()
    if not settings.api_key:
        return {"findings": [], "missing": ["专业核验模型尚未配置。"], "verification_issue": "model_unconfigured",
                "phase": RagPhase.CLAIMS_EXTRACTED.value}

    config = taozhi_config.load_config()
    selected_agents = state["selected_agents"]
    with database_connection() as c:
        spent = count_model_calls(c, state["job_id"])
    if spent >= config.max_model_calls:
        return {"findings": [], "missing": ["本任务已达到模型调用预算。"], "model_calls": 0,
                "usage": [], "verification_issue": "budget_exhausted",
                "phase": RagPhase.CLAIMS_EXTRACTED.value}
    client_settings = settings.model_copy(
        update={
            "llm_timeout": config.specialist_timeout,
            "llm_max_retries": 0,
            "llm_empty_retries": 0,
            "llm_max_tokens": config.specialist_max_tokens,
            "llm_output_cap": max(config.specialist_max_tokens, settings.llm_output_cap),
        }
    )

    def _run_specialist_agent(specialist_id: str) -> dict:
        """运行单个专家智能体的完整核验流程并登记模型调用。

        Args:
            specialist_id: 专家角色编号。

        Returns:
            含 findings、missing 与本次调用用量统计的专家结果。
        """
        agent_findings, agent_missing = [], []
        specialist_name = agent_display_name(specialist_id)
        indices = [i for i, e in enumerate(evidence) if specialist_id != "finance" or is_finance_evidence(e)]
        if specialist_id == "finance" and not indices:
            return {
                "findings": [],
                "missing": ["缺少已授权的财务或经营分析资料，不能推测经营数据"],
                "usage": {"role": specialist_id, "elapsed_ms": 0, "outcome": "skipped"},
            }

        quote_cap = config.quote_max_chars
        with node_span("context.prepare_specialist", "CHAIN", {"context.evidence_count": len(indices)}) as span:
            prompt = json.dumps(
                {
                    "问题": redact(state["question"]),
                    "授权资料": [{"index": i, "quote": redact(e["quote"][:quote_cap]), "title": redact(e["title"])} for i, e in enumerate(evidence) if i in indices],
                },
                ensure_ascii=False,
            )
            if span is not None:
                span.set_attribute("context.output_chars", len(prompt))
        system, _ = RAG_PROMPTS.render(
            "rag.specialist.extract",
            {
                "specialist_name": specialist_name,
                "specialist_description": SPECIALIST_AGENTS[specialist_id][1],
            },
        )

        with database_connection() as c:
            authorized_running_task(c, state, external=True)
            current = lock_running_job(c, state["job_id"], state["run_version"])
            if count_model_calls(c, state["job_id"]) >= config.max_model_calls:
                return {"findings": [], "missing": ["本任务已达到模型调用预算。"], "usage": {"role": specialist_id, "elapsed_ms": 0, "outcome": "budget_exhausted"}}
            call_id = insert_model_call(c, current["tenant_id"], state["job_id"], specialist_id)

        started = time.monotonic()
        outcome = "ok"
        with usage_scope() as specialist_usage:
            try:
                with closing(LLMClient(client_settings, allow_overseas=False)) as client:
                    data = client.chat_json(prompt, system=system or "", tier=ModelTier.FAST, retries=3, schema=SpecialistOutput, max_tokens=config.specialist_max_tokens)
                if isinstance(data, SpecialistOutput):
                    data = data.model_dump()
                if not isinstance(data, dict):
                    raise ValueError()
                for f in (data.get("findings") or [])[:8]:
                    i = f.get("evidence_index")
                    if type(i) is int and i in indices:
                        agent_findings.append(dict(role=specialist_id, claim=f.get("claim"), evidence_index=i, quote=f.get("quote")))
                    else:
                        agent_missing.append(f"{specialist_name}未找到对应的授权引用。")
                if data.get("missing"):
                    agent_missing.append(f"{specialist_name}所获资料未覆盖该角色的全部子问。")
            except Exception as exc:
                record_diagnostic("workflow.summary", "fallback", exc)
                agent_missing.append(specialist_name + "模型暂时不可用。")
                outcome = "failed"

        elapsed = int((time.monotonic() - started) * 1000)
        usage_totals = specialist_usage.to_dict()
        if call_id:
            try:
                with database_connection() as c:
                    update_model_call(c, call_id, elapsed, usage_totals["prompt_tokens"], usage_totals["completion_tokens"], outcome)
            except Exception as exc:
                record_diagnostic("storage.write", "skipped", exc)
        return {
            "findings": agent_findings,
            "missing": agent_missing,
            "usage": {
                "role": specialist_id,
                "elapsed_ms": elapsed,
                "outcome": outcome,
                "prompt_tokens": usage_totals["prompt_tokens"],
                "completion_tokens": usage_totals["completion_tokens"],
                "total_tokens": usage_totals["total_tokens"],
                "cost_usd": usage_totals["cost_usd"],
                "llm_attempts": usage_totals["llm_attempts"],
                "llm_errors": usage_totals["llm_errors"],
                "failovers": usage_totals["failovers"],
                "json_retries": usage_totals["json_retries"],
            },
        }

    parent_context = otel_context.get_current()

    def run_specialist(specialist_id: str) -> dict:
        """在独立 span 内运行单个专家，并标注失败状态。

        Args:
            specialist_id: 专家角色编号。

        Returns:
            该专家的核验结果。
        """
        attrs = {SpanAttributes.AGENT_NAME: specialist_id}
        with node_span("rag.specialist", "AGENT", attrs, context=parent_context) as span:
            result = _run_specialist_agent(specialist_id)
            if span is not None and result["usage"]["outcome"] == "failed":
                span.set_attribute("error.type", "SpecialistFailure")
                span.set_status(StatusCode.ERROR)
            return result

    all_findings, all_missing, all_usage = [], [], []
    actual_calls = 0
    token_usage = state.get("token_usage") or empty_token_usage()
    with ThreadPoolExecutor(max_workers=min(len(selected_agents), max(1, config.specialist_concurrency))) as pool:
        futures = [pool.submit(run_specialist, role) for role in selected_agents]
        for future in futures:
            res = future.result()
            all_findings.extend(res["findings"])
            all_missing.extend(res["missing"])
            usage_record = res["usage"]
            all_usage.append(usage_record)
            if usage_record.get("outcome") in ("ok", "failed"):
                actual_calls += 1
                token_usage = merge_token_usage(token_usage, usage_record["role"], usage_record)

    outcomes = {record["outcome"] for record in all_usage}
    degraded = {
        **(state.get("degraded") or {}),
        "json_retry_used": token_usage.get("json_retries", 0) > 0,
        "llm_failover_used": token_usage.get("failovers", 0) > 0,
        "specialist_failed": "failed" in outcomes,
    }
    result = dict(findings=all_findings, missing=list(dict.fromkeys(all_missing)), model_calls=actual_calls, usage=all_usage, token_usage=token_usage, degraded=degraded,
                  phase=RagPhase.CLAIMS_EXTRACTED.value)
    if "failed" in outcomes:
        result["verification_issue"] = "model_unavailable"
    elif "budget_exhausted" in outcomes:
        result["verification_issue"] = "budget_exhausted"
    return result


def verify_claims(state: State) -> dict:
    """逐条核验事实与引文，发布允许预览的结论并保留通过核验的事实。"""
    _, job = validate_task_execution(state, "事实、数字与引用核验", 82)
    review = job["payload"].get("require_review", False)
    hard = state["hard_block"]
    candidates = []
    missing = list(state.get("missing", ()))
    authorized = {
        index: {**evidence, "quote": redact(evidence["quote"]), "title": redact(evidence["title"])}
        for index, evidence in enumerate(state["evidence"])
        if not detect_injection(evidence["quote"])[0]
    }
    for finding in state["findings"]:
        index, claim, quote = finding.get("evidence_index"), finding.get("claim"), finding.get("quote")
        verified = None
        if type(index) is int and index in authorized and isinstance(claim, str) and isinstance(quote, str):
            evidence = authorized[index]
            verified = supported_finding_text(state["question"], claim.strip(), quote.strip(), evidence["quote"], evidence["title"])
        if verified is None:
            missing.append("部分候选结论缺少能直接回答问题的授权原文。")
        else:
            candidates.append({**finding, "claim": verified})

    candidates, conflicts = conflicting_findings(authorized.items(), candidates)
    missing.extend(conflicts)
    findings = []
    seen = set()
    for finding in candidates:
        claim_key = norm_text(finding["claim"])
        if claim_key not in seen:
            findings.append(finding)
            seen.add(claim_key)
    can_stream = not (review or hard or state["external_blocked"])
    if can_stream:
        for paragraph in cited_paragraphs(state["question"], state["evidence"], findings):
            publish_answer_delta(state["job_id"], state["run_version"], paragraph, state["sources"])
    subjects = fact_subjects(state["question"])
    if len(subjects) > 1:
        covered = fact_subjects("\n".join(f["claim"] for f in findings))
        for item in state.get("business", []):
            if item.get("available"):
                record = item["record"]
                covered.update(label for key, label in (("available", "库存"), ("price_minor", "价格"), ("status", "订单状态")) if key in record)
        missing.extend(f"缺少可核验的{label}资料。" for label in sorted(subjects - covered))
    missing.extend(f"缺少机型 {model} 的对应依据。" for model in missing_models(state["question"], state["evidence"], findings))
    return dict(findings=findings, missing=list(dict.fromkeys(missing)), review=review and not hard, hard_block=hard,
                phase=RagPhase.CLAIMS_VERIFIED.value)
