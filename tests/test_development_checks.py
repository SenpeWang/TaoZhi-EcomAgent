"""开发门禁验收：真实 CLI、部分暂存、历史秘密和目录契约；不读取企业资料。"""
from __future__ import annotations

import importlib.util
import io
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT / "scripts"))
spec = importlib.util.spec_from_file_location("development_checks", PROJECT / "scripts/check_development.py")
development = importlib.util.module_from_spec(spec)
spec.loader.exec_module(development)


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    """用独立合成仓库与真实锁定工具验证门禁，不触碰当前项目索引。"""
    root = tmp_path / "repository"
    (root / "web/src").mkdir(parents=True)
    (root / "docs").mkdir()
    for name in ["eslint.config.js", ".prettierrc.json", ".prettierignore", "commitlint.config.cjs"]:
        shutil.copyfile(PROJECT / "web" / name, root / "web" / name)
    (root / "web/package.json").write_text('{\n  "type": "module"\n}\n')
    (root / "web/node_modules").symlink_to(PROJECT / "web/node_modules", target_is_directory=True)
    (root / "web/src/App.vue").write_text("<template>\n  <div>中文界面</div>\n</template>\n")
    (root / "docs/STRUCTURE.md").write_text(
        "| 目录 | 职责 |\n| --- | --- |\n"
        "| `docs/` | 说明 |\n| `web/` | 前端 |\n| `web/src/` | 源码 |\n"
    )
    monkeypatch.setattr(development, "ROOT", root)
    monkeypatch.setattr(development.repository, "ROOT", root)
    def git(*args):
        return subprocess.run(["git", *args], cwd=root, capture_output=True, text=True, check=True).stdout.strip()
    git("init", "-b", "main")
    git("config", "user.name", "Boundary Test")
    git("config", "user.email", "boundary@example.invalid")
    git("config", "core.hooksPath", "/dev/null")
    git("add", "web/src/App.vue", "web/package.json", "web/eslint.config.js",
        "web/.prettierrc.json", "web/.prettierignore", "web/commitlint.config.cjs", "docs/STRUCTURE.md")
    # 工具配置来自项目；其格式也先真实校验，不能让 fixture 躲过门禁。
    return root, git


@pytest.mark.parametrize("message", [
    "feat(documents): 增加资料版本对比",
    "fix(auth)!: 调整登录接口",
    "feat(api): 调整接口\n\nBREAKING CHANGE: 客户端须迁移到新接口。",
    "revert: 撤销错误的界面改动",
])
def test_valid_messages(workspace, message):
    development.check_message(message)


@pytest.mark.parametrize("message", [
    "update everything", "feat: English only", "unknown: 修改内容",
    "feat: " + "中" * 96, "Merge branch main",
])
def test_invalid_messages(workspace, message):
    with pytest.raises(RuntimeError, match="中文说明"):
        development.check_message(message)


def test_secret_in_message_is_not_echoed(workspace):
    secret = "sk-" + "C" * 32
    with pytest.raises(RuntimeError) as error:
        development.check_message("chore: 更新配置\n\n" + secret)
    assert secret not in str(error.value)
    assert "秘密" in str(error.value)


def test_invalid_and_multiline_pr_titles(workspace, monkeypatch):
    monkeypatch.setattr(sys, "argv", ["check_development.py", "pr-title"])
    monkeypatch.setattr(sys, "stdin", io.StringIO("invalid PR title"))
    assert development.main() == 1
    monkeypatch.setattr(sys, "stdin", io.StringIO("feat: 修改页面\n$(touch ignored)"))
    assert development.main() == 1


def test_pr_title_remains_data(workspace, monkeypatch):
    root, _ = workspace
    marker = root / "executed"
    # 使用相对短标记避免标题长度影响测试，只把内容经 stdin 传给真实 CLI。
    title = "docs: 示例标题 $(touch executed)"
    monkeypatch.setattr(sys, "argv", ["check_development.py", "pr-title"])
    monkeypatch.setattr(sys, "stdin", io.StringIO(title))
    assert development.main() == 0
    assert not marker.exists() and not (root / "web/executed").exists()


