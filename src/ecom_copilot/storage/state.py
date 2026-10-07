"""分片上传状态追踪：Redis BitMap + 本地降级（断点续传基础）。"""

from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Dict, List, Optional

from ..config import Settings, get_settings


class ChunkStateStore:
    """记录 upload_id 下每个分片的完成状态，支持断点续传。"""

    def mark(self, upload_id: str, index: int, total: int) -> None:
        raise NotImplementedError

    def completed(self, upload_id: str) -> List[int]:
        raise NotImplementedError

    def progress(self, upload_id: str, total: int) -> float:
        done = len(self.completed(upload_id))
        return 0.0 if not total else done / total

    def is_complete(self, upload_id: str, total: int) -> bool:
        return len(self.completed(upload_id)) >= total

    def clear(self, upload_id: str) -> None:
        raise NotImplementedError


class LocalChunkState(ChunkStateStore):
    name = "local"

    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._mem: Dict[str, set] = {}

    def _file(self, upload_id: str) -> Path:
        return self.root / f"{upload_id}.bitmap.json"

    def mark(self, upload_id: str, index: int, total: int) -> None:
        with self._lock:
            self._mem.setdefault(upload_id, set()).add(int(index))
            self._file(upload_id).write_text(
                json.dumps({"total": total, "done": sorted(self._mem[upload_id])}),
                encoding="utf-8",
            )

    def completed(self, upload_id: str) -> List[int]:
        with self._lock:
            if upload_id in self._mem:
                return sorted(self._mem[upload_id])
            path = self._file(upload_id)
            if path.exists():
                try:
                    data = json.loads(path.read_text(encoding="utf-8"))
                    self._mem[upload_id] = set(data.get("done", []))
                    return sorted(self._mem[upload_id])
                except Exception:  # noqa: BLE001
                    return []
            return []

    def clear(self, upload_id: str) -> None:
        with self._lock:
            self._mem.pop(upload_id, None)
            path = self._file(upload_id)
            if path.exists():
                path.unlink()


class RedisChunkState(ChunkStateStore):
    """Redis SETBIT / BITCOUNT 实现的 BitMap 状态追踪。"""

    name = "redis"

    def __init__(self, url: str) -> None:
        import redis  # noqa: PLC0415

        self.client = redis.Redis.from_url(url, decode_responses=True)
        self.prefix = "ecom:upload:bitmap:"
        self.meta = "ecom:upload:meta:"

    def _key(self, upload_id: str) -> str:
        return f"{self.prefix}{upload_id}"

    def mark(self, upload_id: str, index: int, total: int) -> None:
        self.client.setbit(self._key(upload_id), int(index), 1)
        self.client.set(f"{self.meta}{upload_id}", int(total))

    def completed(self, upload_id: str) -> List[int]:
        value = self.client.get(self._key(upload_id))
        if value is None:
            return []
        if isinstance(value, str):
            value = value.encode("utf-8")
        total = int(self.client.get(f"{self.meta}{upload_id}") or 0)
        done = []
        for i in range(total):
            byte = value[i // 8] if i // 8 < len(value) else 0
            if byte & (1 << (7 - (i % 8))):
                done.append(i)
        return done

    def clear(self, upload_id: str) -> None:
        self.client.delete(self._key(upload_id))
        self.client.delete(f"{self.meta}{upload_id}")


_state_store: Optional[ChunkStateStore] = None


def get_chunk_state(settings: Optional[Settings] = None) -> ChunkStateStore:
    global _state_store
    if _state_store is not None:
        return _state_store
    cfg = settings or get_settings()
    if cfg.redis_enabled:
        try:
            store = RedisChunkState(cfg.redis_url)
            store.client.ping()
            _state_store = store
            return _state_store
        except Exception:  # noqa: BLE001
            pass
    _state_store = LocalChunkState(cfg.data_dir / "uploads")
    return _state_store
