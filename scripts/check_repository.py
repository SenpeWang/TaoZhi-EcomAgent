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
    "data", "node_modules", "__pycache__", ".venv", ".venv-v3", "venv",
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
    result = subprocess.run(["git", *args], cwd=ROOT, capture_output=True)
    if result.returncode:
        raise RuntimeError("Git 读取失败，请检查仓库与提交引用")
    return result.stdout


def blocked_path(path: str) -> str | None:
    item = PurePosixPath(path)
    if any(part in FORBIDDEN_DIRECTORIES for part in item.parts):
        return "私有数据、依赖或缓存目录"
    if path == "项目说明.md":
        return "个人学习说明"
    if path.startswith("web/dist/") or any(part.startswith(".build-") for part in item.parts):
        return "构建产物"
    if item.name.startswith(".env") and item.name != ".env.example":
        return "私有环境配置"
    if item.name in {"database-secrets.json", "initdb-password"} or item.name.startswith("bootstrap-owner-"):
        return "私有身份凭据"
    if item.suffix.lower() in FORBIDDEN_SUFFIXES or ".bak." in item.name:
        return "业务附件、数据库、凭据或备份"
    return None


def inspect_blob(path: str, content: bytes) -> list[str]:
    problems = []
    category = blocked_path(path)
    if category:
        problems.append(category)
    if len(content) > MAX_FILE_BYTES:
        problems.append("超过 1 MiB；大文件需调整存放方案")
    if b"\0" in content:
        problems.append("二进制文件需明确公开用途")
    for label, pattern in SECRET_PATTERNS:
        for match in pattern.finditer(content):
            # 允许显式占位符，不允许源码内嵌真实数据库密码。
            if label == "内嵌数据库密码" and (
                match.group(1).startswith(bytes([36, 123])) or match.group(1) in {b"PASSWORD", b"<password>"}
            ):
                continue
            problems.append(label)
            break
    return problems


def read_entries(revision: str | None) -> list[tuple[str, str, str]]:
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
        problems.extend(f"{path}: {reason}" for reason in inspect_blob(path, content))
    structure = blobs.get("docs/STRUCTURE.md", b"").decode("utf-8", errors="replace")
    registered = set(re.findall(r"\x60([^\x60\n]+/)\x60", structure))
    directories = set()
    for path, _, _ in entries:
        directories.update(str(parent) + "/" for parent in PurePosixPath(path).parents if str(parent) != ".")
    for directory in sorted(directories - registered):
        problems.append(f"{directory}: 未在 docs/STRUCTURE.md 登记职责")
    for directory in sorted(registered - directories):
        problems.append(f"{directory}: 没有跟踪文件；删除或移动目录后须同步登记")
    return problems


def pre_push_revisions() -> list[str]:
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
