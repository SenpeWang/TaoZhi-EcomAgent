#!/usr/bin/env python3
"""检查 Git 实际提交内容，拒绝私有数据、秘密及未登记目录；只输出路径和类别。"""
from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path, PurePosixPath

ROOT = Path(__file__).resolve().parents[1]
MAX_FILE_BYTES = 1024 * 1024
FORBIDDEN_DIRECTORIES = {
    "node_modules", "__pycache__", ".venv", "venv",
    ".pytest_cache", ".mypy_cache", ".ruff_cache", ".idea", ".vscode",
}
FORBIDDEN_SUFFIXES = {
    ".pdf", ".doc", ".docx", ".xls", ".xlsx", ".sqlite", ".sqlite3",
    ".db", ".dump", ".pem", ".key", ".p12", ".pfx", ".token",
    ".zip", ".tar", ".gz", ".7z", ".bak", ".old", ".orig", ".log", ".pid",
}
SECRET_PATTERNS = [
    ("疑似 API 密钥", re.compile(rb"\bsk-[A-Za-z0-9_-]{20,}\b")),
    ("疑似 GitHub 凭据", re.compile(rb"\b(?:gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{30,})\b")),
    ("私钥", re.compile(rb"-----BEGIN (?:RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----")),
    ("内嵌数据库密码", re.compile(rb"postgres(?:ql)?(?:\+psycopg)?://[A-Za-z0-9_%.-]+:([A-Za-z0-9_%.~!$*+-]+)@")),
    ("内嵌 HTTPS 凭据", re.compile(rb"https://[^/\s:]+:[^@\s]+@")),
]


def git(*args: str) -> bytes:
    """执行 Git 命令并返回原始输出。

    Args:
        args: Git 子命令及其参数。

    Returns:
        命令的标准输出字节。

    Raises:
        RuntimeError: Git 命令返回非零状态时抛出。
    """
    result = subprocess.run(["git", *args], cwd=ROOT, capture_output=True)
    if result.returncode:
        raise RuntimeError("Git 读取失败，请检查仓库与提交引用")
    return result.stdout


def blocked_path(path: str) -> str | None:
    """判断路径是否属于禁止提交的私有数据、构建产物或凭据。

    Args:
        path: 相对仓库根目录的 POSIX 路径。

    Returns:
        禁止原因；路径允许提交时返回 None。
    """
    item = PurePosixPath(path)
    if any(part in FORBIDDEN_DIRECTORIES for part in item.parts):
        return "私有数据、依赖或缓存目录"
    if path.startswith("docs/private/"):
        return "私有文档目录"
    if path.startswith("web/dist/") or any(part.startswith(".build-") for part in item.parts):
        return "构建产物"
    if item.name.startswith(".env") and item.name != ".env.example":
        return "私有环境配置"
    if item.name in {"database-secrets.json", "initdb-password"} or item.name.startswith("bootstrap-owner-"):
        return "私有身份凭据"
    if item.parts and item.parts[0] == "data":
        if item.parts[:3] == ("data", "private", "accessory_pdfs"):
            return "本地保管的配件资料"
        if item.suffix.lower() in {".pem", ".key", ".p12", ".pfx", ".token"}:
            return "私钥或凭据文件"
        return None
    if item.suffix.lower() in FORBIDDEN_SUFFIXES or ".bak." in item.name:
        return "业务附件、数据库、凭据或备份"
    return None


def inspect_blob(path: str, content: bytes) -> list[str]:
    """检查单个文件的路径边界、体积、二进制属性与敏感信息。

    Args:
        path: 文件在仓库中的路径。
        content: 文件原始字节内容。

    Returns:
        问题描述列表，空列表表示通过。
    """
    problems = []
    category = blocked_path(path)
    if category:
        problems.append(category)
    if not path.startswith("data/"):
        if len(content) > MAX_FILE_BYTES:
            problems.append("超过 1 MiB；大文件需调整存放方案")
        if b"\0" in content:
            problems.append("二进制文件需明确公开用途")
    for label, pattern in SECRET_PATTERNS:
        for match in pattern.finditer(content):
            if label == "内嵌数据库密码" and (
                match.group(1).startswith(bytes([36, 123])) or match.group(1) in {b"PASSWORD", b"<password>"}
            ):
                continue
            problems.append(label)
            break
    return problems


