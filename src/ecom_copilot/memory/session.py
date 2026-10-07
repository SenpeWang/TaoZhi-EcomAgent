"""会话记忆存储（SessionStore）与历史注入器（HistoryInjector）。

- SQLite 主存储：data/archive/sessions.sqlite，每轮 Q-A 一行；
- 初始化失败自动降级 JSONL：data/archive/sessions.jsonl（一行一条记录，读取时倒序截取）；
- HistoryInjector：把近 N 轮历史（超阈值时先经 Summarizer 压缩）渲染为
  「【会话历史】」文本块，供 Supervisor 规划与答案生成做指代消解。

记忆属于旁路能力：任何异常都必须静默降级，绝不打断问答主流程。
"""

from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

from ..config import get_settings
from .summarizer import Summarizer

_VALID_ROLES = ("user", "assistant")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS session_messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL,
    user_id TEXT DEFAULT '',
    org_tag TEXT DEFAULT '',
    role TEXT NOT NULL,
    content TEXT NOT NULL,
    task_id TEXT DEFAULT '',
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_session_messages_sid ON session_messages(session_id, id);
"""


class SessionStore:
    """按 session 隔离的会话记忆：SQLite 优先，不可用时降级 JSONL 追加文件。"""

    def __init__(self, db_path: Optional[Path] = None,
                 jsonl_path: Optional[Path] = None) -> None:
        settings = get_settings()
        self.db_path = Path(db_path) if db_path else settings.data_dir / "archive" / "sessions.sqlite"
        self.jsonl_path = Path(jsonl_path) if jsonl_path else settings.data_dir / "archive" / "sessions.jsonl"
        self._lock = threading.RLock()
        self._use_sqlite = self._init_sqlite()

    # ───────────────────────── 初始化 ─────────────────────────
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
        except Exception:  # noqa: BLE001 — SQLite 不可用时降级 JSONL
            return False

    @property
    def backend(self) -> str:
        return "sqlite" if self._use_sqlite else "jsonl"

    # ───────────────────────── 写入 ─────────────────────────
    def add_message(self, session_id: str, role: str, content: str,
                    user_id: str = "", org_tag: str = "", task_id: str = "") -> bool:
        """追加一轮消息（role ∈ user|assistant）；失败静默返回 False。"""
        if not session_id or not content or role not in _VALID_ROLES:
            return False
        record = {
            "session_id": session_id,
            "user_id": user_id,
            "org_tag": org_tag,
            "role": role,
            "content": content,
            "task_id": task_id,
            "created_at": datetime.utcnow().isoformat(timespec="seconds"),
        }
        try:
            with self._lock:
                if self._use_sqlite:
                    return self._add_sqlite(record)
                return self._add_jsonl(record)
        except Exception:  # noqa: BLE001 — 记忆写入失败不影响主流程
            return False

    def _add_sqlite(self, record: Dict[str, str]) -> bool:
        conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        try:
            conn.execute(
                "INSERT INTO session_messages(session_id, user_id, org_tag, role, content, task_id, created_at)"
                " VALUES(?,?,?,?,?,?,?)",
                (record["session_id"], record["user_id"], record["org_tag"],
                 record["role"], record["content"], record["task_id"], record["created_at"]),
            )
            conn.commit()
        finally:
            conn.close()
        return True

    def _add_jsonl(self, record: Dict[str, str]) -> bool:
        self.jsonl_path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.jsonl_path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")
        return True

    # ───────────────────────── 查询 ─────────────────────────
    def get_history(self, session_id: str, limit: int = 6) -> List[Dict[str, str]]:
        """按时间正序返回某 session 最近 limit 条消息（role/content/created_at）。"""
        if not session_id or limit <= 0:
            return []
        try:
            with self._lock:
                rows = self._get_sqlite(session_id, limit) if self._use_sqlite \
                    else self._get_jsonl(session_id, limit)
        except Exception:  # noqa: BLE001
            return []
        return [{"role": r[0], "content": r[1], "created_at": r[2]} for r in rows]

    def _get_sqlite(self, session_id: str, limit: int) -> List[tuple]:
        conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        try:
            cur = conn.execute(
                "SELECT role, content, created_at FROM session_messages"
                " WHERE session_id=? ORDER BY id DESC LIMIT ?",
                (session_id, int(limit)),
            )
            rows = cur.fetchall()
        finally:
            conn.close()
        rows.reverse()  # id 倒序取最新 limit 条后翻转为时间正序
        return rows

    def _get_jsonl(self, session_id: str, limit: int) -> List[tuple]:
        if not self.jsonl_path.exists():
            return []
        rows: List[tuple] = []
        with open(self.jsonl_path, "r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    item = json.loads(line)
                except Exception:  # noqa: BLE001
                    continue
                if item.get("session_id") != session_id:
                    continue
                rows.append((str(item.get("role", "user")),
                             str(item.get("content", "")),
                             str(item.get("created_at", ""))))
        return rows[-int(limit):]  # 追加序即时间正序，倒序截取最近 limit 条

    # ───────────────────────── 清除 ─────────────────────────
    def clear_session(self, session_id: str) -> int:
        """清除某 session 的全部记忆，返回删除条数（失败返回 0）。"""
        if not session_id:
            return 0
        try:
            with self._lock:
                if self._use_sqlite:
                    conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
                    try:
                        cur = conn.execute(
                            "DELETE FROM session_messages WHERE session_id=?", (session_id,))
                        conn.commit()
                        return cur.rowcount if cur.rowcount and cur.rowcount > 0 else 0
                    finally:
                        conn.close()
                return self._clear_jsonl(session_id)
        except Exception:  # noqa: BLE001
            return 0

    def _clear_jsonl(self, session_id: str) -> int:
        if not self.jsonl_path.exists():
            return 0
        kept: List[str] = []
        removed = 0
        with open(self.jsonl_path, "r", encoding="utf-8") as fh:
            for line in fh:
                try:
                    item = json.loads(line)
                except Exception:  # noqa: BLE001
                    continue
                if item.get("session_id") == session_id:
                    removed += 1
                    continue
                kept.append(line.rstrip("\n"))
        if removed:
            with open(self.jsonl_path, "w", encoding="utf-8") as fh:
                fh.write("\n".join(kept) + ("\n" if kept else ""))
        return removed


class HistoryInjector:
    """把会话历史压缩/渲染为「【会话历史】」文本块，注入 Supervisor 与答案生成 prompt。"""

    @staticmethod
    def build(session_id: str, question: str = "", limit: Optional[int] = None) -> str:
        """取历史 → maybe_compress → 渲染文本块；空历史或任何异常返回 ""。"""
        try:
            settings = get_settings()
            if not settings.memory_enabled or not session_id:
                return ""
            turns = int(limit or settings.memory_history_turns)
            history = get_session_store().get_history(session_id, limit=max(turns * 3, 10))
            if not history:
                return ""
            compressed = Summarizer(settings).maybe_compress(history)
            return HistoryInjector.render(compressed)
        except Exception:  # noqa: BLE001 — 记忆故障绝不打断主流程
            return ""

    @staticmethod
    def render(history: List[Dict[str, str]]) -> str:
        if not history:
            return ""
        rounds = sum(1 for m in history if m.get("role") == "user")
        lines = [f"【会话历史】(近 {max(rounds, 1)} 轮)"]
        for item in history:
            speaker = "用户" if item.get("role") == "user" else "助手"
            lines.append(f"{speaker}：{item.get('content', '')}")
        return "\n".join(lines)


_store: Optional[SessionStore] = None
_store_lock = threading.Lock()


def get_session_store() -> SessionStore:
    """模块级单例（与 get_workflow / get_llm 同风格）。"""
    global _store
    with _store_lock:
        if _store is None:
            _store = SessionStore()
        return _store


__all__ = ["HistoryInjector", "SessionStore", "get_session_store"]
