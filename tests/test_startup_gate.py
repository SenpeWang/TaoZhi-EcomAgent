"""启动依赖闸门的单元测试：依赖清单、重试、超时、目标命令解析与凭据脱敏。"""
from __future__ import annotations

import importlib.util
from pathlib import Path
from types import SimpleNamespace

PROJECT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "startup_gate", PROJECT / "scripts/wait_for_startup.py"
)
gate = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gate)


def test_dependencies_follow_enabled_switches() -> None:
    """只探测已启用的中间件，数据库与向量服务始终在列。"""
    disabled = SimpleNamespace(minio_enabled=False, kafka_enabled=False, es_enabled=False)
    assert [name for name, _ in gate.build_dependencies(disabled)] == ["数据库", "向量服务"]

    enabled = SimpleNamespace(minio_enabled=True, kafka_enabled=True, es_enabled=True)
    assert [name for name, _ in gate.build_dependencies(enabled)] == [
        "数据库",
        "MinIO",
        "Kafka",
        "Elasticsearch",
        "向量服务",
    ]


def test_wait_retries_until_all_ready() -> None:
    """依赖先失败后成功时继续轮询，并在状态变化时输出进度。"""
    clock = {"now": 0.0}
    attempts = {"数据库": 0, "向量服务": 0}

    def probe_of(name: str):
        """构造记录尝试次数的就绪探针工厂。

        Args:
            name: 名称标识。
        """
        def probe() -> None:
            """按尝试次数对数据库探针先失败后成功。"""
            attempts[name] += 1
            if name == "数据库" and attempts[name] < 3:
                raise gate.NotReady("数据库未就绪（OperationalError）")

        return probe

    logs: list[str] = []

    def sleep(seconds: float) -> None:
        """推进假时钟。

        Args:
            seconds: 秒数。
        """
        clock["now"] += seconds

    gate.wait(
        [("数据库", probe_of("数据库")), ("向量服务", probe_of("向量服务"))],
        timeout=30,
        interval=2,
        log=logs.append,
        monotonic=lambda: clock["now"],
        sleep=sleep,
    )
    assert attempts["数据库"] == 3
    joined = "\n".join(logs)
    assert "等待 数据库：数据库未就绪（OperationalError）" in joined
    assert "数据库 已就绪" in joined
    assert "向量服务 已就绪" in joined


def test_wait_times_out_with_pending_detail() -> None:
    """超过整体超时抛出 StartupTimeout，并列出仍未就绪的依赖与原因。"""
    clock = {"now": 0.0}

    def stuck() -> None:
        """模拟 Elasticsearch 始终未就绪的探针。"""
        raise gate.NotReady("Elasticsearch 未就绪（集群 ping 失败）")

    def sleep(seconds: float) -> None:
        """推进假时钟。

        Args:
            seconds: 秒数。
        """
        clock["now"] += seconds

    try:
        gate.wait(
            [("Elasticsearch", stuck)],
            timeout=5,
            interval=2,
            log=lambda _: None,
            monotonic=lambda: clock["now"],
            sleep=sleep,
        )
    except gate.StartupTimeout as exc:
        message = str(exc)
    else:
        raise AssertionError("依赖未就绪时应当超时退出")
    assert "5s 内未就绪" in message
    assert "Elasticsearch（Elasticsearch 未就绪（集群 ping 失败））" in message


def test_parse_target_requires_separator_and_command() -> None:
    """缺少分隔符或分隔符后没有命令时返回 None。"""
    assert gate.parse_target([]) is None
    assert gate.parse_target(["uvicorn", "taozhi.server.api:app"]) is None
    assert gate.parse_target(["--"]) is None
    assert gate.parse_target(["--", "uvicorn", "app"]) == ["uvicorn", "app"]


def test_main_rejects_missing_target() -> None:
    """参数不含目标命令时返回用法错误码，不启动任何进程。"""
    assert gate.main([]) == 2
    assert gate.main(["python", "-c", "pass"]) == 2


def test_main_executes_target_after_dependencies_ready(monkeypatch) -> None:
    """依赖就绪后用目标命令替换当前进程。"""
    monkeypatch.setattr(gate, "load_config", lambda: SimpleNamespace())
    monkeypatch.setattr(gate, "build_dependencies", lambda cfg: [])
    monkeypatch.delenv("ECOM_STARTUP_WAIT_SECONDS", raising=False)
    monkeypatch.delenv("ECOM_STARTUP_PROBE_INTERVAL", raising=False)
    executed: list[list[str]] = []
    monkeypatch.setattr(
        gate.os, "execvp", lambda program, argv: executed.append(list(argv))
    )
    assert gate.main(["--", "uvicorn", "taozhi.server.api:app"]) == 0
    assert executed == [["uvicorn", "taozhi.server.api:app"]]


def test_database_probe_does_not_expose_dsn_password() -> None:
    """探测失败的日志只保留异常类型，不回显连接串中的口令。"""
    secret = "not-a-real-secret"
    dsn = "postgresql://probe_user:" + secret + "@127.0.0.1:1/probe_db"
    try:
        gate._probe_database(SimpleNamespace(dsn=dsn))
    except gate.NotReady as exc:
        message = str(exc)
    else:
        raise AssertionError("无法连接时应当判定为未就绪")
    assert secret not in message
    assert message.startswith("数据库未就绪")


def test_dockerfile_ships_startup_gate_into_image() -> None:
    """应用镜像必须带上闸门脚本，否则容器入口找不到目标文件。"""
    dockerfile = (PROJECT / "Dockerfile").read_text(encoding="utf-8")
    assert "COPY scripts ./scripts" in dockerfile
