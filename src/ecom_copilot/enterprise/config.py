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

def load_config() -> Config:
    source = Path(os.environ.get("ECOM_V3_CONFIG", ROOT / ".env.v3-demo"))
    values = {**dotenv_values(source), **os.environ}
    mode = values.get("V3_MODE", "production")
    dsn = values.get("V3_DATABASE_URL", "")
    if not dsn or mode not in ("demo", "production", "test"):
        raise RuntimeError("企业数据库或运行环境未配置")
    private = Path(values.get("V3_PRIVATE_DIR", ROOT / "data/v3" / mode / "private"))
    private.mkdir(parents=True,exist_ok=True,mode=0o700)
    return Config(dsn, mode, private, values.get("V3_TENANT_CODE","mofa"), values.get("V3_COOKIE_SECURE","false")=="true")
