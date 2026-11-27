"""开发门禁验收：真实 CLI、部分暂存、历史秘密和目录契约；不读取企业资料。"""
from __future__ import annotations

import ast
import importlib.util
import io
import shutil
import subprocess
import sys
import tokenize
from pathlib import Path

import pytest
import yaml

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT / "scripts"))
spec = importlib.util.spec_from_file_location("development_checks", PROJECT / "scripts/check_development.py")
development = importlib.util.module_from_spec(spec)
spec.loader.exec_module(development)


def test_source_comments_in_python_and_yaml():
    """扫描仓库中的脚本、迁移、测试及配置，区分源码注释和字符串数据。"""
    tracked = subprocess.check_output(
        ["git", "ls-files", "-z", "*.py", "*.yaml", "*.yml"], cwd=PROJECT,
    ).decode().split("\0")
    tracked = [name for name in tracked if name and not name.startswith("data/")]
    violations = []
    for name in filter(None, tracked):
        source = (PROJECT / name).read_text(encoding="utf-8")
        if name.endswith(".py"):
            for token in tokenize.generate_tokens(io.StringIO(source).readline):
                if token.type == tokenize.COMMENT and not (token.start == (1, 0) and token.string.startswith("#!")):
                    violations.append(f"{name}:{token.start[0]}")
        else:
            scalars = [token for token in yaml.scan(source) if isinstance(token, yaml.tokens.ScalarToken)]
            for offset, char in enumerate(source):
                if char == "#" and not any(token.start_mark.index <= offset < token.end_mark.index for token in scalars):
                    violations.append(f"{name}:{source.count(chr(10), 0, offset) + 1}")
    assert not violations, "源码说明请使用文档字符串：" + "、".join(violations)


def _frontend_comment_violations(name: str, source: str) -> list[str]:
    """扫描 TS/JS/Vue 中除 JSDoc 外的注释位置。"""
    violations: list[str] = []
    index = 0
    line = 1
    quote = ""
    total = len(source)
    while index < total:
        char = source[index]
        if char == "\n":
            line += 1
            index += 1
            continue
        if quote:
            if char == "\\":
                index += 2
                continue
            if char == quote:
                quote = ""
            index += 1
            continue
        if char in ("'", '"', "`"):
            quote = char
            index += 1
            continue
        if source.startswith("//", index):
            end = source.find("\n", index)
            end = total if end < 0 else end
            violations.append(f"{name}:{line}")
            index = end
            continue
        if source.startswith("/*", index):
            end = source.find("*/", index + 2)
            end = total if end < 0 else end + 2
            block = source[index:end]
            if not block.startswith("/**"):
                violations.append(f"{name}:{line}")
            line += block.count("\n")
            index = end
            continue
        if name.endswith(".vue") and source.startswith("<!--", index):
            end = source.find("-->", index + 4)
            end = total if end < 0 else end + 3
            block = source[index:end]
            violations.append(f"{name}:{line}")
            line += block.count("\n")
            index = end
            continue
        index += 1
    return violations


def test_frontend_source_has_no_non_docstring_comments():
    """前端 TS/JS/Vue 只允许 JSDoc，禁止行内注释、块注释与模板注释。"""
    tracked = subprocess.check_output(
        ["git", "ls-files", "-z", "--", "web/src", "web/*.ts", "web/*.js", "web/*.cjs"],
        cwd=PROJECT,
    ).decode().split("\0")
    suffixes = (".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs", ".vue")
    violations = []
    for name in tracked:
        if not name or not name.endswith(suffixes):
            continue
        source = (PROJECT / name).read_text(encoding="utf-8")
        violations.extend(_frontend_comment_violations(name, source))
    assert not violations, "前端源码说明请使用 JSDoc：" + "、".join(violations)


def test_python_modules_have_docstrings():
    """所有 tracked Python 模块（除 data/）必须有模块级 docstring。"""
    tracked = subprocess.check_output(
        ["git", "ls-files", "-z", "*.py"], cwd=PROJECT,
    ).decode().split("\0")
    missing = []
    for name in tracked:
        if not name or name.startswith("data/"):
            continue
        try:
            tree = ast.parse((PROJECT / name).read_text(encoding="utf-8"))
        except SyntaxError:
            missing.append(name)
            continue
        if not ast.get_docstring(tree):
            missing.append(name)
    assert not missing, "Python 模块缺少 docstring：" + "、".join(missing)


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
    (root / "STRUCTURE.md").write_text(
        "| 目录 | 职责 |\n| --- | --- |\n"
        "| `web/` | 前端 |\n| `web/src/` | 源码 |\n"
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
        "web/.prettierrc.json", "web/.prettierignore", "web/commitlint.config.cjs", "STRUCTURE.md")
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


def test_message_cli_rejects_invalid_and_missing_input(workspace, monkeypatch):
    """主分支提交的真实文件参数必须接受校验；无效消息和缺失参数都拒绝。"""
    root, _ = workspace
    message = root / "commit-message.txt"
    message.write_text("invalid commit title")
    monkeypatch.setattr(sys, "argv", ["check_development.py", "message", "--file", str(message)])
    assert development.main() == 1
    monkeypatch.setattr(sys, "argv", ["check_development.py", "message"])
    assert development.main() == 1


def test_commit_message_file_remains_data(workspace, monkeypatch):
    """消息文件中的 Shell 语法始终是数据，不能产生额外文件。"""
    root, _ = workspace
    marker = root / "executed"
    message = root / "commit-message.txt"
    message.write_text("docs: 示例标题 $(touch executed)")
    monkeypatch.setattr(sys, "argv", ["check_development.py", "message", "--file", str(message)])
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
    "// 普通注释\nexport const label = '授权'\n",
    "/* 普通注释 */\nexport const label = '授权'\n",
    "export function authorizeDocument() { return true }\n",
    "/** Validate permission. */\nexport function authorizeDocument() { return true }\n",
    "/** 校验权限。 */\nexport function wrong_name() { return true }\n",
])
def test_key_comment_and_function_naming(workspace, source):
    root, _ = workspace
    (root / "web/src/permission.ts").write_text(source)
    with pytest.raises(RuntimeError, match="eslint"):
        development.frontend(root / "web")


def test_template_comment_and_unescaped_html_are_rejected(workspace):
    """在模板和 Markdown 渲染边界分别拒绝普通注释及未转义的表达式。"""
    root, _ = workspace
    view = root / "web/src/shared/MarkdownReport.vue"
    view.parent.mkdir()
    for template in ("<div><!-- 普通注释 --></div>", '<div v-html="rawInput" />'):
        view.write_text(f"<template>\n  {template}\n</template>\n")
        with pytest.raises(RuntimeError, match="eslint"):
            development.frontend(root / "web")


@pytest.mark.parametrize(("folder", "dependency"), [
    ("shared", "../features/tasks/types"),
    ("features/tasks", "../../app/workspaceApi"),
])
def test_frontend_modules_do_not_depend_on_their_callers(workspace, folder, dependency):
    """防止公共工具依赖业务代码，以及业务模块反向导入工作台。"""
    root, _ = workspace
    source = root / "web/src" / folder / "invalidDependency.ts"
    source.parent.mkdir(parents=True)
    source.write_text(f"import '{dependency}'\n")
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
    (root / "node_modules/pkg").mkdir(parents=True)
    (root / "node_modules/pkg/example.md").write_text("测试自行构造的依赖文件\n")
    git("add", "-f", "node_modules/pkg/example.md")
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
