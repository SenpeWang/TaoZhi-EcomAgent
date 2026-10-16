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
    es_index: str = "chunks"
    es_analyzer: str = "ik_max_word"
    es_search_analyzer: str = "ik_smart"
    # ANN 相关性闸门：top-k 中无任何切片余弦距离 ≤ 该值即判资料不足（bge-small-zh 实测相关≈0.35-0.47 / 无关≈0.46-0.56）
    ann_max_distance: float = 0.45
    # RRF 融合后用本地交叉编码器（bge-reranker-v2-m3，挂嵌入服务 /rerank）对候选精排；失败自动回落融合序
    rerank_enabled: bool = True
    rerank_candidates: int = 20
    # 精排请求超时（秒）。精排是增强项：超时即回落融合序，绝不能拖慢问答主链路。
    rerank_timeout: float = 20.0
    # 检索结果单条正文字符上限（入库已按 850 窗口切分，这里是对外部写入/未来 chunker 的兜底闸门）
    quote_max_chars: int = 1200
    # 送交叉编码器的单条文本字符预算（模型本身截断 512 token，全量长文只是白烧算力）
    rerank_chars: int = 1000
    # 同一文档最多进入结果的切片数：避免 top-k 被单篇文档占满，证据多样性优先
    max_chunks_per_doc: int = 3
    # 精排分数过滤：绝对阈值挡无关噪音，相对阈值挡远落后于首条的陪跑候选
    rerank_min_score: float = 0.05
    rerank_relative: float = 0.15
    # 运维便利：登录页展示真实账号快速切换（默认关闭）
    quick_login: bool = False
    # 仅在 quick_login 打开且显式开启时，才把口令一并返回（仅限内网自用，禁止公网生产开启）
    quick_login_passwords: bool = False
    # 快捷登录统一下发的默认口令（弱口令，仅内网自用；未配置时回落到运维凭据文件）
    quick_login_password: str = ""

def load_config() -> Config:
    source = Path(os.environ.get("ECOM_ENV_FILE", ROOT / ".env.production"))
    values = {**dotenv_values(source), **os.environ}
    mode = values.get("ECOM_MODE", "production")
    dsn = values.get("ECOM_DATABASE_URL", "")
    if not dsn or mode not in ("production", "test"):
        raise RuntimeError("企业数据库或运行环境未配置（仅支持 production / test）")
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
      es_index=values.get("ECOM_ES_INDEX","chunks"),
      es_analyzer=values.get("ECOM_ES_ANALYZER","ik_max_word"),
      es_search_analyzer=values.get("ECOM_ES_SEARCH_ANALYZER","ik_smart"),
      ann_max_distance=float(values.get("ECOM_ANN_MAX_DIST","0.45")),
      rerank_enabled=values.get("ECOM_RERANK_ENABLED","true")=="true",
      rerank_candidates=int(values.get("ECOM_RERANK_CANDIDATES","20")),
      rerank_timeout=float(values.get("ECOM_RERANK_TIMEOUT","20")),
      quote_max_chars=int(values.get("ECOM_QUOTE_MAX_CHARS","1200")),
      rerank_chars=int(values.get("ECOM_RERANK_CHARS","1000")),
      max_chunks_per_doc=int(values.get("ECOM_MAX_CHUNKS_PER_DOC","3")),
      rerank_min_score=float(values.get("ECOM_RERANK_MIN_SCORE","0.05")),
      rerank_relative=float(values.get("ECOM_RERANK_RELATIVE","0.15")),
      quick_login=values.get("ECOM_QUICK_LOGIN","false")=="true",
      quick_login_passwords=values.get("ECOM_QUICK_LOGIN_PASSWORDS","false")=="true",
      quick_login_password=values.get("ECOM_QUICK_LOGIN_PASSWORD",""))
