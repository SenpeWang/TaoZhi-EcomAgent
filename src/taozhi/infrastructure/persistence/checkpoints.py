"""工作流共用的 PostgreSQL 检查点连接和首次初始化。"""
from __future__ import annotations

import threading
from contextlib import contextmanager
from typing import Iterator

from langgraph.checkpoint.postgres import PostgresSaver

from taozhi.config import load_config
from taozhi.knowledge.db import autocommit_connection

_CHECKPOINTER_SETUP_LOCK = threading.Lock()
_CHECKPOINTER_SETUP_DSNS: set[str] = set()


@contextmanager
def postgres_checkpointer() -> Iterator[PostgresSaver]:
    """从连接池复用自动提交连接打开 LangGraph PostgresSaver（每个 DSN 仅建表 setup 一次）。"""
    conn_str = load_config().dsn
    with autocommit_connection(conn_str) as conn:
        saver = PostgresSaver(conn)
        if conn_str not in _CHECKPOINTER_SETUP_DSNS:
            with _CHECKPOINTER_SETUP_LOCK:
                if conn_str not in _CHECKPOINTER_SETUP_DSNS:
                    saver.setup()
                    _CHECKPOINTER_SETUP_DSNS.add(conn_str)
        yield saver
