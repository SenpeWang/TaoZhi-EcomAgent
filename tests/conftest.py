"""pytest 配置：确保 src 在 sys.path 中，并隔离测试数据目录。"""

import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

# Offline test configuration never borrows deployment credentials.
os.environ.setdefault("APP_ENV","dev")
os.environ.setdefault("AUTH_ENABLED","false")
os.environ.setdefault("API_KEY","")
os.environ.setdefault("EMBEDDING_API_KEY","")

# 测试期间使用临时数据目录，避免污染仓库
_TMP = tempfile.mkdtemp(prefix="industry-copilot-test-")
os.environ.setdefault("DATA_DIR", str(Path(_TMP) / "data"))
os.environ.setdefault("REPORTS_DIR", str(Path(_TMP) / "reports"))
os.environ.setdefault("TRACE_DIR", str(Path(_TMP) / "traces"))
os.environ.setdefault("AUDIT_DIR", str(Path(_TMP) / "audit"))
os.environ.setdefault("CHECKPOINT_DB", str(Path(_TMP) / "langgraph.sqlite"))


def pytest_configure(config):  # noqa: ANN001
    config.addinivalue_line("markers", "slow: 需要调用 LLM 的端到端测试")
