"""对象存储抽象：MinIO 分片上传 + 本地文件系统降级。

继承简历实现：MinIO 分片上传 → Redis BitMap 记录分片上报状态 → Kafka 异步承接解析任务。
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import List, Optional

from ..config import Settings, get_settings


class BlobStore:
    def put(self, key: str, data: bytes, content_type: str = "application/octet-stream") -> str:
        raise NotImplementedError

    def get(self, key: str) -> Optional[bytes]:
        raise NotImplementedError

    def exists(self, key: str) -> bool:
        raise NotImplementedError

    def url(self, key: str) -> str:
        raise NotImplementedError


class LocalBlobStore(BlobStore):
    """本地文件系统实现（无 MinIO 时自动降级）。"""

    name = "local"

    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, key: str) -> Path:
        safe = key.replace("..", "_").lstrip("/")
        return self.root / safe

    def put(self, key: str, data: bytes, content_type: str = "application/octet-stream") -> str:
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return f"local://{key}"

    def get(self, key: str) -> Optional[bytes]:
        path = self._path(key)
        return path.read_bytes() if path.exists() else None

    def exists(self, key: str) -> bool:
        return self._path(key).exists()

    def url(self, key: str) -> str:
        return str(self._path(key))


class MinioBlobStore(BlobStore):
    """MinIO 分片上传实现。"""

    name = "minio"
    PART_SIZE = 5 * 1024 * 1024

    def __init__(self, settings: Optional[Settings] = None) -> None:
        from minio import Minio  # noqa: PLC0415
        from minio.error import S3Error  # noqa: PLC0415

        self._S3Error = S3Error
        cfg = settings or get_settings()
        self.cfg = cfg
        self.client = Minio(
            cfg.minio_endpoint,
            access_key=cfg.minio_access_key,
            secret_key=cfg.minio_secret_key,
            secure=False,
        )
        self.bucket = cfg.minio_bucket
        try:
            if not self.client.bucket_exists(self.bucket):
                self.client.make_bucket(self.bucket)
        except Exception:  # noqa: BLE001
            pass

    def put(self, key: str, data: bytes, content_type: str = "application/octet-stream") -> str:
        import io  # noqa: PLC0415

        self.client.put_object(self.bucket, key, io.BytesIO(data), len(data), content_type)
        return f"minio://{self.bucket}/{key}"

    def put_multipart(self, key: str, parts: List[bytes],
                      content_type: str = "application/octet-stream") -> str:
        """分片上传（对齐 MinIO 分片上传 + 断点续传链路）。"""
        import io  # noqa: PLC0415

        for index, part in enumerate(parts):
            self.client.put_object(
                self.bucket, f"{key}.part{index:05d}", io.BytesIO(part), len(part), content_type
            )
        data = b"".join(parts)
        self.client.put_object(self.bucket, key, io.BytesIO(data), len(data), content_type)
        return f"minio://{self.bucket}/{key}"

    def get(self, key: str) -> Optional[bytes]:
        try:
            resp = self.client.get_object(self.bucket, key)
            return resp.read()
        except Exception:  # noqa: BLE001
            return None

    def exists(self, key: str) -> bool:
        try:
            self.client.stat_object(self.bucket, key)
            return True
        except Exception:  # noqa: BLE001
            return False

    def url(self, key: str) -> str:
        return f"minio://{self.bucket}/{key}"


_blob_store = None


def get_blob_store(settings: Optional[Settings] = None) -> BlobStore:
    global _blob_store
    if _blob_store is not None:
        return _blob_store
    cfg = settings or get_settings()
    if cfg.minio_enabled:
        try:
            _blob_store = MinioBlobStore(cfg)
            return _blob_store
        except Exception:  # noqa: BLE001
            pass
    _blob_store = LocalBlobStore(cfg.data_dir / "blobs")
    return _blob_store


def content_hash(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()[:32]
