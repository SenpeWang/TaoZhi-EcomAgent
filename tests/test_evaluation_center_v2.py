"""验证评测中心 AGENT/RAG 双体系、不可变技术栈与 Prompt 留痕及窄写宽读权限。"""
from __future__ import annotations

from datetime import datetime, timezone

from fastapi.testclient import TestClient

from taozhi.infrastructure.persistence.database import database_connection, fetch_all
from taozhi.infrastructure.persistence.evaluation_repo import insert_report
from taozhi.server.api import app


def _login(username: str) -> TestClient:
    client = TestClient(app)
    resp = client.post("/api/auth/login", json={"username": username, "password": "123456"})
    assert resp.status_code == 200, resp.text
    client.headers["X-CSRF-Token"] = resp.json()["csrf_token"]
    return client


def test_evaluation_center_wide_read_narrow_write_and_immutable_profiles(monkeypatch) -> None:
    import importlib.util
    from pathlib import Path
    from taozhi.config import load_config

    assert load_config().mode == "test" and load_config().dsn.endswith("/ecom_test")
    spec = importlib.util.spec_from_file_location(
        "v3_setup", Path(__file__).resolve().parents[1] / "scripts/manage_database.py"
    )
    setup = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(setup)
    with database_connection() as c:
        c.execute("TRUNCATE tenants CASCADE")
        c.execute("TRUNCATE login_attempts")
        c.execute("TRUNCATE eval_audit_logs")
    setup.seed_test()

    admin = _login("admin")
    staff = _login("staff")
    leader = _login("leader")

    profile_payload = {
        "eval_kind": "agent",
        "version_tag": "v1-agent-baseline",
        "tech_stack": {"model": "sensenova-6.8-flash", "orchestration": "langgraph"},
        "retrieval_params": {"max_turns": 5},
        "prompt_snapshots": {"orchestrator": "strict supervisor prompt v1"},
        "notes": "V1 基准配置",
    }
    assert staff.post("/api/evaluations/profiles", json=profile_payload).status_code == 403
    assert leader.post("/api/evaluations/profiles", json=profile_payload).status_code == 403

    created = admin.post("/api/evaluations/profiles", json=profile_payload)
    assert created.status_code == 201, created.text
    profile_id = created.json()["profile_id"]
    again = admin.post("/api/evaluations/profiles", json=profile_payload)
    assert again.status_code == 201 and again.json()["profile_id"] == profile_id
    conflict = admin.post("/api/evaluations/profiles",
                          json={**profile_payload, "tech_stack": {"model": "another-model"}})
    assert conflict.status_code == 409

    with database_connection() as conn:
        owner = fetch_all(conn, "SELECT id, tenant_id FROM users WHERE username='admin'")[0]
        insert_report(
            conn,
            owner["tenant_id"],
            owner["id"],
            {
                "run_id": "run_agent_v1_001",
                "created_at": datetime(2026, 11, 23, 4, 0, tzinfo=timezone.utc),
                "mode": "synthetic_trace_regrade",
                "eval_kind": "agent",
                "profile_id": profile_id,
                "git_sha": "abc1234",
                "dataset_sha256": "d" * 64,
                "judges_sha256": "j" * 64,
                "report_sha256": "r" * 64,
                "baseline_sha256": None,
                "dataset_id": "v1_20261123_agent",
                "dataset_name": "V1 Agent 调研基准集",
                "dataset_version": "v1_20261123",
                "dataset_description": "V1 Agent 合成基准",
                "cases_payload": [{"id": "AGENT-MOQ-011", "question": "比价测试"}],
                "schema_version": 1,
                "pass_rate": 1.0,
                "mean_score": 1.0,
                "latency_p50_ms": 120,
                "latency_p95_ms": 240,
                "total_tokens": 500,
                "judges": [],
                "metrics": {"provenance": {}},
                "gate_passed": True,
                "passed_cases": 1,
                "failed_cases": 0,
                "unassessable_cases": 0,
            },
            [
                {
                    "case_id": "AGENT-MOQ-011",
                    "case_sha256": "c" * 64,
                    "name": "MOQ 阶梯价",
                    "category": "sourcing",
                    "suite": "standard",
                    "status": "passed",
                    "score": 1.0,
                    "hard_failure": False,
                    "trace_id": None,
                    "expected": ["search_1688_wholesale"],
                    "observed": [],
                    "steps_complete": True,
                    "verdicts": [],
                }
            ],
        )

    assert staff.get("/api/evaluations/datasets?eval_kind=agent").status_code == 200
    detail = staff.get("/api/evaluations/datasets/v1_20261123_agent")
    assert detail.status_code == 200
    assert detail.json()["cases"][0]["id"] == "AGENT-MOQ-011"

    staff_profiles = staff.get("/api/evaluations/profiles?eval_kind=agent")
    assert staff_profiles.status_code == 200
    assert staff_profiles.json()["items"][0]["version_tag"] == "v1-agent-baseline"

    admin_profiles = admin.get("/api/evaluations/profiles?eval_kind=agent")
    assert admin_profiles.status_code == 200
    assert admin_profiles.json()["items"][0]["batch_count"] == 1
    assert admin_profiles.json()["items"][0]["avg_pass_rate"] == 1.0

    assert admin.get("/api/evaluations/runs?eval_kind=rag").json()["items"] == []
    assert len(admin.get("/api/evaluations/runs?eval_kind=agent&dataset_id=v1_20261123_agent").json()["items"]) == 1

    trigger_body = {
        "eval_kind": "agent",
        "dataset_id": "v1_20261123_agent",
        "profile_id": profile_id,
        "note": "复测 V1",
    }
    assert staff.post("/api/evaluations/runs/trigger", json=trigger_body).status_code == 403
    trig = admin.post("/api/evaluations/runs/trigger", json=trigger_body)
    assert trig.status_code == 202, trig.text

    with database_connection() as conn:
        audits = fetch_all(conn, "SELECT action, actor_id FROM eval_audit_logs ORDER BY created_at")
    assert [a["action"] for a in audits] == ["register_profile", "trigger_run"]
