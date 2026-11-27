"""调研任务板服务契约：简报转换、CAS 调用与执行上下文。"""
from __future__ import annotations

from contextlib import nullcontext

from taozhi.application.research import task_board as board_module
from taozhi.application.research.task_board import TaskBoard, current_task_board, use_task_board
from taozhi.domain.research import SubagentTaskSpec


def _spec() -> SubagentTaskSpec:
    """构造子智能体任务规格。"""
    return SubagentTaskSpec(
        task_id="t1",
        role="research.market_intelligence",
        goal="查口碑",
        suggested_tools=("search_web",),
        seed_queries=("保温杯",),
        output_format="三段式",
        source_guidance="官方页面",
        boundaries="只查口碑",
        expected_evidence="来源与数字",
    )


def test_task_board_create_tasks_persists_brief(monkeypatch):
    """创建任务时把领域简报转换为任务板 JSON，并带上写入范围。"""
    captured = {}

    def fake_create(conn, **kwargs):
        """替身任务创建入口，记录参数并返回任务行。

        Args:
            conn: 数据库连接。
            kwargs: 关键字参数。
        """
        captured.update(kwargs)
        return [{"id": "rt_1", "task_key": "t1"}]

    monkeypatch.setattr(board_module, "database_connection", lambda: nullcontext(None))
    monkeypatch.setattr(board_module.research_task_repo, "create_tasks", fake_create)
    board = TaskBoard(tenant_id="tenant", job_id="job", run_version=3)
    rows = board.create_tasks([_spec()])

    assert rows[0]["id"] == "rt_1"
    assert captured["tenant_id"] == "tenant"
    assert captured["job_id"] == "job"
    assert captured["run_version"] == 3
    brief = captured["briefs"][0]
    assert brief["task_id"] == "t1"
    assert brief["suggested_tools"] == ["search_web"]
    assert brief["output_format"] == "三段式"
    assert brief["write_scopes"] == []


def test_task_board_start_and_complete_use_latest_revision(monkeypatch):
    """启动/完成都通过任务行 ID 走仓储状态机。"""
    calls = []

    def fake_find(conn, **kwargs):
        """替身任务查询入口，返回就绪任务。

        Args:
            conn: 数据库连接。
            kwargs: 关键字参数。
        """
        return {"id": "rt_1", "status": "ready"}

    def fake_claim(conn, task_id, owner):
        """替身任务认领入口，记录调用并返回运行中任务。

        Args:
            conn: 数据库连接。
            task_id: 任务标识。
            owner: 任务所有者标识。
        """
        calls.append(("claim", task_id, owner))
        return {"id": task_id, "status": "running"}

    def fake_finish(conn, task_id, *, status, result=None, error=""):
        """替身任务完成入口，记录状态与结果。

        Args:
            conn: 数据库连接。
            task_id: 任务标识。
            status: 任务状态。
            result: 任务结果。
            error: 错误信息。
        """
        calls.append(("finish", task_id, status, result, error))
        return {"id": task_id, "status": status}

    monkeypatch.setattr(board_module, "database_connection", lambda: nullcontext(None))
    monkeypatch.setattr(board_module.research_task_repo, "find_task", fake_find)
    monkeypatch.setattr(board_module.research_task_repo, "claim_task", fake_claim)
    monkeypatch.setattr(board_module.research_task_repo, "finish_task", fake_finish)
    board = TaskBoard(tenant_id="tenant", job_id="job", run_version=1)

    assert board.start("t1", "research.market_intelligence")["status"] == "running"
    board.complete("t1", {"summary": "ok"})
    assert calls[0] == ("claim", "rt_1", "research.market_intelligence")
    assert calls[1] == ("finish", "rt_1", "done", {"summary": "ok"}, "")


def test_task_board_context_binds_per_execution():
    """任务板上下文在退出后必须恢复，避免跨任务串板。"""
    board = TaskBoard(tenant_id="a", job_id="job", run_version=1)
    assert current_task_board() is None
    with use_task_board(board):
        assert current_task_board() is board
    assert current_task_board() is None
