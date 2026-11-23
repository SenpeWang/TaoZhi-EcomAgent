"""Compose 启动韧性契约：入口闸门、健康检查宽限期与重启策略不得被回退。"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

PROJECT = Path(__file__).resolve().parents[1]
GATE = ["python", "scripts/wait_for_startup.py", "--"]


def merged(service: dict[str, Any]) -> dict[str, Any]:
    """展开 YAML 合并键（PyYAML 不解析 `<<`），得到服务最终生效的配置。"""
    result: dict[str, Any] = {}
    for key, value in service.items():
        if key != "<<":
            result[key] = value
            continue
        bases = value if isinstance(value, list) else [value]
        for base in bases:
            result = {**result, **base}
    return result


RAW = yaml.safe_load((PROJECT / "docker-compose.yml").read_text(encoding="utf-8"))
SERVICES = {name: merged(service) for name, service in RAW["services"].items()}


def test_api_and_worker_start_through_dependency_gate() -> None:
    """API 与 Worker 的启动命令都先经过依赖闸门。"""
    assert SERVICES["api"]["command"][: len(GATE)] == GATE
    assert SERVICES["api"]["command"][len(GATE):] == [
        "uvicorn",
        "taozhi.server.api:app",
        "--host",
        "127.0.0.1",
        "--port",
        "18080",
        "--no-access-log",
    ]
    assert SERVICES["worker"]["command"][: len(GATE)] == GATE
    assert SERVICES["worker"]["command"][len(GATE):] == [
        "python",
        "-m",
        "taozhi.server.worker",
    ]


def test_healthcheck_startup_grace_covers_recovery_windows() -> None:
    """健康检查宽限期覆盖数据库恢复、GPU 等待与闸门整体等待。"""
    graces = {
        name: service["healthcheck"]["start_period"]
        for name, service in SERVICES.items()
        if "healthcheck" in service
    }
    assert graces == {
        "postgres": "300s",
        "minio": "60s",
        "kafka": "120s",
        "elasticsearch": "180s",
        "embedding": "300s",
        "api": "600s",
    }


def test_restart_policy_keeps_single_container_self_healing() -> None:
    """除一次性迁移外，全部服务保持 unless-stopped，单容器崩溃仍能自愈。"""
    assert {name: service.get("restart") for name, service in SERVICES.items()} == {
        "postgres": "unless-stopped",
        "minio": "unless-stopped",
        "kafka": "unless-stopped",
        "elasticsearch": "unless-stopped",
        "embedding": "unless-stopped",
        "migrate": "no",
        "api": "unless-stopped",
        "worker": "unless-stopped",
    }


def test_compose_up_keeps_migration_and_middleware_conditions() -> None:
    """compose up 的依赖条件不得回退：迁移成功且中间件健康后应用才启动。"""
    for name in ("api", "worker"):
        depends = SERVICES[name]["depends_on"]
        assert depends["migrate"]["condition"] == "service_completed_successfully"
        for middleware in ("minio", "kafka", "elasticsearch", "embedding"):
            assert depends[middleware]["condition"] == "service_healthy"