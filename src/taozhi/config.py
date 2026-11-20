"""淘智 (TaoZhi) 全局运行配置与环境加载中心。

统一维护模型网关配置 (`Settings`) 与企业运行时配置 (`Config`)：
1. `.env` —— LLM 服务层与科研预算配置；
2. `ECOM_ENV_FILE` —— 企业数据库、存储、消息队列与检索调优配置。
严格遵循 Google Python Style 规范。
"""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

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
    log_level: str = "INFO"

    # ───────────────────── LLM 服务层（商汤 sensenova-6.8-flash 国内/海外容灾池） ─────────────────────
    base_url: str = Field(default="https://token.sensenova.cn/v1", alias="BASE_URL")
    api_key: str = Field(default="", alias="API_KEY")
    model: str = Field(default="sensenova-6.8-flash", alias="MODEL")

    # 统一模型路由（默认全量采用 sensenova-6.8-flash）
    llm_fast_model: str = ""
    llm_strong_model: str = ""
    llm_vision_model: str = ""
    llm_temperature: float = 0.2
    # 推理型模型会把预算消耗在思维链上，默认预算需留足余量
    llm_max_tokens: int = 8192
    llm_timeout: int = 180
    llm_max_retries: int = 1
    llm_empty_retries: int = Field(default=1, ge=0, le=2)
    llm_output_cap: int = Field(default=8192, ge=2048, le=32000)
    llm_max_concurrency: int = 6
    llm_direct_connection: bool = True

    # 国内第二 Key + 商汤海外容灾端点 + Dots 备选通道
    sensenova_cn_secondary_api_key: str = Field(default="", alias="SENSENOVA_CN_SECONDARY_API_KEY")
    sensenova_overseas_base_url: str = Field(default="https://token.sensenova.ai/v1", alias="SENSENOVA_OVERSEAS_BASE_URL")
    sensenova_overseas_api_key: str = Field(default="", alias="SENSENOVA_OVERSEAS_API_KEY")
    sensenova_overseas_model: str = Field(default="sensenova-6.8-flash", alias="SENSENOVA_OVERSEAS_MODEL")
    sensenova_overseas_direct_connection: bool = Field(default=False, alias="SENSENOVA_OVERSEAS_DIRECT_CONNECTION")
    dots_base_url: str = Field(default="https://note3-prev-api.askdiandian.com/v1", alias="DOTS_BASE_URL")
    dots_api_key: str = Field(default="", alias="DOTS_API_KEY")
    dots_model: str = Field(default="dots3-note-prev", alias="DOTS_MODEL")
    dots_direct_connection: bool = Field(default=True, alias="DOTS_DIRECT_CONNECTION")

    # 容灾熔断中间件冷却参数（秒）
    llm_circuit_cooldown_rate_limit: int = Field(default=45, ge=5, le=600, alias="LLM_CIRCUIT_COOLDOWN_RATE_LIMIT")
    llm_circuit_cooldown_transient: int = Field(default=25, ge=5, le=600, alias="LLM_CIRCUIT_COOLDOWN_TRANSIENT")
    llm_circuit_cooldown_auth: int = Field(default=600, ge=30, le=3600, alias="LLM_CIRCUIT_COOLDOWN_AUTH")

    # ───────────────────── 调研多智能体预算（可环境变量覆盖） ─────────────────────
    research_max_turns: int = Field(default=5, ge=1, le=20)
    research_max_parallel_subagents: int = Field(default=4, ge=1, le=8)
    research_max_subagent_steps: int = Field(default=3, ge=1, le=8)

    # ───────────────────── 业务系统对接 ─────────────────────
    business_api_url: str = ""
    business_api_token: str = ""

    def model_post_init(self, __context) -> None:
        """将未显式指定的分级模型回落至主模型。"""
        self.llm_fast_model = self.llm_fast_model or self.model
        self.llm_strong_model = self.llm_strong_model or self.model
        self.llm_vision_model = self.llm_vision_model or self.model
        self.sensenova_overseas_model = self.sensenova_overseas_model or self.model

    @property
    def has_llm(self) -> bool:
        """判断当前是否至少配置了一个可用的主备模型密钥。"""
        return bool(
            self.api_key
            or self.sensenova_cn_secondary_api_key
            or self.sensenova_overseas_api_key
            or self.dots_api_key
        )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """读取并缓存全局大模型与科研预算配置实例。"""
    return Settings()


settings: Settings = get_settings()


