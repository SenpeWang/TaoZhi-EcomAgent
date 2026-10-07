#!/usr/bin/env python3
"""开发门禁：暂存快照、前端风格、提交范围与 PR 标题；只检查，不代替审阅。"""
from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import check_repository as repository

ROOT = Path(__file__).resolve().parents[1]


class ToolCheckFailed(RuntimeError):
    """工具返回检查失败；环境缺失保留独立诊断。"""


def node_executable() -> str:
    """使用用户安装的 Node 或 PATH；依赖不足直接报错，绝不联网安装。"""
    local = Path.home() / ".local/share/ecom-v3/runtime/node/bin/node"
    executable = str(local) if local.is_file() else shutil.which("node")
    if not executable:
        raise RuntimeError("缺少 Node.js，请先按部署说明安装 Node 24 与前端锁定依赖")
    version = subprocess.run([executable, "--version"], capture_output=True, text=True, check=True)
    if int(version.stdout.lstrip("v").split(".")[0]) != 24:
        raise RuntimeError("开发检查要求 Node.js 24，请使用项目运行时或调整 PATH")
    return executable


def run_tool(tool: str, args: list[str], workspace: Path, quiet: bool = False) -> None:
    """调用已安装 CLI；失败只保留普通风格输出，提交消息另行脱敏。"""
    modules = ROOT / "web/node_modules"
    binaries = {
        "eslint": modules / "eslint/bin/eslint.js",
        "prettier": modules / "prettier/bin/prettier.cjs",
        "commitlint": modules / "@commitlint/cli/cli.js",
    }
    binary = binaries[tool]
    if not binary.is_file():
        raise RuntimeError(f"缺少 {tool} 依赖，请先在 web/ 执行 npm ci；钩子不会自动安装")
    result = subprocess.run(
        [node_executable(), str(binary), *args],
        cwd=workspace, capture_output=quiet, text=True,
    )
    if result.returncode:
        raise ToolCheckFailed(f"{tool} 检查失败，请按开发规范修复后重新提交")


def check_components(workspace: Path) -> None:
    """检查自建组件文件名；Element Plus 的组件标签不受此限制。"""
    invalid = [
        path.relative_to(workspace).as_posix()
        for path in (workspace / "src").rglob("*.vue")
        if not re.fullmatch(r"[A-Z][A-Za-z0-9]*", path.stem)
    ]
    if invalid:
        raise RuntimeError("Vue 组件文件必须使用 PascalCase：" + "、".join(invalid))


def frontend(workspace: Path, format_only: bool = False, write: bool = False) -> None:
    """检查完整前端基线；只有显式 format 命令可以改写文件。"""
    if not format_only:
        check_components(workspace)
        run_tool("eslint", [".", "--max-warnings", "0"], workspace)
    run_tool("prettier", [
        "--write" if write else "--check",
        "src/**/*.{ts,vue,css}", "*.{js,cjs,mjs,json,html}", ".prettierrc.json",
    ], workspace)


def check_snapshot(revision: str | None) -> None:
    """从 Git 对象导出完整快照；未暂存的修复不能掩盖索引中的错误。"""
    problems = repository.inspect_tree(revision)
    if problems:
        raise RuntimeError("提交内容检查失败：\n" + "\n".join(problems))
    runtime = ROOT / "data/runtime"
    runtime.mkdir(parents=True, exist_ok=True, mode=0o700)
    with tempfile.TemporaryDirectory(prefix="development-check-", dir=runtime) as folder:
        snapshot = Path(folder)
        for name, mode, sha in repository.read_entries(revision):
            path = snapshot / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(repository.git("cat-file", "blob", sha))
            path.chmod(int(mode[-3:], 8))
        (snapshot / "web/node_modules").symlink_to(ROOT / "web/node_modules", target_is_directory=True)
        frontend(snapshot / "web")
    print("暂存/提交快照检查通过，临时目录已清理")


def check_message(message: str, label: str = "提交信息") -> None:
    """秘密先扫描；commitlint 的原始诊断不输出，防止标题或正文回显凭据。"""
    if repository.inspect_blob("commit-message", message.encode()):
        raise RuntimeError(f"{label}：命中秘密检测规则或超出公开内容限制（内容不回显）")
    if not message.strip():
        raise RuntimeError(f"{label}：标题不能为空")
    runtime = ROOT / "data/runtime"
    runtime.mkdir(parents=True, exist_ok=True, mode=0o700)
    with tempfile.TemporaryDirectory(prefix="commit-check-", dir=runtime) as folder:
        message_file = Path(folder) / "message.txt"
        message_file.write_text(message, encoding="utf-8")
        try:
            run_tool("commitlint", [
                "--config", str(ROOT / "web/commitlint.config.cjs"),
                "--edit", str(message_file),
            ], ROOT / "web", quiet=True)
        except ToolCheckFailed as error:
            raise RuntimeError(
                f"{label}：须为 类型(可选模块): 中文说明，标题最多 100 字符；"
                "破坏性变更用 ! 或 BREAKING CHANGE:"
            ) from error


def resolve_revision(value: str) -> str:
    """将引用解析为提交，拒绝不存在的基准；不把用户参数拼入 shell。"""
    return repository.git("rev-parse", "--verify", value + "^{commit}").decode().strip()


def commit_revisions(base: str, head: str) -> list[str]:
    """检查同一分支的新增提交；历史初始化提交不追溯改写。"""
    first, last = resolve_revision(base), resolve_revision(head)
    return repository.git("rev-list", "--reverse", f"{first}..{last}").decode().splitlines()


def check_commits(revisions: list[str], include_frontend: bool = False) -> None:
    """逐次检查提交树和消息，提交后删除的私有内容也必须拦截。"""
    for revision in revisions:
        problems = repository.inspect_tree(revision)
        if problems:
            raise RuntimeError(f"{revision[:12]} 内容检查失败：\n" + "\n".join(problems))
        message = repository.git("show", "-s", "--format=%B", revision).decode()
        check_message(message, revision[:12] + " 提交信息")
        if include_frontend:
            check_snapshot(revision)
    print(f"新增提交检查通过（{len(revisions)} 项）：提交信息、秘密、资料和目录登记")


def main() -> int:
    """执行明确检查模式，所有失败返回非零；钩子不格式化、不暂存、不安装。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=[
        "lint", "format", "format-check", "staged", "message", "commits", "push", "pr-title",
    ])
    parser.add_argument("--file", type=Path)
    parser.add_argument("--base", default="origin/main")
    parser.add_argument("--head", default="HEAD")
    args = parser.parse_args()
    try:
        repository.ROOT = ROOT
        if args.command == "lint":
            frontend(ROOT / "web")
        elif args.command in {"format", "format-check"}:
            frontend(ROOT / "web", format_only=True, write=args.command == "format")
        elif args.command == "staged":
            check_snapshot(None)
            repository.git("diff", "--cached", "--check")
        elif args.command == "message":
            if not args.file:
                raise RuntimeError("commit-msg 必须提供 --file")
            check_message(args.file.read_text(encoding="utf-8"))
        elif args.command == "commits":
            check_commits(commit_revisions(args.base, args.head))
        elif args.command == "push":
            check_commits(repository.pre_push_revisions(), include_frontend=True)
        else:
            title = sys.stdin.read().rstrip("\n")
            if "\n" in title or "\r" in title:
                raise RuntimeError("PR 标题必须是单行文本")
            check_message(title, "PR 标题")
        return 0
    except (RuntimeError, ValueError, OSError, subprocess.SubprocessError) as error:
        print(str(error), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
