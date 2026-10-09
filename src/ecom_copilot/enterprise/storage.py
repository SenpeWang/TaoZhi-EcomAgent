"""附件对象存储层：MinIO 为主，本地私有目录为回退。

所有附件读写统一经此模块，业务语义约定：
- 对象键 = <tenant_id>/<blob_name>，桶内前缀即租户隔离；
- 非 boss 下载永远走应用侧脱敏流式输出，不适用预签名直链；
- 预签名 URL 只在审计落库后签发，短时效（默认 600 秒）。
"""
from __future__ import annotations
import io
from .config import load_config

class LocalBlobStore:
    """回退实现：沿用 data/private/<mode>/<tenant>/ 目录。"""
    def __init__(self, cfg):
        self.cfg = cfg
    def _path(self, tenant_id, name):
        folder = self.cfg.private_dir / tenant_id
        folder.mkdir(parents=True, exist_ok=True, mode=0o700)
        return folder / name
    def put_blob(self, tenant_id, name, data: bytes):
        path = self._path(tenant_id, name)
        path.write_bytes(data)
        path.chmod(0o600)
    def remove_blob(self, tenant_id, name):
        self._path(tenant_id, name).unlink(missing_ok=True)
    def open_blob(self, tenant_id, name) -> bytes:
        path = self._path(tenant_id, name)
        if not path.is_file():
            raise FileNotFoundError(name)
        return path.read_bytes()
    def presign_url(self, tenant_id, name, ttl=None):
        return None  # 本地盘无直链语义，下载端回退 FileResponse

class MinioBlobStore:
    def __init__(self, cfg):
        from minio import Minio
        from minio.versioningconfig import ENABLED, VersioningConfig
        self.cfg = cfg
        self.client = Minio(cfg.minio_endpoint, cfg.minio_access_key, cfg.minio_secret_key, secure=False)
        if not self.client.bucket_exists(cfg.minio_bucket):
            self.client.make_bucket(cfg.minio_bucket)
            self.client.set_bucket_versioning(cfg.minio_bucket, VersioningConfig(ENABLED))
    def _key(self, tenant_id, name):
        return f"{tenant_id}/{name}"
    def put_blob(self, tenant_id, name, data: bytes):
        self.client.put_object(self.cfg.minio_bucket, self._key(tenant_id, name), io.BytesIO(data), len(data))
    def remove_blob(self, tenant_id, name):
        from minio.error import S3Error
        try:
            self.client.remove_object(self.cfg.minio_bucket, self._key(tenant_id, name))
        except S3Error:
            pass
    def open_blob(self, tenant_id, name) -> bytes:
        resp = self.client.get_object(self.cfg.minio_bucket, self._key(tenant_id, name))
        try:
            return resp.read()
        finally:
            resp.close()
            resp.release_conn()
    def presign_url(self, tenant_id, name, ttl=None):
        from datetime import timedelta
        return self.client.presigned_get_object(self.cfg.minio_bucket, self._key(tenant_id, name), expires=timedelta(seconds=ttl or self.cfg.presign_ttl))

class DualBlobStore:
    """双写实现：MinIO 为主写、本地私有目录为镜像写，读优先 MinIO 并回落本地。

    用于迁移期（ECOM_BLOB_DUAL_WRITE="true"）：两侧同时落盘后用
    scripts/verify_blob_migration.py 比对哈希，全部一致后再关闭镜像写。
    """
    def __init__(self, primary, mirror):
        self.primary = primary
        self.mirror = mirror
    def put_blob(self, tenant_id, name, data: bytes):
        self.primary.put_blob(tenant_id, name, data)
        self.mirror.put_blob(tenant_id, name, data)
    def remove_blob(self, tenant_id, name):
        self.primary.remove_blob(tenant_id, name)
        self.mirror.remove_blob(tenant_id, name)
    def open_blob(self, tenant_id, name) -> bytes:
        try:
            return self.primary.open_blob(tenant_id, name)
        except Exception:
            return self.mirror.open_blob(tenant_id, name)
    def presign_url(self, tenant_id, name, ttl=None):
        return self.primary.presign_url(tenant_id, name, ttl)

def get_blob_store():
    """按配置返回存储实现；MinIO 连接失败自动回落本地目录并告警。

    开启 ECOM_BLOB_DUAL_WRITE 时返回双写实现，迁移期内两侧保持一致。
    """
    cfg = load_config()
    if cfg.minio_enabled:
        try:
            store = MinioBlobStore(cfg)
            store.client.bucket_exists(cfg.minio_bucket)  # 连通性探测
            if cfg.blob_dual_write:
                return DualBlobStore(store, LocalBlobStore(cfg))
            return store
        except Exception as exc:
            print("MinIO 不可用，附件回退本地目录:", type(exc).__name__, flush=True)
    return LocalBlobStore(cfg)
