#!/usr/bin/env python3
"""应用容器启动前的依赖就绪闸门：依赖真正可用后再 exec 目标进程。

Compose 的 depends_on 只在 docker compose up 时生效。宿主机重启后容器由
restart: unless-stopped 策略拉起，彼此没有顺序保证：数据库还在崩溃恢复、向量服务还在
加载模型时，API 与 Worker 已经启动，表现为连接池超时、健康检查变红以及无谓的崩溃重启。
把「等待依赖」放进应用入口后，docker compose up、docker start 与重启策略三种拉起方式
都会先等到依赖可用，再 exec 目标进程。

探测口径与 compose 的 depends_on 对齐：数据库必探；MinIO、Kafka、Elasticsearch 与
向量服务按配置开关探测。全部就绪后 exec 目标命令，保持 PID 1 与信号语义；超时仍不就绪
则打印未就绪项并以非零码退出，交由容器重启策略重试。

用法：python scripts/wait_for_startup.py -- <目标命令> [参数...]
配置：ECOM_STARTUP_WAIT_SECONDS（整体超时，默认 600 秒）、
      ECOM_STARTUP_PROBE_INTERVAL（探测间隔，默认 2 秒）。
"""
from __future__ import annotations

import os
import sys
import time
from typing import Callable, List, Tuple

from taozhi.config import load_config

Probe = Callable[[], None]
Dependency = Tuple[str, Probe]


class NotReady(RuntimeError):
    """依赖尚未就绪；消息只保留可安全落日志的原因，不含连接串与口令。"""


class StartupTimeout(RuntimeError):
    """依赖在整体超时内仍未就绪。"""


def _probe_database(cfg) -> None:
    """探测数据库能否建立连接并执行一次查询。"""
    import psycopg

    try:
        with psycopg.connect(cfg.dsn, connect_timeout=3) as conn:
            conn.execute("SELECT 1")
    except Exception as exc:
        raise NotReady(f"数据库未就绪（{type(exc).__name__}）") from None


def _probe_minio(cfg) -> None:
    """探测 MinIO 存活接口。"""
    import httpx

    scheme = "https" if cfg.minio_secure else "http"
    try:
        response = httpx.get(
            f"{scheme}://{cfg.minio_endpoint}/minio/health/live", timeout=3.0, trust_env=False
        )
    except Exception as exc:
        raise NotReady(f"MinIO 未就绪（{type(exc).__name__}）") from None
    if response.status_code != 200:
        raise NotReady(f"MinIO 存活接口返回 HTTP {response.status_code}")


def _probe_kafka(cfg) -> None:
    """探测 Kafka 集群能否响应管理请求。"""
    from kafka.admin import KafkaAdminClient

    admin = None
    try:
        admin = KafkaAdminClient(
            bootstrap_servers=cfg.kafka_brokers.split(","), request_timeout_ms=3000
        )
        admin.list_topics()
    except Exception as exc:
        raise NotReady(f"Kafka 未就绪（{type(exc).__name__}）") from None
    finally:
        if admin is not None:
            try:
                admin.close()
            except Exception:
                pass


def _probe_elasticsearch(cfg) -> None:
    """探测 Elasticsearch 检索服务连通性。"""
    from taozhi.infrastructure.knowledge import es_index

    if not es_index.available():
        raise NotReady("Elasticsearch 未就绪（集群 ping 失败）")


def _probe_embedding(cfg) -> None:
    """探测向量服务健康接口。"""
    from taozhi.infrastructure.knowledge import embedding

    if not embedding.available():
        raise NotReady("向量服务未就绪（健康接口未返回 ok）")


def build_dependencies(cfg) -> List[Dependency]:
    """按配置开关组装依赖探测列表，顺序与 compose 的启动依赖一致。"""
    dependencies: List[Dependency] = [("数据库", lambda: _probe_database(cfg))]
    if cfg.minio_enabled:
        dependencies.append(("MinIO", lambda: _probe_minio(cfg)))
    if cfg.kafka_enabled:
        dependencies.append(("Kafka", lambda: _probe_kafka(cfg)))
    if cfg.es_enabled:
        dependencies.append(("Elasticsearch", lambda: _probe_elasticsearch(cfg)))
    dependencies.append(("向量服务", lambda: _probe_embedding(cfg)))
    return dependencies


