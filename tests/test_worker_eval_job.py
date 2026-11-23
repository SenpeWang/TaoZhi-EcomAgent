"""评测任务执行：运行器进度回报、入库终态、超时与取消。"""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import sys
import threading
import time

from fastapi.testclient import TestClient

from taozhi.application.evaluation import execution
from taozhi.infrastructure.persistence import evaluation_repo, job_repo
from taozhi.infrastructure.persistence.database import database_connection
from taozhi.server.api import app

PROJECT = Path(__file__).resolve().parents[1]
DATASET_ID = "agent_research-v1_20261123"


def _setup() -> None:
    spec = importlib.util.spec_from_file_location("worker_setup", PROJECT / "scripts/manage_database.py")
    setup = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(setup)
    with database_connection() as conn:
        conn.execute(
            "TRUNCATE eval_audit_logs, eval_baselines, eval_run_reports, eval_case_results, "
            "eval_runs, eval_datasets, eval_profiles, tenants CASCADE"
        )
        conn.execute("TRUNCATE login_attempts")
    setup.seed_test()


def _register_dataset() -> None:
    with database_connection() as conn:
        evaluation_repo.upsert_dataset(conn, {
            "dataset_id": DATASET_ID, "name": "agent_research", "eval_kind": "agent",
            "version": "v1_20261123", "description": "Agent 调研基准 V1",
            "source": "synthetic_trace_regrade", "suite": "agent_research",
            "content_sha256": "c" * 64, "case_count": 22, "cases_payload": [],
            "metadata": {"modes": ["live", "synthetic_trace_regrade"], "scopes": ["full"]},
        })


def _admin() -> TestClient:
    client = TestClient(app)
    response = client.post("/api/auth/login", json={"username": "admin", "password": "123456"})
    assert response.status_code == 200, response.text
    client.headers["X-CSRF-Token"] = response.json()["csrf_token"]
    return client


def _queue_job(client: TestClient) -> dict:
    response = client.post("/api/evaluations/runs/trigger", json={
        "eval_kind": "agent", "dataset_id": DATASET_ID, "scope": "full",
        "mode": "synthetic_trace_regrade", "note": "worker 任务测试",
    })
    assert response.status_code == 202, response.text
    return job_repo.claim_next_job("worker_test")


def _job_state(job_id: str) -> dict:
    with database_connection() as conn:
        return job_repo.get_job_by_id(conn, job_id)


def test_process_evaluation_job_reports_progress_and_publishes(monkeypatch) -> None:
    """运行器成功时任务进入 completed，并带回入库批次结果。"""
    _setup()
    _register_dataset()
    job = _queue_job(_admin())

    def fake_runner(job, dataset, report_dir, progress_file):
        script = (
            "import json, pathlib, sys\n"
            "report_dir = pathlib.Path(sys.argv[1]); progress = pathlib.Path(sys.argv[2])\n"
            "report_dir.mkdir(parents=True, exist_ok=True)\n"
            "(report_dir / 'result.json').write_text(json.dumps({'gate': {'passed': True}}), encoding='utf-8')\n"
            "progress.write_text(json.dumps({'stage': '执行用例 1/1', 'progress': 60}) + '\\n', encoding='utf-8')\n"
        )
        return [sys.executable, "-c", script, str(report_dir), str(progress_file)]

    def fake_publish(job, report_path):
        receipt = {"run_id": "run_fake_001", "created": True, "gate_passed": True, "dataset_id": DATASET_ID,
                   "eval_kind": "agent", "dataset_version": "v1_20261123", "scope": "full",
                   "mode": "synthetic_trace_regrade"}
        return [sys.executable, "-c", f"import json;print(json.dumps({receipt!r}))"]

    monkeypatch.setattr(execution, "_runner_command", fake_runner)
    monkeypatch.setattr(execution, "_publish_command", fake_publish)
    execution.process_evaluation_job(job)

    state = _job_state(job["id"])
    assert state["state"] == "completed", (state["stage"], state["result"])
    assert state["progress"] == 100
    assert state["result"]["run_id"] == "run_fake_001"
    assert state["result"]["gate_passed"] is True


def test_process_evaluation_job_times_out(monkeypatch) -> None:
    """运行器超过配置超时会被终止并标记失败。"""
    _setup()
    _register_dataset()
    job = _queue_job(_admin())
    monkeypatch.setenv("ECOM_EVAL_TIMEOUT_SECONDS", "2")
    monkeypatch.setattr(execution, "_runner_command",
                        lambda *args: [sys.executable, "-c", "import time; time.sleep(30)"])
    execution.process_evaluation_job(job)

    state = _job_state(job["id"])
    assert state["state"] == "failed", (state["stage"], state["result"])
    assert "未完成" in state["stage"]


def test_process_evaluation_job_cancel(monkeypatch) -> None:
    """运行中被取消时终止子进程并标记任务已取消。"""
    _setup()
    _register_dataset()
    job = _queue_job(_admin())
    monkeypatch.setattr(execution, "_runner_command",
                        lambda *args: [sys.executable, "-c", "import time; time.sleep(30)"])
    monkeypatch.setenv("ECOM_EVAL_TIMEOUT_SECONDS", "120")

    worker = threading.Thread(target=execution.process_evaluation_job, args=(job,), daemon=True)
    worker.start()
    for _ in range(30):
        if _job_state(job["id"])["state"] == "running":
            break
        time.sleep(0.2)
    with database_connection() as conn:
        job_repo.mark_job_cancelled(conn, job["id"])
    worker.join(timeout=30)
    assert not worker.is_alive()
    state = _job_state(job["id"])
    assert state["state"] == "cancelled"