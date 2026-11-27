"""后台限量批写 Span，任务执行线程只投递不可变快照。"""
from __future__ import annotations

import logging
import queue
import threading
import time
from dataclasses import dataclass, field

_LOG = logging.getLogger(__name__)
_CAPACITY = 4096
_BATCH_SIZE = 128
_QUEUE: queue.Queue = queue.Queue(maxsize=_CAPACITY)
_START_LOCK = threading.Lock()
_thread: threading.Thread | None = None
_last_warning = float("-inf")


@dataclass
class _Flush:
    """后台批写栅栏：等待此前入队记录提交完成。"""
    done: threading.Event = field(default_factory=threading.Event)
    success: bool = False


def _warn(reason: str) -> None:
    """按 30 秒节流输出链路上报告警。

    Args:
        reason: 告警原因。
    """
    global _last_warning
    now = time.monotonic()
    if now - _last_warning >= 30:
        _last_warning = now
        _LOG.warning("执行链路部分记录未能保存 (%s)", reason)


def _write(batch: list[dict]) -> bool:
    """批量写入 span 记录，失败时重试一次。

    Args:
        batch: 待写入的 span 记录列表。

    Returns:
        True 表示写入成功。
    """
    if not batch:
        return True
    for attempt in range(2):
        try:
            from taozhi.infrastructure.persistence.span_repo import write_spans

            write_spans(batch)
            return True
        except Exception as exc:
            if attempt:
                _warn(type(exc).__name__)
            else:
                time.sleep(0.1)
    return False


def _consume() -> None:
    """后台线程持续消费队列并批量写入，栅栏用于优雅关闭。"""
    success = True
    while True:
        entries = [_QUEUE.get()]
        while len(entries) < _BATCH_SIZE:
            try:
                entries.append(_QUEUE.get_nowait())
            except queue.Empty:
                break
        batch = []
        for entry in entries:
            if isinstance(entry, _Flush):
                entry.success = _write(batch) and success
                entry.done.set()
                success = True
                batch = []
            else:
                batch.append(entry)
        success = _write(batch) and success


def submit_span(row: dict) -> None:
    """主线程零等待入队，高水位时为根 Span 预留容量。"""
    global _thread
    if _thread is None or not _thread.is_alive():
        with _START_LOCK:
            if _thread is None or not _thread.is_alive():
                _thread = threading.Thread(target=_consume, name="taozhi-span-writer", daemon=True)
                _thread.start()
    if row["parent_span_id"] is not None and _QUEUE.qsize() >= _CAPACITY - 64:
        _warn("写入队列达到高水位")
        return
    try:
        _QUEUE.put_nowait(row)
    except queue.Full:
        _warn("写入队列已满")


def flush_spans(timeout: float = 2.0) -> bool:
    """在有界时间内等待此前已入队记录提交，供 Worker 优雅关闭使用。"""
    if _thread is None:
        return True
    barrier = _Flush()
    deadline = time.monotonic() + timeout
    try:
        _QUEUE.put(barrier, timeout=max(0, timeout))
    except queue.Full:
        return False
    return barrier.done.wait(max(0, deadline - time.monotonic())) and barrier.success
