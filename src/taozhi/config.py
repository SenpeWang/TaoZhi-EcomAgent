"""淘智 (TaoZhi) 全局运行配置与环境加载中心。

统一维护大模型参数、数据库连接池、中间件开关与租户运行上下文。
严格遵循 Google Python Style 规范。
"""

from __future__ import annotations


"""电商商品知识智能问答系统 —— 全局配置（Pydantic Settings v2）。

配置优先级：环境变量 > .env 文件 > 代码默认值。

两份文件叠加读取，后者优先：
1. 项目根 .env —— LLM 服务层配置所在地（BASE_URL / API_KEY / MODEL）。
   企业链路刻意不读它，只认 ECOM_ENV_FILE，两边键名不重叠；
2. ECOM_ENV_FILE 指向的环境文件 —— 让切环境时（make test 用 .env.test）
   本层配置也能跟着切。

只认 ECOM_ENV_FILE：仓库里没有第二个名字，别再发明 ENV_FILE 这类开关。
"""


import os
from functools import lru_cache
from pathlib import Path
from typing import Optional

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

# <...>/TaoZhi-EcomAgent
PROJECT_ROOT: Path = Path(__file__).resolve().parents[2]

# 只读仓库内的 .env，缺了就当没有：不回落到上级目录那种仓库外、非版本控制
# 的文件——读到一个来路不明的 .env 比读不到更难排查。
_ENV_FILES = [str(PROJECT_ROOT / ".env")] if (PROJECT_ROOT / ".env").exists() else []
_ecom_env_file = os.getenv("ECOM_ENV_FILE")
if _ecom_env_file:
    _ENV_FILES.append(_ecom_env_file)


