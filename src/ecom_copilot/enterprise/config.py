from __future__ import annotations
import os
from dataclasses import dataclass
from pathlib import Path
from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parents[3]
@dataclass(frozen=True)
class Config:
    dsn: str
    mode: str
    private_dir: Path
    tenant_code: str
    cookie_secure: bool
    task_timeout: int = 600
    lease_seconds: int = 45
    max_attempts: int = 3
    max_model_calls: int = 6
    max_pending: int = 50
    embed_service_url: str = ""
    hyde_enabled: bool = True
    minio_enabled: bool = False
    minio_endpoint: str = "127.0.0.1:19000"
    minio_access_key: str = ""
    minio_secret_key: str = ""
    minio_bucket: str = "ecom-private"
    presign_ttl: int = 600
    blob_dual_write: bool = False
    kafka_enabled: bool = False
    kafka_brokers: str = "127.0.0.1:9092"
    kafka_topic: str = "ecom.tasks"
    kafka_partitions: int = 6
    es_enabled: bool = False
    es_url: str = "http://127.0.0.1:9200"
    es_index: str = "chunks_v1"
    es_analyzer: str = "ik_max_word"
    es_search_analyzer: str = "ik_smart"

def load_config() -> Config:
    source = Path(os.environ.get("ECOM_ENV_FILE", ROOT / ".env.demo"))
    values = {**dotenv_values(source), **os.environ}
    mode = values.get("ECOM_MODE", "production")
    dsn = values.get("ECOM_DATABASE_URL", "")
    if not dsn or mode not in ("demo", "production", "test"):
        raise RuntimeError("企业数据库或运行环境未配置")
    private = Path(values.get("ECOM_PRIVATE_DIR", ROOT / "data/private" / mode / "private"))
    private.mkdir(parents=True,exist_ok=True,mode=0o700)
    return Config(dsn, mode, private, values.get("ECOM_TENANT_CODE","mofa"), values.get("ECOM_COOKIE_SECURE","false")=="true",
      embed_service_url=values.get("ECOM_EMBED_SERVICE_URL","http://127.0.0.1:18555"),
      hyde_enabled=values.get("ECOM_HYDE_ENABLED","true")=="true",
      minio_enabled=values.get("ECOM_MINIO_ENABLED","false")=="true",
      minio_endpoint=values.get("ECOM_MINIO_ENDPOINT","127.0.0.1:19000"),
      minio_access_key=values.get("ECOM_MINIO_ACCESS_KEY",""),
      minio_secret_key=values.get("ECOM_MINIO_SECRET_KEY",""),
      minio_bucket=values.get("ECOM_MINIO_BUCKET","ecom-private"),
      presign_ttl=int(values.get("ECOM_PRESIGN_TTL","600")),
      blob_dual_write=values.get("ECOM_BLOB_DUAL_WRITE","false")=="true",
      kafka_enabled=values.get("ECOM_KAFKA_ENABLED","false")=="true",
      kafka_brokers=values.get("ECOM_KAFKA_BROKERS","127.0.0.1:9092"),
      kafka_topic=values.get("ECOM_KAFKA_TOPIC","ecom.tasks"),
      kafka_partitions=int(values.get("ECOM_KAFKA_PARTITIONS","6")),
      es_enabled=values.get("ECOM_ES_ENABLED","false")=="true",
      es_url=values.get("ECOM_ES_URL","http://127.0.0.1:9200"),
      es_index=values.get("ECOM_ES_INDEX","chunks_v1"),
      es_analyzer=values.get("ECOM_ES_ANALYZER","ik_max_word"),
      es_search_analyzer=values.get("ECOM_ES_SEARCH_ANALYZER","ik_smart"))
