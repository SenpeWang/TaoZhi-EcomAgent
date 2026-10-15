from __future__ import annotations
from contextlib import contextmanager
import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from .config import load_config

@contextmanager
def database_connection():
    with psycopg.connect(load_config().dsn, row_factory=dict_row, connect_timeout=8) as conn:
        try:
            from pgvector.psycopg import register_vector
            register_vector(conn)  # chunks.embedding 为 vector(512)：写入传 numpy/列表，读回 numpy
        except Exception:
            pass  # pgvector-python 缺失时退化为文本协议，向量读写由调用方兜底
        with conn.transaction():
            yield conn

def fetch_one(conn, sql, params=()):
    return conn.execute(sql,params).fetchone()
def fetch_all(conn, sql, params=()):
    return conn.execute(sql,params).fetchall()
def as_jsonb(value):
    return Jsonb(value)