class Settings(BaseSettings):
    """系统级配置。所有字段均可在 .env / 环境变量中覆盖。"""

    model_config = SettingsConfigDict(
        env_file=tuple(_ENV_FILES),
        env_file_encoding="utf-8",
        extra="ignore",
        populate_by_name=True,
        case_sensitive=False,
    )

    # ───────────────────────── 基本 ─────────────────────────
    app_name: str = "TaoZhi-EcomAgent 电商商品知识多智能体中台"
    app_env: str = Field(default="dev", alias="APP_ENV")
    log_level: str = "INFO"

    data_dir: Path = PROJECT_ROOT / "data"
    reports_dir: Path = PROJECT_ROOT / "data" / "reports"
    checkpoint_db: Path = PROJECT_ROOT / "data" / "archive" / "langgraph.sqlite"

    # ───────────────────── LLM 服务层（模型分级路由） ─────────────────────
    base_url: str = Field(default="https://token.sensenova.cn/v1", alias="BASE_URL")
    api_key: str = Field(default="", alias="API_KEY")
    model: str = Field(default="sensenova-6.8-flash", alias="MODEL")

    # 分级路由：日常解析用轻量模型，复杂研判用强模型，多模态用视觉模型
    llm_fast_model: str = ""
    llm_strong_model: str = ""
    llm_vision_model: str = ""
    llm_temperature: float = 0.2
    # 推理型模型会把预算消耗在思维链上，默认预算需留足余量
    llm_max_tokens: int = 8192
    llm_timeout: int = 180
    llm_max_retries: int = 1
    llm_empty_retries: int = Field(default=1,ge=0,le=2)
    llm_output_cap: int = Field(default=8192,ge=2048,le=32000)
    llm_max_concurrency: int = 6
    llm_fallback_base_url: str = Field(default="", alias="LLM_FALLBACK_BASE_URL")
    llm_fallback_api_key: str = Field(default="", alias="LLM_FALLBACK_API_KEY")
    llm_fallback_model: str = Field(default="", alias="LLM_FALLBACK_MODEL")

    # ───────────────────── Embedding ─────────────────────
    embedding_base_url: str = ""
    embedding_api_key: str = ""
    embedding_model: str = "BAAI/bge-m3"
    embedding_dim: int = 256
    embedding_device: str = "cpu"

    # ───────────────────── 检索层 ─────────────────────
    retrieval_first_enabled: bool = True
    retrieval_top_k: int = 8
    rrf_k: int = 60
    hyde_enabled: bool = True
    rerank_enabled: bool = True
    cache_ttl_seconds: int = 300

    # ───────────────────── 基础设施（均可降级为本地实现） ─────────────────────
    milvus_enabled: bool = False
    milvus_uri: str = "http://localhost:19530"
    milvus_collection: str = "ecom_knowledge"

    es_enabled: bool = False
    es_url: str = "http://localhost:9200"
    es_index: str = "ecom_bm25"

    neo4j_enabled: bool = False
    neo4j_uri: str = "bolt://localhost:7687"
    neo4j_user: str = "neo4j"
    neo4j_password: str = "ecom123"

    minio_enabled: bool = False
    minio_endpoint: str = "localhost:9000"
    minio_access_key: str = "minioadmin"
    minio_secret_key: str = "minioadmin"
    minio_bucket: str = "ecom-docs"

    kafka_enabled: bool = False
    kafka_bootstrap: str = "localhost:9092"
    kafka_topic_parse: str = "doc.parse"

    redis_enabled: bool = False
    redis_url: str = "redis://localhost:6379/0"

    # ───────────────────── 安全 / 权限 ─────────────────────
    jwt_secret: str = "ecom-copilot-dev-secret"
    jwt_algorithm: str = "HS256"
    jwt_expire_minutes: int = 720
    auth_enabled: bool = False
    auth_default_org: str = "手机配件公司"

    business_api_url: str = ""
    business_api_token: str = ""
    business_snapshot_max_age: int = Field(default=60,ge=1,le=300)

    # Durable task service; one process owns the local index/checkpoints.
    task_max_pending: int = Field(default=50, ge=1, le=500)
    task_timeout_seconds: int = Field(default=900, ge=10, le=3600)
    specialist_timeout_seconds: float = Field(default=90, gt=0, le=300)
    cors_origins: list[str] = Field(default_factory=lambda: ["http://localhost:8501"])
    max_upload_bytes: int = Field(default=20 * 1024 * 1024, ge=1)
    max_agent_iterations: int = Field(default=24, ge=6, le=60)
    llm_direct_connection: bool = True

    # ───────────────────── Agent 编排 ─────────────────────
    max_parallel_subagents: int = 4
    max_hypothesis_rounds: int = 6
    max_debate_rounds: int = 2
    min_evidence_per_hypothesis: int = 2
    confidence_review_threshold: float = 0.6
    citation_coverage_target: float = 0.95

    # ───────────────────── 会话记忆 / FAQ 缓存 / 拒答门禁 ─────────────────────
    memory_enabled: bool = Field(default=True, alias="MEMORY_ENABLED")
    memory_history_turns: int = Field(default=6, alias="MEMORY_HISTORY_TURNS")
    memory_summary_threshold: int = Field(default=3000, alias="MEMORY_SUMMARY_THRESHOLD")
    faq_cache_sim_threshold: float = Field(default=0.92, alias="FAQ_CACHE_SIM_THRESHOLD")
    reject_confidence_threshold: float = Field(default=0.3, alias="REJECT_CONFIDENCE_THRESHOLD")

    # ───────────────────── 可观测性 ─────────────────────
    langsmith_enabled: bool = False
    langsmith_api_key: str = ""
    langsmith_project: str = "ecom-copilot"
    trace_dir: Path = PROJECT_ROOT / "data" / "traces"
    audit_dir: Path = PROJECT_ROOT / "data" / "audit"

    # ───────────────────── 数据采集 ─────────────────────
    request_timeout: int = 30
    user_agent: str = (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    )
    # 无网络 / 被封禁时启用 LLM 合成数据兜底（PRD 风险对策）
    synthetic_fallback_enabled: bool = False
    offline_mode: bool = False

    def model_post_init(self, __context) -> None:  # noqa: D105
        if self.app_env.lower() in ("prod", "production"):
            if not self.auth_enabled or len(self.jwt_secret) < 32 or self.jwt_secret == "ecom-copilot-dev-secret":
                raise ValueError("Production requires AUTH_ENABLED=true and a strong JWT_SECRET")
            if self.synthetic_fallback_enabled:
                raise ValueError("Production forbids synthetic evidence fallback")
            if "*" in self.cors_origins:
                raise ValueError("Production CORS must use explicit origins")
        self.data_dir = Path(self.data_dir)
        self.reports_dir = Path(self.reports_dir)
        self.checkpoint_db = Path(self.checkpoint_db)
        self.trace_dir = Path(self.trace_dir)
        self.audit_dir = Path(self.audit_dir)
        for p in (
            self.data_dir,
            self.reports_dir,
            self.checkpoint_db.parent,
            self.trace_dir,
            self.audit_dir,
        ):
            p.mkdir(parents=True, exist_ok=True)

        self.llm_fast_model = self.llm_fast_model or self.model
        self.llm_strong_model = self.llm_strong_model or self.model
        self.llm_vision_model = self.llm_vision_model or self.model
        # Remote embedding requires separately configured credentials.


    # ───────────────────── 便捷属性 ─────────────────────
    @property
    def env_file(self) -> str:
        return _ENV_FILE

    @property
    def has_llm(self) -> bool:
        return bool(self.api_key)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()


settings: Settings = get_settings()


# ---------------------------------------------------------------------------
# 企业服务运行时配置（数据层与中间件）
# ---------------------------------------------------------------------------
import os
from dataclasses import dataclass
from pathlib import Path
from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parents[2]
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
