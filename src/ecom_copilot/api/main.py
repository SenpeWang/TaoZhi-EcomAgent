"""统一企业 API 入口；历史实现仅由离线回归测试直接加载。"""
import os
from pathlib import Path

def create_app():
    project = Path(__file__).resolve().parents[3]
    os.environ.setdefault("ECOM_V3_CONFIG", str(project / ".env.v3-production"))
    from ..enterprise.api import app
    return app

app = create_app()