# ---------------------------------------------------------------------------
# 企业服务运行时配置（数据层与中间件）
# ---------------------------------------------------------------------------
from dataclasses import dataclass
from dotenv import dotenv_values

ROOT = PROJECT_ROOT
@dataclass(frozen=True)
class Config:
    """企业服务端、知识检索、中间件与任务租约运行时配置快照。"""

    dsn: str
    mode: str
    private_dir: Path
    tenant_code: str
    cookie_secure: bool
    task_timeout: int = 600
    review_timeout: int = 86400
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
    minio_secure: bool = True
    minio_public_endpoint: str = ""
    presign_ttl: int = 600
    blob_dual_write: bool = False
    runtime_dir: Path = Path.home() / ".local" / "share" / "ecom-agent"
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
    rerank_candidates: int = 30
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
    # 切片与检索调优参数（均可通过 ECOM_* 环境变量覆盖）
    chunk_size: int = 850
    chunk_overlap: int = 200
    adaptive_hyde: bool = True
    hyde_rerank_threshold: float = 0.65
    hyde_max_tokens: int = 220
    hyde_passage_chars: int = 800
    recency_boost: float = 0.08
    entity_quota: int = 2
    tfidf_max_features: int = 24000
    # 语义缓存参数（均可通过 ECOM_SEMANTIC_CACHE_* 覆盖）
    semantic_cache_threshold: float = 0.96
    semantic_cache_ttl: int = 3600
    semantic_cache_max_entries: int = 10000
    # 专家智能体推理参数（均可通过 ECOM_SPECIALIST_* 覆盖）
    specialist_timeout: int = 45
    specialist_max_tokens: int = 4096
    specialist_concurrency: int = 2
    # API 基址（供评测或客户端动态解析，不写死）
    api_base_url: str = "http://127.0.0.1:18080"
    # 运维便利：登录页展示真实账号快速切换（默认关闭）
    quick_login: bool = False
    # 仅在 quick_login 打开且显式开启时，才把口令一并返回（仅限内网自用，禁止公网生产开启）
    quick_login_passwords: bool = False
    # 快捷登录统一下发的默认口令（弱口令，仅内网自用；未配置时回落到运维凭据文件）
    quick_login_password: str = ""


def resolve_runtime_dir(env_values: dict[str, str | None] | None = None) -> Path:
    """按环境变量或用户默认状态目录解析运行时根目录（不依赖数据库连接）。"""
    env_map = os.environ if env_values is None else env_values
    raw = str(env_map.get("ECOM_RUNTIME_DIR") or "").strip()
    if raw:
        return Path(raw)
    return Path.home() / ".local" / "share" / "ecom-agent"


