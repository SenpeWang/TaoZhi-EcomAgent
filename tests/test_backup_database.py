"""以本地临时附件及模拟 libpq 客户端验证备份命令，不连接或恢复实际数据库。"""
import io
import json
import tarfile
from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock

import pytest

from scripts import backup_database as backup


def test_snapshot_dump_and_signatures_share_one_transaction(monkeypatch, tmp_path):
    """pg_dump 的外部连接与签名查询使用同一个导出快照。"""
    conn = MagicMock()
    conn.execute.return_value.fetchone.return_value = ("snapshot-123",)
    order = []

    def client(name, source, *args):
        """伪造 pg_dump 调用入口，校验快照参数并记录导出顺序。

        Args:
            name: 名称标识。
            source: 数据来源或原始文本。
            args: 位置参数。
        """
        assert source is conn and name == "pg_dump"
        assert args[args.index("--snapshot") + 1] == "snapshot-123"
        order.append("dump")

    monkeypatch.setattr(backup, "_run_client", client)
    expected = {'"public"."task_events"': "hash", '"public"."task_spans"': "span-hash"}
    def checksum(source):
        """伪造校验和查询函数，断言复用同一连接并返回期望摘要。

        Args:
            source: 数据来源或原始文本。
        """
        assert source is conn
        order.append("signatures")
        return expected
    monkeypatch.setattr(backup, "signatures", checksum)
    assert backup._dump_snapshot(conn, tmp_path / "backup.dump") == expected
    assert order == ["dump", "signatures"]
    assert conn.execute.call_args_list[0].args[0].startswith("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
    conn.transaction.return_value.__enter__.assert_called_once()
    conn.transaction.return_value.__exit__.assert_called_once()


def test_client_conninfo_keeps_sensitive_settings_out_of_argv(monkeypatch):
    """连接参数支持环境凭据、证书和服务选项，私密设置只在受限临时文件中短暂存在。"""
    monkeypatch.setenv("PGPASSWORD", "environment-value")
    info = SimpleNamespace(dbname="isolated database", user="backup_user", password="",
                           get_parameters=lambda: {"service": "source", "host": "/tmp/socket",
                                                   "sslmode": "verify-full", "sslpassword": "test-phrase"})
    connection = SimpleNamespace(info=info)
    seen = []
    def client(argv, *, env, check):
        """伪造子进程执行入口，校验命令行与隔离环境变量不含明文口令。

        Args:
            argv: 命令行参数列表。
            env: 子进程环境变量。
            check: 是否启用子进程检查。
        """
        assert check and "--dbname=service=backup" in argv
        assert "test-phrase" not in str(argv) and "environment-value" not in str(argv)
        assert env["PGDATABASE"] == "isolated database" and env["PGUSER"] == "backup_user"
        assert env["PGHOST"] == "/tmp/socket" and env["PGSSLMODE"] == "verify-full"
        assert env["PGPASSWORD"] == "environment-value"
        profile = Path(env["PGSERVICEFILE"])
        assert profile.stat().st_mode & 0o777 == 0o600
        assert "sslpassword=test-phrase" in profile.read_text()
        seen.append(profile)
    monkeypatch.setattr(backup.shutil, "which", lambda name: name)
    monkeypatch.setattr(backup.subprocess, "run", client)
    backup._run_client("pg_restore", connection, "--exit-on-error", "test.dump")
    assert len(seen) == 1 and not seen[0].exists()


def test_attachments_verify_checksums_and_reject_unsafe_members(tmp_path):
    """备份清单检测损坏，隔离恢复只接受私有目录内的普通文件。"""
    private = tmp_path / "source"
    (private / "tenant").mkdir(parents=True)
    (private / "tenant" / "file").write_bytes(b"content")
    dump = tmp_path / "test.dump"
    dump.write_bytes(b"dump")
    archive = tmp_path / "private-files.tar.gz"
    backup._archive_files(private, archive)
    expected = {'"public"."task_spans"': "fake-signature"}
    (tmp_path / "manifest.json").write_text(json.dumps({
        "database_sha256": backup._digest(dump), "attachment_sha256": backup._digest(archive), "tables": expected,
    }))
    assert backup._verify_artifacts(dump, archive) == expected
    restored = tmp_path / "restored"
    assert backup._restore_files(archive, restored) == 1
    assert (restored / "private" / "tenant" / "file").read_bytes() == b"content"
    dump.write_bytes(b"corrupt")
    with pytest.raises(RuntimeError, match="校验值"):
        backup._verify_artifacts(dump, archive)
    link = tarfile.TarInfo("private/link")
    link.type = tarfile.SYMTYPE
    link.linkname = "outside"
    for entry in (link, tarfile.TarInfo("private/../outside"), tarfile.TarInfo("/outside")):
        with tarfile.open(archive, "w:gz") as output:
            output.addfile(entry, io.BytesIO(b"") if entry.isfile() else None)
        with pytest.raises(RuntimeError, match="路径"):
            backup._restore_files(archive, tmp_path / "isolated")


def test_restore_checks_snapshot_and_drops_only_its_isolated_database(monkeypatch, tmp_path):
    """校验失败仍删除本次临时库，目标连接沿用关键字 DSN 的主机和传输参数。"""
    dsn = "dbname='business database' host=/example/socket sslmode=require"
    monkeypatch.setattr(backup, "load_config", lambda: SimpleNamespace(dsn=dsn))
    monkeypatch.delenv("ECOM_PG_ADMIN_DSN", raising=False)
    source = SimpleNamespace(info=SimpleNamespace(user="app_user"))
    admin = Mock()
    restored = Mock()
    restored.transaction.return_value = nullcontext()
    handles = iter((source, admin, restored))
    connections = []
    def connect(string, *, autocommit):
        """伪造数据库连接入口，记录解析后的连接参数并断言自动提交。

        Args:
            string: 数据库连接串。
            autocommit: 是否自动提交事务。
        """
        from psycopg.conninfo import conninfo_to_dict
        assert autocommit
        connections.append(conninfo_to_dict(string))
        return nullcontext(next(handles))
    monkeypatch.setattr(backup.psycopg, "connect", connect)
    monkeypatch.setattr(backup, "_create_backup", lambda cfg, conn: tmp_path / "test.dump")
    monkeypatch.setattr(backup, "_verify_artifacts", lambda *args: {"events": "at_backup"})
    monkeypatch.setattr(backup, "_run_client", lambda *args: None)
    check = Mock(return_value={"events": "incorrect_restore"})
    monkeypatch.setattr(backup, "signatures", check)
    with pytest.raises(RuntimeError, match="签名不一致"):
        backup.verify_restore()
    check.assert_called_once_with(restored)
    assert connections[0]["dbname"] == "business database" and connections[1]["dbname"] == "postgres"
    target = connections[2]["dbname"]
    assert target.startswith("ecom_restore_") and connections[2]["sslmode"] == "require"
    assert all(info["host"] == "/example/socket" for info in connections)
    assert admin.execute.call_args.args[0].as_string() == f'DROP DATABASE "{target}"'


def test_signatures_include_discovered_trace_tables_and_unambiguous_row_boundaries():
    """诊断表和未来加入的普通应用表均参与快照校验，行边界也计入签名。"""
    conn = MagicMock()
    conn.connection = None
    conn.execute.return_value.fetchall.return_value = [
        ("public", "task_events"), ("public", "task_spans"), ("agent", "new_table")
    ]
    cursor = conn.cursor.return_value.__enter__.return_value
    cursor.__iter__.side_effect = lambda: iter([("ab",), ("c",)])
    expected = backup.signatures(conn)
    assert set(expected) == {'"public"."task_events"', '"public"."task_spans"', '"agent"."new_table"'}
    cursor.__iter__.side_effect = lambda: iter([("a",), ("bc",)])
    assert backup.signatures(conn) != expected
    assert all("ORDER BY" in call.args[0].as_string() for call in cursor.execute.call_args_list)
