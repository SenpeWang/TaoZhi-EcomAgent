"""提交安全边界测试：私有文件、暂存内容、历史提交与秘密不得漏过。"""
import importlib.util
import subprocess
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location(
    "repository_guard", Path(__file__).resolve().parents[1] / "scripts/check_repository.py"
)
guard = importlib.util.module_from_spec(spec)
spec.loader.exec_module(guard)


@pytest.mark.parametrize("path", [
    ".env.production",
    "web/node_modules/a.js", "web/dist/index.html", "database-secrets.json",
    "plan.bak.20261007", "customer.docx",
    "docs/private/RUNBOOK.md", "docs/private/nested/notes.md",
    "data/runtime/secret.pem", "data/runtime/access.token",
    "data/private/accessory_pdfs/manual.pdf", "data/private/accessory_pdfs/manifest.json",
])
def test_private_paths_are_blocked(path):
    """验证私有目录、构建产物、凭据与备份等路径被拦截。

    Args:
        path: ``path`` 参数化取值。
    """
    assert guard.blocked_path(path)


def test_example_and_software_docs_are_allowed():
    """验证示例配置、软件文档与公开数据路径放行。"""
    assert guard.inspect_blob(".env.example", b'API_KEY=""\n') == []
    assert guard.blocked_path("docs/STRUCTURE.md") is None
    assert guard.blocked_path("data/corpus/产品.pdf") is None
    assert guard.blocked_path("data/README.md") is None
    assert guard.blocked_path("data/accessory_pdfs/manual.pdf") is None
    assert guard.blocked_path("data/runtime/accounts.json") is None
    assert guard.blocked_path("AGENTS.md") is None


def test_data_secrets_are_still_blocked():
    """验证 data 目录中的密钥内容仍被拦截。"""
    secret = b"sk-" + b"C" * 32
    problems = guard.inspect_blob("data/runtime/config.json", b'{"api_key":"' + secret + b'"}')
    assert problems


def test_secret_errors_do_not_echo_credentials():
    """验证密钥检查报错不回显凭据明文。"""
    secret = b"sk-" + b"A" * 32
    problems = guard.inspect_blob("src/config.py", b'API_KEY="' + secret + b'"')
    assert problems and all(secret.decode() not in problem for problem in problems)


def test_push_scans_deleted_secret_commit(monkeypatch, tmp_path):
    """验证推送检查会扫描被后续提交删除的密钥。

    Args:
        monkeypatch: pytest 提供的补丁夹具，用于替换属性或环境变量。
        tmp_path: pytest 提供的临时目录路径夹具。
    """
    monkeypatch.setattr(guard, "ROOT", tmp_path)
    def run(*args):
        """在临时仓库中执行 Git 命令。

        Args:
            args: 位置参数。
        """
        subprocess.run(["git", *args], cwd=tmp_path, check=True, capture_output=True)
    run("init", "-b", "main")
    run("config", "user.name", "Test")
    run("config", "user.email", "test@example.invalid")
    (tmp_path / "private.py").write_text('API_KEY="sk-' + "B" * 32 + '"\n')
    run("add", "private.py")
    run("commit", "-m", "bad intermediate commit")
    first = guard.git("rev-parse", "HEAD").decode().strip()
    (tmp_path / "private.py").unlink()
    (tmp_path / "README.md").write_text("public\n")
    run("add", "-A")
    run("commit", "-m", "delete secret")
    assert any("API" in item for item in guard.inspect_tree(first))
    assert not any("API" in item for item in guard.inspect_tree("HEAD"))
