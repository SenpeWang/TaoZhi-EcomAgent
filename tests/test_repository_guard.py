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
    "data/corpus/产品.md", "data/README.md", "项目说明.md", ".env.v3-production",
    "web/node_modules/a.js", "web/dist/index.html", "database-secrets.json",
    "plan.bak.20261007", "customer.docx",
    "AGENTS.md", "agents.md", "docs/AGENTS.md", "web/aGeNtS.Md",
])
def test_private_paths_are_blocked(path):
    assert guard.blocked_path(path)


def test_example_and_software_docs_are_allowed():
    assert guard.inspect_blob(".env.example", b'API_KEY=""\n') == []
    assert guard.blocked_path("docs/STRUCTURE.md") is None


def test_secret_errors_do_not_echo_credentials():
    secret = b"sk-" + b"A" * 32
    problems = guard.inspect_blob("src/config.py", b'API_KEY="' + secret + b'"')
    assert problems and all(secret.decode() not in problem for problem in problems)


def test_push_scans_deleted_secret_commit(monkeypatch, tmp_path):
    monkeypatch.setattr(guard, "ROOT", tmp_path)
    def run(*args):
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
