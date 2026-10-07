"""FAQ 语义缓存（长期记忆）：问题向量 + 答案 + 引用，embedding 相似度命中直接返回。

存储：data/archive/faq_cache.sqlite（vec 为 numpy float32 tobytes，dim 记录向量维度）。
初始化失败时全部方法静默降级（永远未命中 / 不写入），绝不抛错影响主流程。
"""

from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

import numpy as np

from ..config import get_settings
from ..llm.embeddings import cosine_matrix, get_embedding_provider

_SCHEMA = """
CREATE TABLE IF NOT EXISTS faq_cache (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    question TEXT NOT NULL,
    answer TEXT NOT NULL,
    citations_json TEXT DEFAULT '[]',
    vec BLOB NOT NULL,
    dim INTEGER NOT NULL,
    created_at TEXT NOT NULL
);
"""


class FaqCache:
    """高频问答语义缓存：余弦相似度 ≥ FAQ_CACHE_SIM_THRESHOLD 时命中。"""

    # 相似度极高（同一问题重复写入）时覆盖更新而非新增条目
    OVERWRITE_SIM = 0.995

    def __init__(self, db_path: Optional[Path] = None) -> None:
        settings = get_settings()
        self.db_path = Path(db_path) if db_path else settings.data_dir / "archive" / "faq_cache.sqlite"
        self.sim_threshold = settings.faq_cache_sim_threshold
        self._lock = threading.RLock()
        self._enabled = self._init_sqlite()

    def _init_sqlite(self) -> bool:
        try:
            self.db_path.parent.mkdir(parents=True, exist_ok=True)
            conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
            try:
                conn.executescript(_SCHEMA)
                conn.commit()
            finally:
                conn.close()
            return True
        except Exception:  # noqa: BLE001 — 缓存不可用时静默降级
            return False

    @property
    def enabled(self) -> bool:
        return self._enabled

    # ───────────────────────── 查询 ─────────────────────────
    def lookup(self, question: str, sim_threshold: Optional[float] = None) -> Optional[Dict[str, Any]]:
        """返回 {question/answer/citations/score/id}；未命中或任何异常返回 None。"""
        if not question or not self._enabled:
            return None
        threshold = float(sim_threshold if sim_threshold is not None else self.sim_threshold)
        try:
            query_vec = self._embed(question)
            if query_vec is None:
                return None
            with self._lock:
                rows = self._rows()
            if not rows:
                return None
            best_id, best_score, best_row = None, -1.0, None
            for row in rows:
                row_id, rq, answer, citations_json, blob, dim, created_at = row
                vec = np.frombuffer(blob, dtype="float32")
                if vec.size != query_vec.size:  # embedding 后端/维度变化，旧向量作废
                    continue
                sim = self._cosine(query_vec, vec)
                if sim > best_score:
                    best_id, best_score, best_row = row_id, float(sim), row
            if best_row is None or best_score < threshold:
                return None
            _, rq, answer, citations_json, _, _, created_at = best_row
            return {
                "id": best_id,
                "question": rq,
                "answer": answer,
                "citations": json.loads(citations_json or "[]"),
                "score": round(best_score, 4),
                "created_at": created_at,
            }
        except Exception:  # noqa: BLE001
            return None

    # ───────────────────────── 写入 ─────────────────────────
    def put(self, question: str, answer: str, citations: Optional[List[Dict[str, Any]]] = None) -> bool:
        """写入/覆盖一条 FAQ 缓存；任何异常静默返回 False。"""
        if not question or not answer or not self._enabled:
            return False
        citations_json = json.dumps(citations or [], ensure_ascii=False, default=str)
        try:
            vec = self._embed(question)
            if vec is None:
                return False
            created_at = datetime.utcnow().isoformat(timespec="seconds")
            with self._lock:
                conn = self._connect()
                try:
                    # 同一问题（相似度极高）覆盖更新，避免重复条目
                    dup_id = self._find_duplicate(conn, vec)
                    if dup_id is not None:
                        conn.execute(
                            "UPDATE faq_cache SET question=?, answer=?, citations_json=?, vec=?, dim=?, created_at=?"
                            " WHERE id=?",
                            (question, answer, citations_json, vec.tobytes(),
                             int(vec.size), created_at, dup_id),
                        )
                    else:
                        conn.execute(
                            "INSERT INTO faq_cache(question, answer, citations_json, vec, dim, created_at)"
                            " VALUES(?,?,?,?,?,?)",
                            (question, answer, citations_json, vec.tobytes(),
                             int(vec.size), created_at),
                        )
                    conn.commit()
                finally:
                    conn.close()
            return True
        except Exception:  # noqa: BLE001
            return False

    def count(self) -> int:
        if not self._enabled:
            return 0
        try:
            with self._lock:
                conn = self._connect()
                try:
                    cur = conn.execute("SELECT COUNT(*) FROM faq_cache")
                    return int(cur.fetchone()[0])
                finally:
                    conn.close()
        except Exception:  # noqa: BLE001
            return 0

    # ───────────────────────── 内部实现 ─────────────────────────
    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(str(self.db_path), check_same_thread=False)

    def _rows(self) -> List[tuple]:
        conn = self._connect()
        try:
            cur = conn.execute(
                "SELECT id, question, answer, citations_json, vec, dim, created_at FROM faq_cache"
            )
            return cur.fetchall()
        finally:
            conn.close()

    def _find_duplicate(self, conn: sqlite3.Connection, vec: np.ndarray) -> Optional[int]:
        cur = conn.execute("SELECT id, vec FROM faq_cache")
        for row_id, blob in cur.fetchall():
            other = np.frombuffer(blob, dtype="float32")
            if other.size == vec.size and self._cosine(vec, other) >= self.OVERWRITE_SIM:
                return int(row_id)
        return None

    @staticmethod
    def _cosine(a: np.ndarray, b: np.ndarray) -> float:
        sim = cosine_matrix(a.astype("float32"), b.reshape(1, -1).astype("float32"))
        return float(sim[0]) if sim.size else -1.0

    @staticmethod
    def _embed(text: str) -> Optional[np.ndarray]:
        """经项目统一 embedding 降级链（远端 → sentence-transformers → TF-IDF/SVD → hash）。"""
        try:
            vec = np.asarray(get_embedding_provider().encode_one(text), dtype="float32")
            return vec if vec.size else None
        except Exception:  # noqa: BLE001
            return None


_cache: Optional[FaqCache] = None
_cache_lock = threading.Lock()


def get_faq_cache() -> FaqCache:
    """模块级单例（与 get_session_store 同风格）。"""
    global _cache
    with _cache_lock:
        if _cache is None:
            _cache = FaqCache()
        return _cache


__all__ = ["FaqCache", "get_faq_cache"]
