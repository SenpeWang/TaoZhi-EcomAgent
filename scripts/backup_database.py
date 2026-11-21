"""创建 PostgreSQL 一致性快照和本地附件归档，在隔离数据库中核验恢复结果。"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

import psycopg
from psycopg import pq, sql
from psycopg.conninfo import make_conninfo

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from taozhi.config import Config, load_config, resolve_runtime_dir


def _digest(path: Path) -> str:
    """以固定内存读取备份文件并计算校验值。"""
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


@contextmanager
def _client_environment(conn: psycopg.Connection) -> Iterator[dict[str, str]]:
    """复用 libpq 已解析的连接选项，将环境变量以外的设置暂存在私有服务文件。"""
    environment = os.environ.copy()
    options = conn.info.get_parameters()
    options.pop("service", None)
    options["dbname"] = conn.info.dbname
    options["user"] = conn.info.user
    if conn.info.password:
        options["password"] = conn.info.password
    variables = {option.keyword.decode(): option.envvar.decode()
                 for option in pq.Conninfo.get_defaults() if option.envvar}
    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", prefix="backup-service-") as profile:
        profile.write("[backup]\n")
        for name, value in options.items():
            if name in variables:
                environment[variables[name]] = value
            else:
                if "\n" in value or "\r" in value:
                    raise ValueError(f"连接选项 {name} 包含服务文件不支持的换行")
                profile.write(f"{name}={value}\n")
        profile.flush()
        environment["PGSERVICEFILE"] = profile.name
        yield environment


def _run_client(name: str, conn: psycopg.Connection, *arguments: str) -> None:
    """用已连接数据库的配置运行客户端，进程参数中仅使用固定的服务名称。"""
    executable = shutil.which(name)
    if not executable:
        raise RuntimeError(f"缺少 {name} 工具")
    with _client_environment(conn) as environment:
        subprocess.run([executable, "--no-password", "--dbname=service=backup", *arguments],
                       env=environment, check=True)


def signatures(conn: psycopg.Connection) -> dict[str, str]:
    """在当前事务内逐表按固定排序流式计算行签名，涵盖任务、诊断和检查点等应用数据。"""
    conn.execute("SET LOCAL TIME ZONE 'UTC'")
    conn.execute("SET LOCAL DateStyle TO 'ISO, YMD'")
    conn.execute("SET LOCAL IntervalStyle TO 'iso_8601'")
    conn.execute("SET LOCAL extra_float_digits TO 3")
    conn.execute("SET LOCAL bytea_output TO 'hex'")
    tables = conn.execute("""
        SELECT n.nspname, c.relname FROM pg_class c
        JOIN pg_namespace n ON n.oid=c.relnamespace
        WHERE c.relkind='r' AND n.nspname !~ '^pg_' AND n.nspname<>'information_schema'
          AND NOT EXISTS (SELECT 1 FROM pg_depend d WHERE d.classid='pg_class'::regclass
                          AND d.objid=c.oid AND d.deptype='e')
        ORDER BY n.nspname, c.relname
    """).fetchall()
    result = {}
    for schema, table in tables:
        identifier = sql.Identifier(schema, table)
        digest = hashlib.sha256()
        with conn.cursor(name="backup_rows") as cursor:
            cursor.execute(sql.SQL('SELECT row_to_json(t)::text AS body FROM ONLY {} AS t '
                                   'ORDER BY row_to_json(t)::text COLLATE "C"').format(identifier))
            for (body,) in cursor:
                data = body.encode("utf-8")
                digest.update(len(data).to_bytes(8, "big"))
                digest.update(data)
        result[identifier.as_string(conn)] = digest.hexdigest()
    return result


def _dump_snapshot(conn: psycopg.Connection, path: Path) -> dict[str, str]:
    """让源库签名与 pg_dump 共享同一导出快照，包括备份期间并发修改的表。"""
    with conn.transaction():
        conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
        snapshot, = conn.execute("SELECT pg_export_snapshot()").fetchone()
        _run_client("pg_dump", conn, "--format=custom", "--no-owner", "--no-privileges",
                    "--snapshot", snapshot, "--file", str(path))
        return signatures(conn)


def _attachment_entry(item: tarfile.TarInfo) -> tarfile.TarInfo:
    """本地附件归档只接受 private 下的普通文件和目录。"""
    path = Path(item.name)
    if not path.parts or path.parts[0] != "private" or ".." in path.parts or not (item.isdir() or item.isfile()):
        raise RuntimeError("备份附件路径或文件类型无效")
    return item


def _archive_files(source: Path, path: Path) -> None:
    """创建本地私有文件归档，拒绝链接和设备文件。"""
    with tarfile.open(path, "w:gz") as archive:
        archive.add(source, arcname="private", filter=_attachment_entry)


def _restore_files(path: Path, folder: Path) -> int:
    """将归档解包到独立目录，逐文件比较落盘内容与归档内容。"""
    count = 0
    with tarfile.open(path, "r:gz") as archive:
        for item in archive:
            _attachment_entry(item)
            target = folder / item.name
            if item.isdir():
                target.mkdir(parents=True, exist_ok=True, mode=0o700)
                continue
            target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            digest = hashlib.sha256()
            with archive.extractfile(item) as source, target.open("xb") as output:
                target.chmod(0o600)
                while chunk := source.read(1024 * 1024):
                    digest.update(chunk)
                    output.write(chunk)
            if _digest(target) != digest.hexdigest():
                raise RuntimeError("恢复附件内容校验失败")
            count += 1
    return count


def _create_backup(cfg: Config, conn: psycopg.Connection) -> Path:
    """在私有临时目录中完成所有备份文件后，将整份备份一次性发布。"""
    parent = Path(os.environ.get("ECOM_BACKUP_DIR", ROOT / "data/backups")).resolve()
    if parent.is_relative_to(cfg.private_dir.resolve()):
        raise ValueError("备份目录不能位于本地附件目录内")
    parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S_%f")
    destination = parent / f"{cfg.mode}_{stamp}_{uuid4().hex[:8]}"
    with tempfile.TemporaryDirectory(prefix=".pending-", dir=parent) as pending:
        folder = Path(pending)
        dump = folder / f"{cfg.mode}.dump"
        tables = _dump_snapshot(conn, dump)
        dump.chmod(0o600)
        archive = folder / "private-files.tar.gz"
        _archive_files(cfg.private_dir, archive)
        archive.chmod(0o600)
        manifest = folder / "manifest.json"
        manifest.write_text(json.dumps({"mode": cfg.mode, "database_sha256": _digest(dump),
                                        "attachment_sha256": _digest(archive), "tables": tables}), encoding="utf-8")
        manifest.chmod(0o600)
        folder.rename(destination)
    return destination / dump.name


def create_database_backup() -> Path:
    """创建当前环境的备份并返回数据库归档路径。"""
    cfg = load_config()
    with psycopg.connect(cfg.dsn, autocommit=True) as conn:
        return _create_backup(cfg, conn)


def _verify_artifacts(dump: Path, archive: Path) -> dict[str, str]:
    """在开始恢复前校验备份文件，并返回导出快照的表签名。"""
    manifest = json.loads((dump.parent / "manifest.json").read_text(encoding="utf-8"))
    if _digest(dump) != manifest["database_sha256"] or _digest(archive) != manifest["attachment_sha256"]:
        raise RuntimeError("备份文件校验值不一致")
    return manifest["tables"]


def verify_restore() -> None:
    """创建新备份并隔离恢复，使用备份时记录的签名验证数据库与本地附件。"""
    cfg = load_config()
    with psycopg.connect(cfg.dsn, autocommit=True) as source:
        owner = source.info.user
        dump = _create_backup(cfg, source)
    archive = dump.parent / "private-files.tar.gz"
    expected = _verify_artifacts(dump, archive)
    target = "ecom_restore_" + uuid4().hex
    admin_dsn = os.environ.get("ECOM_PG_ADMIN_DSN") or make_conninfo(cfg.dsn, dbname="postgres")
    with psycopg.connect(admin_dsn, autocommit=True) as admin:
        admin.execute(sql.SQL("CREATE DATABASE {} OWNER {}").format(sql.Identifier(target), sql.Identifier(owner)))
        try:
            admin.execute(sql.SQL("REVOKE CONNECT ON DATABASE {} FROM PUBLIC").format(sql.Identifier(target)))
            with psycopg.connect(make_conninfo(cfg.dsn, dbname=target), autocommit=True) as restored:
                restored.execute("CREATE EXTENSION IF NOT EXISTS vector")
                _run_client("pg_restore", restored, "--no-owner", "--no-privileges", "--exit-on-error", str(dump))
                with restored.transaction():
                    if signatures(restored) != expected:
                        raise RuntimeError("恢复数据库与备份快照的表签名不一致")
            runtime = resolve_runtime_dir()
            runtime.mkdir(parents=True, exist_ok=True, mode=0o700)
            with tempfile.TemporaryDirectory(prefix="restore-files-", dir=runtime) as folder:
                count = _restore_files(archive, Path(folder))
            print("隔离恢复校验通过，本地附件数", count, "备份目录", dump.parent)
        finally:
            admin.execute(sql.SQL("DROP DATABASE {}").format(sql.Identifier(target)))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="显式创建备份或执行隔离恢复校验")
    parser.add_argument("command", choices=("create", "verify"))
    if parser.parse_args().command == "verify":
        verify_restore()
    else:
        print("备份目录", create_database_backup().parent)
