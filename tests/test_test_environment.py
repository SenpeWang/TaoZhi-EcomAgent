"""共享测试环境定义与解析的契约测试。"""

from pathlib import Path
from urllib.parse import urlsplit

import pytest
import yaml
from dotenv import dotenv_values

from taozhi.config import load_config

ROOT = Path(__file__).resolve().parents[1]
SHARED_ENV = ROOT / "config" / "test.env"
WORKFLOW = ROOT / ".github" / "workflows" / "ci.yml"


def test_shared_test_environment_is_passwordless():
    """共享测试环境必须指向 ecom_test 且不内嵌口令。"""
    values = dotenv_values(SHARED_ENV)
    assert values["ECOM_MODE"] == "test"
    parts = urlsplit(values["ECOM_TEST_DATABASE_URL"])
    assert parts.path == "/ecom_test"
    assert parts.password is None


def test_runtime_resolves_shared_test_environment():
    """测试模式必须解析到共享测试环境，而不是宿主 .env。"""
    config = load_config()
    assert config.mode == "test"
    assert config.dsn.endswith("/ecom_test")
    assert config.env_file == SHARED_ENV


def test_test_dsn_with_embedded_password_is_rejected(tmp_path, monkeypatch):
    """测试连接串内嵌口令时必须拒绝加载。"""
    env_file = tmp_path / "test.env"
    password = "not-a-real-secret"
    env_file.write_text(
        "ECOM_MODE=test\n"
        f"ECOM_TEST_DATABASE_URL=postgresql://ecom_test:{password}@127.0.0.1:15432/ecom_test\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("ECOM_ENV_FILE", str(env_file))
    with pytest.raises(RuntimeError, match="PGPASSWORD"):
        load_config()


def test_workflow_declares_no_test_environment():
    """远端 CI 不得自行声明测试环境变量，只能复用本地 make 目标。"""
    document = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    declared = set(document.get("env") or {})
    for job in (document.get("jobs") or {}).values():
        declared.update((job or {}).get("env") or {})
    assert [key for key in declared if key.startswith("ECOM_")] == []