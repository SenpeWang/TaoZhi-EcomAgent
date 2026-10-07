"""电商商品知识智能问答系统 —— 全局配置（Pydantic Settings v2）。

配置优先级：环境变量 > .env 文件 > 代码默认值。
优先读取项目 .env，再回退上级目录；可通过环境变量 ENV_FILE 覆盖。
"""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Optional

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

# <...>/00_base/multi-agent
PROJECT_ROOT: Path = Path(__file__).resolve().parents[2]
# <...>/00_base/.env
DEFAULT_ENV_FILE: Path = PROJECT_ROOT.parent / ".env"

_ENV_FILE = os.getenv("ENV_FILE") or (
    str(PROJECT_ROOT / ".env") if (PROJECT_ROOT / ".env").exists() else str(DEFAULT_ENV_FILE)
)


class Settings(BaseSettings):
    """系统级配置。所有字段均可在 .env / 环境变量中覆盖。"""

    model_config = SettingsConfigDict(
        env_file=_ENV_FILE,
        env_file_encoding="utf-8",
        extra="ignore",
        populate_by_name=True,
        case_sensitive=False,
    )

    # ───────────────────────── 基本 ─────────────────────────
    app_name: str = "电商商品知识智能问答系统"
    app_env: str = Field(default="dev", alias="APP_ENV")
    log_level: str = "INFO"

    data_dir: Path = PROJECT_ROOT / "data"
    reports_dir: Path = PROJECT_ROOT / "data" / "reports"
    checkpoint_db: Path = PROJECT_ROOT / "data" / "archive" / "langgraph.sqlite"

    # ───────────────────── LLM 服务层（模型分级路由） ─────────────────────
    base_url: str = Field(default="https://token.sensenova.cn/v1", alias="BASE_URL")
    api_key: str = Field(default="", alias="API_KEY")
    model: str = Field(default="sensenova-6.8-flash-lite", alias="MODEL")

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

    # ───────────────────── Embedding ─────────────────────
    embedding_base_url: str = ""
    embedding_api_key: str = ""
    embedding_model: str = "BAAI/bge-m3"
    embedding_dim: int = 256
    embedding_device: str = "cpu"

    # ───────────────────── 检索层 ─────────────────────
    retrieval_first_enabled: bool = True
    retrieval_top_k: int = 8
    retrieval_candidate_multiplier: int = 4
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

    postgres_enabled: bool = False
    postgres_dsn: str = ""  # 可选离线连接仅从私有环境配置读取

    # ───────────────────── 安全 / 权限 ─────────────────────
    jwt_secret: str = "ecom-copilot-dev-secret"
    jwt_algorithm: str = "HS256"
    jwt_expire_minutes: int = 720
    auth_enabled: bool = False
    auth_default_org: str = "手机配件公司"
    prompt_injection_guard: bool = True

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
    context_max_chars: int = 24000

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
    jina_api_key: str = ""

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
