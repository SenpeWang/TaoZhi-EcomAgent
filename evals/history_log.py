"""评测台账增量追加记录器（只追加不覆盖，保留全部历史文档与实验数据）。"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any


def _collect_tech_stack() -> dict[str, str]:
    """从当前运行配置动态读取技术栈、实际主通道与故障转移顺序。"""
    from taozhi.ai.router import build_model_endpoints
    from taozhi.config import get_settings, load_config

    settings = get_settings()
    cfg = load_config()
    endpoints = build_model_endpoints(settings)
    primary = endpoints[0] if endpoints else None
    fallback = " -> ".join(f"{ep.endpoint_id}({ep.model})" for ep in endpoints[1:]) or "无"
    return {
        "orchestration": "LangGraph + FastAPI + PostgreSQL (pgvector)",
        "llm_channel": (f"{primary.label}（{primary.endpoint_id}，{primary.model}）"
                        if primary is not None else "未配置模型通道"),
        "llm_fallback": fallback,
        "llm_model": primary.model if primary is not None else settings.model,
        "llm_fast_model": settings.llm_fast_model,
        "llm_strong_model": settings.llm_strong_model,
        "retrieval_stack": (
            f"ES={'on' if cfg.es_enabled else 'off'} + pgvector HNSW + "
            f"AdaptiveHyDE={'on' if cfg.adaptive_hyde else 'off'} + "
            f"Rerank={'on' if cfg.rerank_enabled else 'off'}"
        ),
        "semantic_cache": f"threshold={cfg.semantic_cache_threshold}, ttl={cfg.semantic_cache_ttl}s",
    }


def _stack_header_lines(stack: dict[str, str], report_path: Path, extra_line: str) -> list[str]:
    """生成评测记录共用的技术栈与报告归档信息行。"""
    return [
        extra_line,
        f"- **报告归档**：`{_rel_path(report_path)}`",
        f"- **编排与存储栈**：`{stack['orchestration']}`",
        f"- **模型主通道**：{stack['llm_channel']}（Fast `{stack['llm_fast_model']}` / Strong `{stack['llm_strong_model']}`）",
        f"- **故障转移顺序**：`{stack['llm_fallback']}`",
    ]


def _append_markdown_block(log_path: Path, block: str) -> Path:
    """以只追加模式写入 Markdown 片段，严禁覆盖已有记录。"""
    log_path.parent.mkdir(parents=True, exist_ok=True)
    prefix = ""
    if log_path.exists() and log_path.stat().st_size > 0:
        with log_path.open("rb") as rf:
            rf.seek(-1, os.SEEK_END)
            last_byte = rf.read(1)
        prefix = "\n" if last_byte == b"\n" else "\n\n"
    with log_path.open("a", encoding="utf-8") as wf:
        wf.write(prefix + block.rstrip() + "\n")
    return log_path


def _fmt_pct(value: Any) -> str:
    return f"{value:.1%}" if isinstance(value, (int, float)) else "-"


def _fmt_num(value: Any, digits: int = 3) -> str:
    return f"{value:.{digits}f}" if isinstance(value, (int, float)) else "-"


def _rel_path(path: Path) -> str:
    root = Path(__file__).resolve().parents[1]
    try:
        return path.resolve().relative_to(root).as_posix()
    except ValueError:
        return path.name


def append_rag_eval_record(
    result: dict[str, Any],
    report_path: Path,
    log_path: Path | None = None,
) -> Path:
    """将单次 RAG 评测的元信息、技术栈与实测指标增量追加到测评记录。"""
    target = log_path or (Path(__file__).resolve().parent / "rag_eval" / "测评记录.md")
    stack = _collect_tech_stack()
    cfg = result.get("config") or {}
    ret = result.get("retrieval") or {}
    adv = result.get("adversarial") or {}
    beh = result.get("behavior") or {}
    six = result.get("six_metrics") or {}

    lines = [
        "---",
        "",
        f"## 自动评测记录（{result.get('at', '-')} · Git `{result.get('git', '-')}`）",
        "",
        "### 1. 测试元信息与技术栈",
        f"- **测试时间**：`{result.get('at', '-')}` | **Git SHA**：`{result.get('git', '-')}`",
        *_stack_header_lines(
            stack,
            report_path,
            f"- **运行配置**：`repeat={cfg.get('repeat', 1)}`, `concurrency={cfg.get('concurrency', 1)}`, `corpus_scope={cfg.get('corpus_scope', 'all')}`",
        ),
        f"- **检索与缓存配置**：`{stack['retrieval_stack']}`；`{stack['semantic_cache']}`",
        "",
        "### 2. 本次实测核心指标",
        "",
        "| 层级 | 核心指标 | 实测数值 |",
        "|---|---|---|",
    ]
    if ret:
        lat = ret.get("latency") or {}
        lines.append(
            f"| **检索层 (`n={ret.get('n', '-')}`)** | `Hit@1 / @3 / @8` · `Recall@8` · `MRR` | "
            f"`{_fmt_num(ret.get('hit@1'))}` / `{_fmt_num(ret.get('hit@3'))}` / `{_fmt_num(ret.get('hit@8'))}` · "
            f"`{_fmt_num(ret.get('recall@8'))}` · `{_fmt_num(ret.get('mrr'))}` (`p50={_fmt_num(lat.get('p50_s'), 2)}s`, `p95={_fmt_num(lat.get('p95_s'), 2)}s`) |"
        )
    if adv:
        lines.append(
            f"| **对抗层 (`n={adv.get('n', '-')}`)** | `pass_rate` · 金丝雀泄漏 | "
            f"`{_fmt_pct(adv.get('pass_rate'))}` · `{adv.get('canary_leaked', 0)}` |"
        )
    if beh:
        lines.append(
            f"| **行为层 (`n={beh.get('n', '-')}`)** | `accuracy` · `relevant` · `faithfulness_micro` | "
            f"`{_fmt_pct(beh.get('accuracy'))}` · `{_fmt_pct(beh.get('relevant_rate'))}` · `{_fmt_num(beh.get('faithfulness_micro'))}` |"
        )
    sut_cost = (six.get("sut_token_cost") or {}) if isinstance(six, dict) else {}
    if sut_cost:
        lines.append(
            f"| **Token 与成本** | SUT `total_tokens` / `cost_usd` | "
            f"`{sut_cost.get('total_tokens', 0)}` / `${_fmt_num(sut_cost.get('cost_usd'), 4)}` |"
        )
    lines.append("")
    return _append_markdown_block(target, "\n".join(lines))


def append_agent_eval_record(
    report: dict[str, Any],
    report_path: Path,
    log_path: Path | None = None,
) -> Path:
    """将单次 Agent 调研评测的元信息、技术栈与实测指标增量追加到测评记录。"""
    target = log_path or (Path(__file__).resolve().parent / "agent_eval" / "测评记录.md")
    stack = _collect_tech_stack()
    gate = report.get("gate") or {}
    metrics = report.get("metrics") or {}
    blockers = gate.get("blocking_case_ids") or []
    tool_acc = (metrics.get("tool_selection_accuracy") or {}).get("rate")
    req_cov = (metrics.get("required_tool_coverage") or {}).get("rate")
    fact_cov = (metrics.get("reference_fact_coverage") or {}).get("rate")

    lines = [
        "---",
        "",
        f"## 自动评测记录（{report.get('timestamp', '-')} · Run `{report.get('run_id', '-')}`）",
        "",
        "### 1. 测试元信息与技术栈",
        *_stack_header_lines(
            stack,
            report_path,
            f"- **评测模式**：`{report.get('mode', '-')}` | **Git SHA**：`{report.get('git_sha') or '-'}`",
        ),
        "",
        "### 2. 本次实测核心指标",
        "",
        "| 指标项 | 实测数值 |",
        "|---|---|",
        f"| **门禁结果 (`gate.passed`)** | `{'PASS' if gate.get('passed') else 'FAIL'}`（阻塞用例：`{', '.join(blockers) if blockers else '无'}`） |",
        f"| **平均分 (`mean_score`)** | `{_fmt_num(report.get('mean_score'), 4)}` |",
        f"| **用例统计 (`selected / passed / failed / unassessable`)** | `{metrics.get('selected', '-')}` / `{metrics.get('passed', '-')}` / `{metrics.get('failed', '-')}` / `{metrics.get('unassessable', '-')}` |",
        f"| **工具选型 / 必选覆盖 / 事实命中率** | `tool={_fmt_pct(tool_acc)}` · `required={_fmt_pct(req_cov)}` · `fact={_fmt_pct(fact_cov)}` |",
        "",
    ]
    return _append_markdown_block(target, "\n".join(lines))
