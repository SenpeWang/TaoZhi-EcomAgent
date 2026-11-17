"""PostgreSQL 连接池与可重入事务上下文管理器。

使用 ``psycopg_pool.ConnectionPool`` 复用连接，并通过 ``contextvars``
在嵌套调用 ``database_connection()`` 时打开保存点（SAVEPOINT）复用外层同一连接，
杜绝多处建连开销与嵌套事务死锁。
"""
from __future__ import annotations

import atexit
import contextvars
import logging
import threading
from contextlib import contextmanager
from typing import Any, Iterator

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from psycopg_pool import ConnectionPool

from taozhi.config import load_config

_LOG = logging.getLogger(__name__)
_POOL_LOCK = threading.Lock()
_POOLS: dict[str, ConnectionPool] = {}
_ACTIVE_CONN: contextvars.ContextVar[psycopg.Connection | None] = contextvars.ContextVar(
    "taozhi_active_conn", default=None
)


def _configure_connection(conn: psycopg.Connection) -> None:
    """初始化新建立的数据库连接，注册 pgvector 向量编解码扩展。"""
    try:
        from pgvector.psycopg import register_vector

        register_vector(conn)
    except Exception as exc:
        _LOG.debug("pgvector 扩展注册跳过（退化为文本协议）: %s", exc)


def _get_pool(dsn: str) -> ConnectionPool:
    """按 DSN 懒加载并复用线程安全连接池。"""
    with _POOL_LOCK:
        pool = _POOLS.get(dsn)
        if pool is None or pool.closed:
            pool = ConnectionPool(
                dsn,
                min_size=1,
                max_size=12,
                timeout=8.0,
                kwargs={"row_factory": dict_row, "connect_timeout": 8},
                configure=_configure_connection,
                open=True,
            )
            _POOLS[dsn] = pool
        return pool


def close_pools() -> None:
    """关闭进程内缓存的所有数据库连接池。"""
    with _POOL_LOCK:
        for pool in list(_POOLS.values()):
            try:
                pool.close()
            except Exception as exc:
                _LOG.debug("关闭连接池异常: %s", exc)
        _POOLS.clear()


atexit.register(close_pools)


@contextmanager
def database_connection() -> Iterator[psycopg.Connection]:
    """获取可重入的事务数据库连接；嵌套调用时复用外层连接与保存点。

    Yields:
        绑定 ``dict_row`` 行工厂与事务保护的 PostgreSQL 连接实例。
    """
    outer = _ACTIVE_CONN.get()
    if outer is not None and not outer.closed:
        with outer.transaction():
            yield outer
        return

    pool = _get_pool(load_config().dsn)
    with pool.connection() as conn:
        token = _ACTIVE_CONN.set(conn)
        try:
            with conn.transaction():
                yield conn
        finally:
            _ACTIVE_CONN.reset(token)


def fetch_one(conn: Any, sql: str, params: tuple[Any, ...] = ()) -> dict[str, Any] | None:
    """执行只读查询并返回首行字典（无结果返回 None）。"""
    return conn.execute(sql, params).fetchone()


def fetch_all(conn: Any, sql: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
    """执行只读查询并返回全部结果字典列表。"""
    return conn.execute(sql, params).fetchall()


def as_jsonb(value: Any) -> Jsonb:
    """将 Python 对象包装为 psycopg JSONB 参数适配器。"""
    return Jsonb(value)