def wait(
    dependencies: List[Dependency],
    *,
    timeout: float,
    interval: float,
    log: Callable[[str], None],
    monotonic: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
) -> None:
    """轮询依赖直到全部就绪；超出整体超时抛 StartupTimeout 并列出未就绪项。

    Args:
        dependencies: 依赖名称与探测函数的列表，探测函数不就绪时抛 NotReady。
        timeout: 整体等待上限（秒）。
        interval: 两轮探测之间的间隔（秒）。
        log: 输出单行进度的回调，容器内即标准输出。
        monotonic: 单调时钟，测试可注入。
        sleep: 等待函数，测试可注入。

    Raises:
        StartupTimeout: 到达整体上限仍有依赖未就绪。
    """
    started_at = monotonic()
    deadline = started_at + timeout
    pending = list(dependencies)
    reasons: dict[str, str] = {}
    while pending:
        remaining: List[Dependency] = []
        for name, probe in pending:
            try:
                probe()
            except NotReady as exc:
                reason = str(exc) or "未就绪"
                if reasons.get(name) != reason:
                    log(f"[启动闸门] 等待 {name}：{reason}")
                    reasons[name] = reason
                remaining.append((name, probe))
            else:
                reasons.pop(name, None)
                log(f"[启动闸门] {name} 已就绪（累计 {monotonic() - started_at:.0f}s）")
        pending = remaining
        if not pending:
            return
        now = monotonic()
        if now >= deadline:
            detail = "、".join(f"{name}（{reasons.get(name, '未就绪')}）" for name, _ in pending)
            raise StartupTimeout(f"依赖在 {timeout:.0f}s 内未就绪：{detail}")
        sleep(min(interval, deadline - now))


def parse_target(argv: List[str]) -> List[str] | None:
    """解析分隔符之后的目标命令；缺少分隔符或命令为空时返回 None。"""
    if "--" not in argv:
        return None
    target = argv[argv.index("--") + 1:]
    return target or None


def exec_target(target: List[str], log: Callable[[str], None]) -> int:
    """用目标进程替换当前进程（保留 PID 1 与信号语义），替换失败返回非零码。"""
    try:
        os.execvp(target[0], target)
    except OSError as exc:
        log(f"[启动闸门] 目标进程启动失败：{exc}")
        return 1
    return 0


def main(argv: List[str] | None = None) -> int:
    """等待依赖就绪后 exec 目标命令，参数非法或超时返回非零码。"""
    args = list(sys.argv[1:] if argv is None else argv)
    target = parse_target(args)
    if target is None:
        print("用法：python scripts/wait_for_startup.py -- <目标命令> [参数...]", file=sys.stderr)
        return 2
    try:
        timeout = float(os.environ.get("ECOM_STARTUP_WAIT_SECONDS", "600"))
        interval = float(os.environ.get("ECOM_STARTUP_PROBE_INTERVAL", "2"))
    except ValueError:
        print("[启动闸门] ECOM_STARTUP_WAIT_SECONDS 与 ECOM_STARTUP_PROBE_INTERVAL 须为数字", file=sys.stderr)
        return 1
    if timeout <= 0 or interval <= 0:
        print("[启动闸门] ECOM_STARTUP_WAIT_SECONDS 与 ECOM_STARTUP_PROBE_INTERVAL 须为正数", file=sys.stderr)
        return 1

    def log(message: str) -> None:
        """把单行进度打到标准输出，镜像已设置 PYTHONUNBUFFERED。"""
        print(message, flush=True)

    try:
        cfg = load_config()
        dependencies = build_dependencies(cfg)
    except Exception as exc:
        print(f"[启动闸门] 读取运行配置失败：{type(exc).__name__}: {exc}", file=sys.stderr, flush=True)
        return 1
    names = "、".join(name for name, _ in dependencies)
    log(f"[启动闸门] 等待依赖就绪（最多 {timeout:.0f}s）：{names}")
    try:
        wait(dependencies, timeout=timeout, interval=interval, log=log)
    except StartupTimeout as exc:
        log(f"[启动闸门] {exc}")
        return 1
    log(f"[启动闸门] 依赖全部就绪，启动目标进程：{' '.join(target)}")
    return exec_target(target, log)


if __name__ == "__main__":
    sys.exit(main())