def load_config() -> Config:
    """从 ECOM_ENV_FILE 与进程环境变量加载企业在线运行时配置。

    Returns:
        校验通过的不可变 Config 对象。

    Raises:
        RuntimeError: 当 ECOM_DATABASE_URL 缺失或 ECOM_MODE 非法时抛出。
    """
    source = Path(os.environ.get("ECOM_ENV_FILE", ROOT / ".env.production"))
    values = {**dotenv_values(source), **os.environ}
    mode = values.get("ECOM_MODE", "production")
    dsn = values.get("ECOM_DATABASE_URL", "")
    if not dsn or mode not in ("production", "test"):
        raise RuntimeError("企业数据库或运行环境未配置（仅支持 production / test）")
    private = Path(values.get("ECOM_PRIVATE_DIR", ROOT / "data/private" / mode / "private"))
    private.mkdir(parents=True, exist_ok=True, mode=0o700)
    minio_ep = values.get("ECOM_MINIO_ENDPOINT", "127.0.0.1:19000")
    minio_secure_raw = values.get("ECOM_MINIO_SECURE")
    minio_secure = (
        not minio_ep.startswith(("127.0.0.1", "localhost"))
        if minio_secure_raw is None
        else minio_secure_raw == "true"
    )
    runtime_dir = resolve_runtime_dir(values)
    return Config(
        dsn,
        mode,
        private,
        values.get("ECOM_TENANT_CODE", "mofa"),
        values.get("ECOM_COOKIE_SECURE", "false") == "true",
        review_timeout=int(values.get("ECOM_REVIEW_TIMEOUT", "86400")),
        embed_service_url=values.get("ECOM_EMBED_SERVICE_URL", "http://127.0.0.1:18555"),
        hyde_enabled=values.get("ECOM_HYDE_ENABLED", "true") == "true",
        minio_enabled=values.get("ECOM_MINIO_ENABLED", "false") == "true",
        minio_endpoint=minio_ep,
        minio_access_key=values.get("ECOM_MINIO_ACCESS_KEY", ""),
        minio_secret_key=values.get("ECOM_MINIO_SECRET_KEY", ""),
        minio_bucket=values.get("ECOM_MINIO_BUCKET", "ecom-private"),
        minio_secure=minio_secure,
        minio_public_endpoint=values.get("ECOM_MINIO_PUBLIC_ENDPOINT", "").strip(),
        presign_ttl=int(values.get("ECOM_PRESIGN_TTL", "600")),
        blob_dual_write=values.get("ECOM_BLOB_DUAL_WRITE", "false") == "true",
        runtime_dir=runtime_dir,
        kafka_enabled=values.get("ECOM_KAFKA_ENABLED", "false") == "true",
        kafka_brokers=values.get("ECOM_KAFKA_BROKERS", "127.0.0.1:9092"),
        kafka_topic=values.get("ECOM_KAFKA_TOPIC", "ecom.tasks"),
        kafka_partitions=int(values.get("ECOM_KAFKA_PARTITIONS", "6")),
        es_enabled=values.get("ECOM_ES_ENABLED", "false") == "true",
        es_url=values.get("ECOM_ES_URL", "http://127.0.0.1:9200"),
        es_index=values.get("ECOM_ES_INDEX", "chunks"),
        es_analyzer=values.get("ECOM_ES_ANALYZER", "ik_max_word"),
        es_search_analyzer=values.get("ECOM_ES_SEARCH_ANALYZER", "ik_smart"),
        ann_max_distance=float(values.get("ECOM_ANN_MAX_DIST", "0.45")),
        rerank_enabled=values.get("ECOM_RERANK_ENABLED", "true") == "true",
        rerank_candidates=int(values.get("ECOM_RERANK_CANDIDATES", "30")),
        rerank_timeout=float(values.get("ECOM_RERANK_TIMEOUT", "20")),
        quote_max_chars=int(values.get("ECOM_QUOTE_MAX_CHARS", "1200")),
        rerank_chars=int(values.get("ECOM_RERANK_CHARS", "1000")),
        max_chunks_per_doc=int(values.get("ECOM_MAX_CHUNKS_PER_DOC", "3")),
        rerank_min_score=float(values.get("ECOM_RERANK_MIN_SCORE", "0.05")),
        rerank_relative=float(values.get("ECOM_RERANK_RELATIVE", "0.15")),
        chunk_size=int(values.get("ECOM_CHUNK_SIZE", "850")),
        chunk_overlap=int(values.get("ECOM_CHUNK_OVERLAP", "200")),
        adaptive_hyde=values.get("ECOM_ADAPTIVE_HYDE", "true") == "true",
        hyde_rerank_threshold=float(values.get("ECOM_HYDE_RERANK_THRESHOLD", "0.65")),
        hyde_max_tokens=int(values.get("ECOM_HYDE_MAX_TOKENS", "220")),
        hyde_passage_chars=int(values.get("ECOM_HYDE_PASSAGE_CHARS", "800")),
        recency_boost=float(values.get("ECOM_RECENCY_BOOST", "0.08")),
        entity_quota=int(values.get("ECOM_ENTITY_QUOTA", "2")),
        tfidf_max_features=int(values.get("ECOM_TFIDF_MAX_FEATURES", "24000")),
        semantic_cache_threshold=float(values.get("ECOM_SEMANTIC_CACHE_THRESHOLD", "0.96")),
        semantic_cache_ttl=int(values.get("ECOM_SEMANTIC_CACHE_TTL", "3600")),
        semantic_cache_max_entries=int(values.get("ECOM_SEMANTIC_CACHE_MAX_ENTRIES", "10000")),
        specialist_timeout=int(values.get("ECOM_SPECIALIST_TIMEOUT", "45")),
        specialist_max_tokens=int(values.get("ECOM_SPECIALIST_MAX_TOKENS", "4096")),
        specialist_concurrency=int(values.get("ECOM_SPECIALIST_CONCURRENCY", "2")),
        api_base_url=values.get("ECOM_API_BASE_URL", "http://127.0.0.1:18080"),
        quick_login=values.get("ECOM_QUICK_LOGIN", "false") == "true",
        quick_login_passwords=values.get("ECOM_QUICK_LOGIN_PASSWORDS", "false") == "true",
        quick_login_password=values.get("ECOM_QUICK_LOGIN_PASSWORD", ""),
    )
