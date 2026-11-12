"""生产级工作流韧性、断点中断重答与事件流水可见性验收测试。"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from taozhi.knowledge.db import database_connection, fetch_one, fetch_all
from taozhi.server.api import app
from taozhi.server.services import present_job
from taozhi.infrastructure.persistence.access_repo import load_principal


def _get_client_and_job():
    client = TestClient(app)
    login_res = client.post("/api/auth/login", json={"username": "syn_admin", "password": "taozhi2026eval"})
    if login_res.status_code != 200:
        login_res = client.post("/api/auth/login", json={"username": "admin", "password": "123456"})
    assert login_res.status_code == 200, login_res.text
    csrf = login_res.json()["csrf_token"]
    client.headers["X-CSRF-Token"] = csrf

    with database_connection() as c:
        job = fetch_one(c, "SELECT * FROM jobs LIMIT 1")
    if not job:
        res = client.post("/api/tasks", json={"question": "测试工作流韧性任务", "kind": "ask"})
        assert res.status_code == 202
        task_id = res.json()["id"]
        with database_connection() as c:
            job = fetch_one(c, "SELECT * FROM jobs WHERE id=%s", (task_id,))
    return client, job


def test_present_job_returns_task_events():
    """验证任务详情接口 100% 返回 task_events 表中的真实历史流转记录。"""
    with database_connection() as c:
        job = fetch_one(c, "SELECT * FROM jobs WHERE state='completed' LIMIT 1")
        if not job:
            pytest.skip("数据库暂无已完成任务")
        p = load_principal(c, job["owner_id"])
        data = present_job(c, p, job, full=True)

        assert "events" in data
        assert isinstance(data["events"], list)
        assert len(data["events"]) >= 1

        ev = data["events"][0]
        assert "id" in ev
        assert "stage" in ev
        assert "progress" in ev
        assert "status" in ev


def test_task_cancellation_and_retry_flow():
    """测试任务在取消后可安全发起 retry（run_version 递增并重新排队）。"""
    client, job = _get_client_and_job()
    job_id = job["id"]
    orig_state = job["state"]
    orig_version = job.get("run_version") or 1

    # 1. 尝试对非 running 任务重试
    with database_connection() as c:
        c.execute("UPDATE jobs SET state='cancelled' WHERE id=%s", (job_id,))

    retry_res = client.post(f"/api/tasks/{job_id}/retry")
    assert retry_res.status_code == 200
    retry_data = retry_res.json()
    assert retry_data["run_version"] == orig_version + 1

    # 验证数据库状态恢复为 queued 且写入了 task_outbox
    with database_connection() as c:
        updated_job = fetch_one(c, "SELECT * FROM jobs WHERE id=%s", (job_id,))
        assert updated_job["state"] == "queued"
        assert updated_job["run_version"] == orig_version + 1

        outbox_row = fetch_one(c, "SELECT * FROM task_outbox WHERE job_id=%s", (job_id,))
        assert outbox_row is not None

        # 恢复初始状态
        c.execute("UPDATE jobs SET state=%s, run_version=%s WHERE id=%s", (orig_state, orig_version, job_id))
        c.execute("DELETE FROM task_outbox WHERE job_id=%s", (job_id,))


def test_task_retry_rejected_when_running():
    """测试当任务处于 running 运行中时，拒绝并发重复 retry 重试。"""
    client, job = _get_client_and_job()
    orig_state = job["state"]
    with database_connection() as c:
        c.execute("UPDATE jobs SET state='running' WHERE id=%s", (job["id"],))

    try:
        r = client.post(f"/api/tasks/{job['id']}/retry")
        assert r.status_code == 409
        assert "无需重复发起" in r.text
    finally:
        with database_connection() as c:
            c.execute("UPDATE jobs SET state=%s WHERE id=%s", (orig_state, job["id"]))