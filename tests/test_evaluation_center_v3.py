"""评测中心 V3：数据集版本、算法版本、基线、原始报告、指标趋势与评测任务。"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import importlib.util
import json
from pathlib import Path

import pytest

from fastapi.testclient import TestClient

from taozhi.application.services import evaluation_service
from taozhi.infrastructure.persistence import evaluation_repo
from taozhi.infrastructure.persistence.database import database_connection, fetch_all
from taozhi.server.api import app

PROJECT = Path(__file__).resolve().parents[1]
DATASET_ID = "agent_research-agent_v1_20261123"


def _truncate_eval() -> None:
    """只清空评测中心写入，保留租户夹具供其它测试文件复用。"""
    with database_connection() as conn:
        conn.execute(
            "TRUNCATE eval_audit_logs, eval_baselines, eval_run_reports, eval_case_results, "
            "eval_runs, eval_datasets, eval_profiles CASCADE"
        )


def _truncate_all() -> None:
    """清空租户与评测中心写入，供本文件用例重新初始化夹具。"""
    with database_connection() as conn:
        conn.execute(
            "TRUNCATE eval_audit_logs, eval_baselines, eval_run_reports, eval_case_results, "
            "eval_runs, eval_datasets, eval_profiles, tenants CASCADE"
        )
        conn.execute("TRUNCATE login_attempts")


@pytest.fixture(autouse=True)
def _clean_eval_center():
    """每个用例结束后只清理评测中心数据，避免全局数据集资产影响其它测试。"""
    yield
    _truncate_eval()


def _setup() -> None:
    """初始化演示企业与四种现有正式账号，清空评测中心写入痕迹。"""
    spec = importlib.util.spec_from_file_location("v3_setup", PROJECT / "scripts/manage_database.py")
    setup = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(setup)
    _truncate_all()
    setup.seed_test()


def _login(username: str) -> TestClient:
    client = TestClient(app)
    response = client.post("/api/auth/login", json={"username": username, "password": "123456"})
    assert response.status_code == 200, response.text
    client.headers["X-CSRF-Token"] = response.json()["csrf_token"]
    return client


def _owner(username: str) -> dict:
    with database_connection() as conn:
        return fetch_all(conn, "SELECT id, tenant_id FROM users WHERE username=%s", (username,))[0]


def _register_dataset() -> None:
    """登记一个 V1 Agent 数据集版本（用例内容足够覆盖分页与预览）。"""
    cases = [
        {"id": f"AGENT-T-{index:03d}", "name": f"用例 {index}", "category": "sourcing",
         "suite": "benchmark", "execution_mode": "live", "prompt": f"问题 {index}"}
        for index in range(1, 6)
    ]
    with database_connection() as conn:
        evaluation_repo.upsert_dataset(conn, {
            "dataset_id": DATASET_ID,
            "name": "agent_research",
            "eval_kind": "agent",
            "version": "agent_v1_20261123",
            "description": "Agent 调研基准 V1",
            "source": "live",
            "suite": "agent_research",
            "content_sha256": "c" * 64,
            "case_count": 22,
            "cases_payload": cases,
            "metadata": {
                "modes": ["live"],
                "scopes": ["full"],
                "files": {"research_cases.json": _research_cases_sha()},
            },
        })


def _research_cases_sha() -> str:
    """返回仓库内 Agent 基准用例文件的 sha256，用于数据集文件透明化用例。"""
    payload = (PROJECT / "evals/datasets/agent_v1_20261123/research_cases.json").read_bytes()
    return hashlib.sha256(payload).hexdigest()


def _insert_run(owner: dict, run_id: str, *, day: int, pass_rate: float, gate: bool) -> None:
    """直接以仓储写入一个批次，避免依赖评测运行器。"""
    report_digest = hashlib.sha256(run_id.encode()).hexdigest()
    report = {
        "run_id": run_id,
        "created_at": datetime(2026, 11, day, 4, 0, tzinfo=timezone.utc),
        "mode": "live",
        "eval_kind": "agent",
        "scope": "full",
        "git_sha": "a1b2c3d4",
        "dataset_sha256": "d" * 64,
        "judges_sha256": "j" * 64,
        "report_sha256": report_digest,
        "baseline_sha256": None,
        "dataset_id": DATASET_ID,
        "dataset_name": "agent_research",
        "dataset_version": "agent_v1_20261123",
        "dataset_description": "Agent 调研基准 V1",
        "dataset_suite": "agent_research",
        "dataset_content_sha256": "c" * 64,
        "dataset_case_count": 22,
        "cases_payload": [],
        "dataset_metadata": {"modes": ["live"], "scopes": ["full"]},
        "schema_version": 4,
        "metrics": {"provenance": {"comparable": True}, "task_completion": {"rate": pass_rate}},
        "gate_passed": gate,
        "passed_cases": 1 if gate else 0,
        "failed_cases": 0 if gate else 1,
        "unassessable_cases": 0,
        "pass_rate": pass_rate,
        "mean_score": pass_rate,
        "latency_p50_ms": 120,
        "latency_p95_ms": 240,
        "total_tokens": 500,
        "judges": [],
    }
    rows = [{
        "case_id": f"AGENT-T-00{1 if gate else 2}",
        "case_sha256": hashlib.sha256(run_id.encode()).hexdigest(),
        "name": "MOQ 阶梯价",
        "category": "sourcing",
        "suite": "benchmark",
        "status": "passed" if gate else "failed",
        "score": 1.0 if gate else 0.0,
        "hard_failure": False,
        "trace_id": None,
        "expected": ["search_1688_wholesale"],
        "observed": [],
        "steps_complete": True,
        "verdicts": [],
    }]
    with database_connection() as conn:
        assert evaluation_repo.insert_report(conn, owner["tenant_id"], owner["id"], report, rows)


def test_dataset_versions_baselines_reports_and_metrics() -> None:
    """覆盖数据集版本、算法版本、基线唯一与历史、报告原文、趋势与审计权限。"""
    _setup()
    _register_dataset()
    admin = _login("admin")
    staff = _login("staff")
    owner = _owner("admin")
    _insert_run(owner, "run_v1_001", day=1, pass_rate=0.6, gate=False)
    _insert_run(owner, "run_v1_002", day=2, pass_rate=1.0, gate=True)

    datasets = admin.get("/api/evaluations/datasets").json()["items"]
    assert [item["dataset_id"] for item in datasets] == [DATASET_ID]
    assert datasets[0]["version"] == "agent_v1_20261123"
    assert datasets[0]["case_count"] == 22

    detail = admin.get(f"/api/evaluations/datasets/{DATASET_ID}").json()
    assert len(detail["cases"]) == 5 and detail["total_cases"] == 5
    page = admin.get(f"/api/evaluations/datasets/{DATASET_ID}?limit=2").json()
    assert len(page["cases"]) == 2 and page["next_cursor"] == "2"

    profile = admin.post("/api/evaluations/profiles", json={
        "eval_kind": "agent", "version_tag": "", "tech_stack": {"model": "demo-model"},
        "retrieval_params": {"top_k": 8}, "prompt_snapshots": {"planner": "sha"},
        "git_sha": "a1b2c3d4", "dataset_version": "agent_v1_20261123", "notes": "自动编号验证",
    }).json()
    assert profile["version_tag"] == "algo-v1" and profile["version_no"] == 1
    assert profile["digest"]

    assert staff.post("/api/evaluations/baselines", json={"run_id": "run_v1_002", "note": "越权"}).status_code == 403
    first = admin.post("/api/evaluations/baselines", json={"run_id": "run_v1_001", "note": "初始基线"})
    assert first.status_code == 201, first.text
    second = admin.post("/api/evaluations/baselines", json={"run_id": "run_v1_002", "note": "新基线"})
    assert second.status_code == 201, second.text

    history = admin.get("/api/evaluations/baselines").json()["items"]
    assert len(history) == 2
    assert [item["active"] for item in history] == [True, False]
    assert history[0]["run_id"] == "run_v1_002"

    baseline_runs = admin.get("/api/evaluations/runs?baseline_only=true").json()["items"]
    assert [item["run_id"] for item in baseline_runs] == ["run_v1_002"]
    assert baseline_runs[0]["is_baseline"] is True

    trends = admin.get("/api/evaluations/metrics/trends?eval_kind=agent").json()
    assert [item["run_id"] for item in trends["items"]] == ["run_v1_001", "run_v1_002"]
    assert trends["baseline"]["run_id"] == "run_v1_002"
    assert trends["items"][0]["pass_rate_delta"] == -0.4
    assert trends["aggregate"]["avg_pass_rate"] == 0.8

    assert admin.get("/api/evaluations/runs/run_v1_002/layers").json()["layers"] == {}

    payload = json.dumps({"run_id": "run_v1_002", "spans": []}, ensure_ascii=False).encode("utf-8")
    with database_connection() as conn:
        evaluation_service.store_report(conn, owner["tenant_id"], "run_v1_002", eval_kind="agent",
                                        dataset_id=DATASET_ID, payload=payload)
    report = admin.get("/api/evaluations/runs/run_v1_002/report")
    assert report.status_code == 200
    assert report.content == payload
    assert report.headers["X-Report-Sha256"] == hashlib.sha256(payload).hexdigest()
    assert staff.get("/api/evaluations/runs/run_v1_002/report").status_code == 404

    assert staff.get("/api/evaluations/audit-logs").status_code == 403
    actions = [item["action"] for item in admin.get("/api/evaluations/audit-logs").json()["items"]]
    assert actions.count("set_baseline") == 2


def test_trigger_creates_real_evaluation_job_and_cancel() -> None:
    """触发评测创建 evaluation 任务并串行限制，取消后任务进入已取消状态。"""
    _setup()
    _register_dataset()
    admin = _login("admin")
    staff = _login("staff")
    body = {"eval_kind": "agent", "dataset_id": DATASET_ID, "scope": "full",
            "mode": "live", "note": "回归验证", "mark_baseline": False}
    assert staff.post("/api/evaluations/runs/trigger", json=body).status_code == 403

    triggered = admin.post("/api/evaluations/runs/trigger", json=body)
    assert triggered.status_code == 202, triggered.text
    receipt = triggered.json()
    assert receipt["job_id"].startswith("task_") and receipt["status"] == "queued"
    assert admin.post("/api/evaluations/runs/trigger", json=body).status_code == 409

    status = admin.get(f"/api/evaluations/jobs/{receipt['job_id']}").json()
    assert status["state"] == "queued" and status["progress"] == 0

    with database_connection() as conn:
        audits = fetch_all(conn, "SELECT action FROM eval_audit_logs WHERE action='trigger_run'")
    assert len(audits) == 1

    assert admin.post(f"/api/evaluations/jobs/{receipt['job_id']}/cancel").status_code == 200
    assert admin.get(f"/api/evaluations/jobs/{receipt['job_id']}").json()["state"] == "cancelled"
    assert admin.post(f"/api/evaluations/jobs/{receipt['job_id']}/cancel").status_code == 409


def test_synthetic_mode_rejected_for_real_results() -> None:
    """评测中心只接收真实执行结果：合成/回放方式在触发阶段被拒绝。"""
    _setup()
    _register_dataset()
    admin = _login("admin")
    datasets = admin.get("/api/evaluations/datasets").json()["items"]
    assert all(item["execution_modes"] == ["live"] for item in datasets)
    body = {"eval_kind": "agent", "dataset_id": DATASET_ID, "scope": "full",
            "mode": "synthetic_trace_regrade"}
    rejected = admin.post("/api/evaluations/runs/trigger", json=body)
    assert rejected.status_code == 422
    assert rejected.json()["code"] == "REAL_MODE_ONLY"


def test_dataset_detail_not_found_and_invalid_scope_rejected() -> None:
    """未知数据集返回 404，超出数据集声明范围的口径在触发阶段被拒绝。"""
    _setup()
    _register_dataset()
    admin = _login("admin")
    assert admin.get("/api/evaluations/datasets/unknown-v9").status_code == 404
    body = {"eval_kind": "agent", "dataset_id": DATASET_ID, "scope": "quick",
            "mode": "live"}
    rejected = admin.post("/api/evaluations/runs/trigger", json=body)
    assert rejected.status_code == 422
    assert rejected.json()["code"] == "INVALID_SCOPE"


def test_metric_catalog_summary_and_dataset_raw_transparency() -> None:
    """指标目录按类型+口径唯一，批次摘要同口径一致；数据集原始用例与文件可查。"""
    _setup()
    _register_dataset()
    admin = _login("admin")
    owner = _owner("admin")

    catalog = admin.get("/api/evaluations/metrics/catalog").json()
    assert catalog["version"] >= 1 and set(catalog["kinds"]) == {"agent", "rag"}
    agent_schema = catalog["kinds"]["agent"]["scopes"]["full"]
    metric_keys = [metric["key"] for metric in agent_schema["metrics"]]
    assert metric_keys == [
        "task_completion", "mean_score", "required_tool_coverage", "parameter_accuracy",
        "reference_fact_coverage", "call_coverage_among_known", "latency_p50_ms",
        "latency_p95_ms", "total_tokens",
    ]
    assert set(catalog["kinds"]["rag"]["scopes"]) == {
        "full", "quick", "adversarial", "retrieval", "behavior",
    }
    again = admin.get("/api/evaluations/metrics/catalog").json()
    assert again["kinds"]["agent"]["scopes"]["full"] == agent_schema

    _insert_run(owner, "run_metric_001", day=1, pass_rate=0.6, gate=False)
    runs = admin.get("/api/evaluations/runs").json()["items"]
    assert list(runs[0]["summary"]) == metric_keys
    assert runs[0]["summary"]["task_completion"] == 0.6
    assert runs[0]["metric_schema_version"] == catalog["version"]

    trends = admin.get("/api/evaluations/metrics/trends", params={
        "eval_kind": "agent", "dataset_version": "agent_v1_20261123", "scope": "full",
    }).json()
    assert [metric["key"] for metric in trends["metric_schema"]["metrics"]] == metric_keys
    assert list(trends["items"][0]["summary"]) == metric_keys

    raw = admin.get(f"/api/evaluations/datasets/{DATASET_ID}/raw")
    assert raw.status_code == 200
    body = raw.json()
    assert len(body["cases"]) == 5
    assert body["cases"][0]["id"] == "AGENT-T-001" and "prompt" in body["cases"][0]
    sha = _research_cases_sha()
    assert body["files"]["research_cases.json"] == sha

    file_resp = admin.get(f"/api/evaluations/datasets/{DATASET_ID}/files",
                          params={"path": "research_cases.json"})
    assert file_resp.status_code == 200
    assert file_resp.headers["X-Content-Sha256"] == sha

    assert admin.get(f"/api/evaluations/datasets/{DATASET_ID}/files",
                     params={"path": "../AGENTS.md"}).status_code == 400
    assert admin.get(f"/api/evaluations/datasets/{DATASET_ID}/files",
                     params={"path": "unknown.json"}).status_code == 404
    assert admin.get("/api/evaluations/datasets/unknown-v9/raw").status_code == 404


def test_rag_case_detail_accepts_colon_case_ids() -> None:
    """RAG 逐例编号含冒号（层前缀）时，用例详情与游标分页不得被路径校验拒绝。"""
    _setup()
    admin = _login("admin")
    owner = _owner("admin")
    run_id = "rag_metric_colon_001"
    report = {
        "run_id": run_id,
        "created_at": datetime(2026, 11, 3, 4, 0, tzinfo=timezone.utc),
        "mode": "live",
        "eval_kind": "rag",
        "scope": "full",
        "git_sha": "a1b2c3d4",
        "dataset_sha256": "e" * 64,
        "judges_sha256": "j" * 64,
        "report_sha256": hashlib.sha256(run_id.encode()).hexdigest(),
        "baseline_sha256": None,
        "dataset_id": "rag_metric-rag_v1_20261123",
        "dataset_name": "rag_metric",
        "dataset_version": "rag_v1_20261123",
        "dataset_description": "RAG 指标用例",
        "dataset_suite": "rag_core",
        "dataset_content_sha256": "e" * 64,
        "dataset_case_count": 18,
        "cases_payload": [],
        "dataset_metadata": {"modes": ["live"], "scopes": ["full"]},
        "schema_version": 1,
        "metrics": {"retrieval": {"hit@8": 0.9}, "provenance": {"comparable": True}},
        "gate_passed": True,
        "passed_cases": 1,
        "failed_cases": 0,
        "unassessable_cases": 0,
        "pass_rate": 1.0,
        "mean_score": None,
        "latency_p50_ms": 100,
        "latency_p95_ms": 200,
        "total_tokens": 300,
        "judges": [],
    }
    def case_row(case_id: str, corpus: str) -> dict:
        return {
            "case_id": case_id,
            "case_sha256": "f" * 64,
            "name": "行为层用例",
            "category": "product",
            "suite": "behavior",
            "status": "passed",
            "score": 1.0,
            "hard_failure": False,
            "trace_id": None,
            "expected": [corpus],
            "observed": {"why": "命中文档"},
            "steps_complete": True,
            "verdicts": [{"judge": "BehaviorJudge", "dimension": "answer_quality",
                          "status": "passed", "reason": "通过", "issues": []}],
        }

    rows = [case_row("behavior:GEN-T-001", "SYN_CORPUS_LX20"),
            case_row("behavior:GEN-T-002", "SYN_CORPUS_LX21")]
    with database_connection() as conn:
        assert evaluation_repo.insert_report(conn, owner["tenant_id"], owner["id"], report, rows)

    detail = admin.get(f"/api/evaluations/runs/{run_id}/cases/behavior:GEN-T-001")
    assert detail.status_code == 200, detail.text
    body = detail.json()
    assert body["case"]["case_id"] == "behavior:GEN-T-001"
    assert body["expected"] == ["SYN_CORPUS_LX20"]
    assert body["observed"] == {"why": "命中文档"}

    page = admin.get(f"/api/evaluations/runs/{run_id}/cases", params={"limit": 1}).json()
    assert page["next_cursor"] == "behavior:GEN-T-001"
    second = admin.get(f"/api/evaluations/runs/{run_id}/cases",
                       params={"limit": 1, "cursor": page["next_cursor"]}).json()
    assert second["items"][0]["case_id"] == "behavior:GEN-T-002"
    assert admin.get(f"/api/evaluations/runs/{run_id}/runs").status_code in (404, 405)


def test_audit_log_accepts_purge_data_action() -> None:
    """审计读接口必须接受数据清理动作 purge_data，避免整页响应校验失败。"""
    _setup()
    admin = _login("admin")
    owner = _owner("admin")
    with database_connection() as conn:
        evaluation_repo.insert_audit_log(
            conn, owner["tenant_id"], owner["id"], "purge_data", "purge_all",
            {"mode": "all", "runs": 3},
        )
    response = admin.get("/api/evaluations/audit-logs", params={"limit": 30})
    assert response.status_code == 200, response.text
    actions = [item["action"] for item in response.json()["items"]]
    assert "purge_data" in actions