def test_partial_stage_uses_index_and_cleans_snapshot(workspace):
    root, git = workspace
    source = root / "web/src/App.vue"
    source.write_text('<script setup lang="ts">const label="中文界面";</script><template><div>{{label}}</div></template>\n')
    git("add", "web/src/App.vue")
    staged = git("show", ":web/src/App.vue")
    source.write_text('<script setup lang="ts">\nconst label = \'中文界面\'\n</script>\n\n<template>\n  <div>{{ label }}</div>\n</template>\n')
    with pytest.raises(RuntimeError, match="prettier"):
        development.check_snapshot(None)
    assert git("show", ":web/src/App.vue") == staged
    assert "const label = " in source.read_text()
    assert not list((root / "data/runtime").glob("development-check-*"))
    git("add", "web/src/App.vue")
    development.check_snapshot(None)


def test_component_filename_and_declaration(workspace):
    root, _ = workspace
    bad = root / "web/src/wrong-name.vue"
    bad.write_text("<template>\n  <div>中文</div>\n</template>\n")
    with pytest.raises(RuntimeError, match="PascalCase"):
        development.frontend(root / "web")
    bad.unlink()
    bad = root / "web/src/PermissionPanel.vue"
    bad.write_text(
        '<script setup lang="ts">\ndefineOptions({ name: "wrong-name" })\n</script>\n'
        "<template>\n  <div>中文</div>\n</template>\n"
    )
    with pytest.raises(RuntimeError, match="eslint"):
        development.frontend(root / "web")


@pytest.mark.parametrize("source", [
    "export function authorizeDocument() { return true }\n",
    "/** Validate permission. */\nexport function authorizeDocument() { return true }\n",
    "/** 校验权限。 */\nexport function wrong_name() { return true }\n",
])
def test_key_comment_and_function_naming(workspace, source):
    root, _ = workspace
    (root / "web/src/permission.ts").write_text(source)
    with pytest.raises(RuntimeError, match="eslint"):
        development.frontend(root / "web")


def test_missing_dependencies_fail_without_install(workspace):
    root, _ = workspace
    (root / "web/node_modules").unlink()
    with pytest.raises(RuntimeError, match="npm ci"):
        development.check_message("chore: 完善检查")


def test_unregistered_and_removed_directory(workspace):
    root, git = workspace
    (root / "extra").mkdir()
    (root / "extra/tool.py").write_text("# 合成代码\n")
    git("add", "extra/tool.py")
    assert any("未在" in problem for problem in development.repository.inspect_tree(None))
    git("rm", "--cached", "extra/tool.py")
    git("commit", "-m", "chore: 初始化合成结构")
    git("rm", "web/src/App.vue")
    assert any("没有跟踪文件" in problem for problem in development.repository.inspect_tree(None))


def test_private_data_is_blocked_even_when_forced_into_index(workspace):
    root, git = workspace
    (root / "data/incoming").mkdir(parents=True)
    (root / "data/incoming/example.md").write_text("测试自行构造的资料\n")
    git("add", "-f", "data/incoming/example.md")
    with pytest.raises(RuntimeError, match="私有数据"):
        development.check_snapshot(None)


def test_range_scans_secret_deleted_in_later_commit(workspace):
    root, git = workspace
    git("commit", "-m", "chore: 初始化合成仓库")
    base = git("rev-parse", "HEAD")
    (root / "leak.py").write_text('TOKEN="sk-' + "D" * 32 + '"\n')
    git("add", "leak.py")
    git("commit", "-m", "chore: 添加合成配置")
    git("rm", "leak.py")
    git("commit", "-m", "chore: 删除合成配置")
    assert not any("API" in item for item in development.repository.inspect_tree("HEAD"))
    with pytest.raises(RuntimeError, match="API"):
        development.check_commits(development.commit_revisions(base, "HEAD"))


