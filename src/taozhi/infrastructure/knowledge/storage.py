"""附件对象存储层：MinIO 为主，本地私有目录为回退。

所有附件读写统一经此模块，业务语义约定：
- 对象键 = <tenant_id>/<blob_name>，桶内前缀即租户隔离；
- 非 boss 下载永远走应用侧脱敏流式输出，不适用预签名直链；
- 预签名 URL 只在审计落库后签发，短时效（默认 600 秒）。
"""
from __future__ import annotations
import io
import logging
from taozhi.config import load_config

_LOG = logging.getLogger(__name__)

class LocalBlobStore:
    """回退实现：沿用 data/private/<mode>/<tenant>/ 目录。"""
    def __init__(self, cfg):
        self.cfg = cfg
    def _path(self, tenant_id, name):
        """解析租户私有目录下的附件路径并确保目录存在。

        Args:
            tenant_id: 租户编号。
            name: 附件相对路径。

        Returns:
            附件的绝对路径。
        """
        folder = self.cfg.private_dir / tenant_id
        folder.mkdir(parents=True, exist_ok=True, mode=0o700)
        return folder / name
    def put_blob(self, tenant_id, name, data: bytes):
        """把字节写入本地私有目录，并收紧为仅属主可读写。

        Args:
            tenant_id: 租户编号。
            name: 附件相对路径。
            data: 附件字节内容。
        """
        path = self._path(tenant_id, name)
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        path.write_bytes(data)
        path.chmod(0o600)
    def remove_blob(self, tenant_id, name):
        """删除本地附件文件；文件不存在时忽略。

        Args:
            tenant_id: 租户编号。
            name: 附件相对路径。
        """
        self._path(tenant_id, name).unlink(missing_ok=True)
    def open_blob(self, tenant_id, name) -> bytes:
        """读取本地附件字节。

        Args:
            tenant_id: 租户编号。
            name: 附件相对路径。

        Returns:
            附件字节内容。

        Raises:
            FileNotFoundError: 附件不存在时抛出。
        """
        path = self._path(tenant_id, name)
        if not path.is_file():
            raise FileNotFoundError(name)
        return path.read_bytes()
    def presign_url(self, tenant_id, name, ttl=None):
        """本地磁盘存储不提供直链，返回 None 由下载接口回退文件流。"""
        return None


class MinioBlobStore:
    """基于 MinIO 对象存储的租户隔离附件存储实现。"""
    def __init__(self, cfg):
        from minio import Minio
        from minio.versioningconfig import ENABLED, VersioningConfig
        self.cfg = cfg
        self.client = Minio(
            cfg.minio_endpoint,
            cfg.minio_access_key,
            cfg.minio_secret_key,
            secure=getattr(cfg, "minio_secure", True),
        )
        if not self.client.bucket_exists(cfg.minio_bucket):
            self.client.make_bucket(cfg.minio_bucket)
            self.client.set_bucket_versioning(cfg.minio_bucket, VersioningConfig(ENABLED))
    def _key(self, tenant_id, name):
        """拼接桶内对象键：租户编号即隔离前缀。

        Args:
            tenant_id: 租户编号。
            name: 附件相对路径。

        Returns:
            桶内对象键。
        """
        return f"{tenant_id}/{name}"
    def put_blob(self, tenant_id, name, data: bytes):
        """把字节写入 MinIO 桶。

        Args:
            tenant_id: 租户编号。
            name: 附件相对路径。
            data: 附件字节内容。
        """
        self.client.put_object(self.cfg.minio_bucket, self._key(tenant_id, name), io.BytesIO(data), len(data))
    def remove_blob(self, tenant_id, name):
        """删除 MinIO 对象；对象本就不存在时忽略。

        Args:
            tenant_id: 租户编号。
            name: 附件相对路径。
        """
        from minio.error import S3Error
        try:
            self.client.remove_object(self.cfg.minio_bucket, self._key(tenant_id, name))
        except S3Error as exc:
            if exc.code not in {"NoSuchKey", "NoSuchObject"}:
                _LOG.warning("附件对象清理失败（错误码=%s）", exc.code)
    def open_blob(self, tenant_id, name) -> bytes:
        """读取 MinIO 对象字节后关闭响应。

        Args:
            tenant_id: 租户编号。
            name: 附件相对路径。

        Returns:
            附件字节内容。
        """
        resp = self.client.get_object(self.cfg.minio_bucket, self._key(tenant_id, name))
        try:
            return resp.read()
        finally:
            resp.close()
            resp.release_conn()
    def presign_url(self, tenant_id, name, ttl=None):
        """签发短时效的附件下载直链。

        Args:
            tenant_id: 租户编号。
            name: 附件相对路径。
            ttl: 有效期秒数；缺省使用配置值。

        Returns:
            预签名 GET URL。
        """
        from datetime import timedelta
        return self.client.presigned_get_object(self.cfg.minio_bucket, self._key(tenant_id, name), expires=timedelta(seconds=ttl or self.cfg.presign_ttl))

class DualBlobStore:
    """双写实现：MinIO 为主写、本地私有目录为镜像写，读优先 MinIO 并回落本地。

    用于迁移期（ECOM_BLOB_DUAL_WRITE="true"）：两侧同时落盘并在核验一致后关闭镜像写。
    """
    def __init__(self, primary, mirror):
        self.primary = primary
        self.mirror = mirror
    def put_blob(self, tenant_id, name, data: bytes):
        """主写与镜像写同时落盘，保证迁移期两侧一致。

        Args:
            tenant_id: 租户编号。
            name: 附件相对路径。
            data: 附件字节内容。
        """
        self.primary.put_blob(tenant_id, name, data)
        self.mirror.put_blob(tenant_id, name, data)
    def remove_blob(self, tenant_id, name):
        """同时删除主写与镜像对象。

        Args:
            tenant_id: 租户编号。
            name: 附件相对路径。
        """
        self.primary.remove_blob(tenant_id, name)
        self.mirror.remove_blob(tenant_id, name)
    def open_blob(self, tenant_id, name) -> bytes:
        """优先读取主写对象，失败时回退本地镜像。

        Args:
            tenant_id: 租户编号。
            name: 附件相对路径。

        Returns:
            附件字节内容。
        """
        try:
            return self.primary.open_blob(tenant_id, name)
        except Exception as exc:
            _LOG.warning("MinIO 读取失败，回退本地镜像（%s）", type(exc).__name__)
            return self.mirror.open_blob(tenant_id, name)
    def presign_url(self, tenant_id, name, ttl=None):
        """使用主写存储签发下载直链。

        Args:
            tenant_id: 租户编号。
            name: 附件相对路径。
            ttl: 有效期秒数。

        Returns:
            预签名 GET URL。
        """
        return self.primary.presign_url(tenant_id, name, ttl)

def get_blob_store():
    """按配置返回存储实现；MinIO 连接失败自动回落本地目录并告警。

    开启 ECOM_BLOB_DUAL_WRITE 时返回双写实现，迁移期内两侧保持一致。
    """
    cfg = load_config()
    if cfg.minio_enabled:
        try:
            store = MinioBlobStore(cfg)
            if cfg.blob_dual_write:
                return DualBlobStore(store, LocalBlobStore(cfg))
            return store
        except Exception as exc:
            _LOG.warning("MinIO 不可用，附件回退本地目录（异常类型=%s）", type(exc).__name__)
    return LocalBlobStore(cfg)