def read_entries(revision: str | None) -> list[tuple[str, str, str]]:
    """读取索引或指定提交树中的文件条目（路径、模式与对象摘要）。

    Args:
        revision: 提交引用；None 表示读取当前索引。

    Returns:
        文件条目三元组列表。

    Raises:
        RuntimeError: 存在未解决的 Git 合并冲突时抛出。
    """
    if revision is None:
        records = git("ls-files", "--stage", "-z").split(b"\0")
        result = []
        for record in filter(None, records):
            metadata, path = record.split(b"\t", 1)
            mode, sha, stage = metadata.decode().split()
            if stage != "0":
                raise RuntimeError("尚有未解决的 Git 合并冲突")
            result.append((path.decode(), mode, sha))
        return result
    records = git("ls-tree", "-r", "-z", revision).split(b"\0")
    return [(path.decode(), metadata.decode().split()[0], metadata.decode().split()[2])
            for metadata, path in (record.split(b"\t", 1) for record in filter(None, records))]


def inspect_tree(revision: str | None) -> list[str]:
    """检查索引或提交树：文件边界、敏感信息与 STRUCTURE.md 目录登记一致性。

    Args:
        revision: 提交引用；None 表示检查当前索引。

    Returns:
        问题描述列表，空列表表示通过。
    """
    entries = read_entries(revision)
    if not entries:
        return ["没有可检查的已跟踪文件"]
    problems = []
    blobs = {}
    for path, mode, sha in entries:
        if mode not in {"100644", "100755"}:
            problems.append(f"{path}: 不允许符号链接或外部子模块")
            continue
        content = git("cat-file", "blob", sha)
        blobs[path] = content
        reasons = inspect_blob(path, content)
        problems.extend(f"{path}: {reason}" for reason in reasons)
    structure = blobs.get("STRUCTURE.md", b"").decode("utf-8", errors="replace")
    registered = set(re.findall(r"\x60([^\x60\n]+/)\x60", structure))
    directories = set()
    for path, _, _ in entries:
        directories.update(str(parent) + "/" for parent in PurePosixPath(path).parents if str(parent) != ".")
    directories = {item for item in directories if item == "data/" or not item.startswith("data/")}
    registered = {item for item in registered if item == "data/" or not item.startswith("data/")}
    for directory in sorted(directories - registered):
        problems.append(f"{directory}: 未在 STRUCTURE.md 登记职责")
    for directory in sorted(registered - directories):
        problems.append(f"{directory}: 没有跟踪文件；删除或移动目录后须同步登记")
    return problems


def pre_push_revisions() -> list[str]:
    """从标准输入读取 pre-push 推送范围并解析需要检查的提交。

    Returns:
        待检查的提交 SHA 列表。

    Raises:
        RuntimeError: 推送试图删除远端分支时抛出。
    """
    revisions = set()
    for line in sys.stdin:
        _, local_sha, _, remote_sha = line.split()
        if set(local_sha) == {"0"}:
            raise RuntimeError("本项目禁止通过维护任务删除远端分支")
        if set(remote_sha) == {"0"}:
            args = ["rev-list", local_sha, "--not", "--remotes"]
        else:
            args = ["rev-list", f"{remote_sha}..{local_sha}"]
        revisions.update(git(*args).decode().splitlines())
    return sorted(revisions)


def main() -> int:
    """命令行入口：按模式检查索引、提交树或推送范围并输出结论。

    Returns:
        0 表示检查通过，1 表示发现问题。
    """
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--staged", action="store_true", help="检查完整 Git 索引")
    group.add_argument("--tracked", action="store_true", help="检查 HEAD 已提交树")
    group.add_argument("--pre-push", action="store_true", help="从 Git 钩子读取推送范围并检查所有新增提交")
    args = parser.parse_args()
    try:
        if args.pre_push:
            revisions = pre_push_revisions()
        else:
            revisions = ["HEAD"] if args.tracked else [None]
        problems = []
        for revision in revisions:
            prefix = (str(revision)[:12] + " ") if revision else ""
            problems.extend(prefix + problem for problem in inspect_tree(revision))
        if problems:
            print("提交检查失败：", file=sys.stderr)
            print("\n".join(problems), file=sys.stderr)
            return 1
        print(f"提交检查通过（{len(revisions)} 个提交树）：资料、秘密和目录边界已检查")
        return 0
    except (RuntimeError, ValueError) as error:
        print(str(error), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