@pytest.mark.parametrize("name", ["AGENTS.md", "agents.md", "docs/AGENTS.md"])
def test_local_agent_policy_cannot_enter_snapshot(workspace, name):
    """强制暂存也必须拒绝维护文件；检查不删除本机规则。"""
    root, git = workspace
    policy = root / name
    policy.write_text("仅供维护者使用的合成规则\n")
    git("add", "-f", name)
    with pytest.raises(RuntimeError, match="本机维护约束文件"):
        development.check_snapshot(None)
    assert policy.read_text() == "仅供维护者使用的合成规则\n"
    assert not list((root / "data/runtime").glob("development-check-*"))


def test_published_agent_policy_cutover_is_limited(workspace, monkeypatch):
    """已发布路径兼容不允许未来重新跟踪，也不豁免历史秘密。"""
    root, git = workspace
    policy = root / "AGENTS.md"
    policy.write_text("合成的旧维护规则\n")
    git("add", "AGENTS.md")
    git("commit", "-m", "chore: 发布旧维护规则")
    published = git("rev-parse", "HEAD")
    monkeypatch.setattr(
        development.repository, "PRE_LOCAL_AGENT_POLICY_COMMITS", frozenset({published}),
    )
    assert development.repository.inspect_tree(published) == []
    assert any("本机维护约束文件" in item for item in development.repository.inspect_tree(None))
    (root / "README.md").write_text("新提交仍携带旧文件也必须拒绝\n")
    git("add", "README.md")
    git("commit", "-m", "docs: 更新合成介绍")
    assert any("本机维护约束文件" in item for item in development.repository.inspect_tree("HEAD"))
    git("rm", "--cached", "AGENTS.md")
    git("commit", "-m", "fix: 仅保留本机维护规则")
    assert policy.exists()
    assert development.repository.inspect_tree("HEAD") == []
    policy.write_text('TOKEN="sk-' + "E" * 32 + '"\n')
    git("add", "-f", "AGENTS.md")
    git("commit", "-m", "test: 构造历史检测边界")
    secret_commit = git("rev-parse", "HEAD")
    monkeypatch.setattr(
        development.repository, "PRE_LOCAL_AGENT_POLICY_COMMITS", frozenset({secret_commit}),
    )
    assert any("API" in item for item in development.repository.inspect_tree(secret_commit))
    git("rm", "--cached", "AGENTS.md")
    (root / "data").mkdir(exist_ok=True)
    (root / "data/AGENTS.md").write_text("私有目录的合成资料\n")
    git("add", "-f", "data/AGENTS.md")
    git("commit", "-m", "test: 构造私有目录边界")
    private_commit = git("rev-parse", "HEAD")
    monkeypatch.setattr(
        development.repository, "PRE_LOCAL_AGENT_POLICY_COMMITS", frozenset({private_commit}),
    )
    assert any("私有数据" in item for item in development.repository.inspect_tree(private_commit))


def test_gitignore_excludes_agent_policy_variants(workspace):
    """实际 Git 忽略规则覆盖根目录、嵌套目录和大小写变化。"""
    root, git = workspace
    (root / ".gitignore").write_text((PROJECT / ".gitignore").read_text())
    for name in ["AGENTS.md", "agents.md", "docs/AGENTS.md", "web/aGeNtS.Md"]:
        assert git("check-ignore", "--no-index", name) == name


def test_branch_switch_preserves_ignored_local_policy(workspace):
    """从旧跟踪方式切换后，拒绝覆写，并验证正常快进后本机文件保留。"""
    root, git = workspace
    policy = root / "AGENTS.md"
    policy.write_text("旧合成规则\n")
    git("add", "AGENTS.md")
    git("commit", "-m", "chore: 初始化旧分支")
    git("switch", "-c", "chore/local-policy")
    (root / ".gitignore").write_text((PROJECT / ".gitignore").read_text())
    git("rm", "--cached", "AGENTS.md")
    policy.write_text("新本机合成规则\n")
    git("add", ".gitignore")
    git("commit", "-m", "fix: 移出维护文件")
    with pytest.raises(subprocess.CalledProcessError):
        git("switch", "--no-overwrite-ignore", "main")
    assert policy.read_text() == "新本机合成规则\n"
    git("fetch", ".", "HEAD:main")
    git("switch", "--no-overwrite-ignore", "main")
    assert policy.read_text() == "新本机合成规则\n"
    assert git("ls-files", "AGENTS.md") == ""
