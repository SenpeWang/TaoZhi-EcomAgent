"""市场调研多智能体运行时回调注册表、进度推送与 Steering 状态增量化助手。"""
from __future__ import annotations

import hashlib
import threading
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

from .schemas import AutonomousAgentState


@dataclass
class RuntimeHooks:
    """进程内线程安全运行时回调句柄，不进 LangGraph Checkpoint。"""

    check_cancel_fn: Optional[Callable[[], bool]] = None
    fetch_steering_fn: Optional[Callable[[], List[Any]]] = None
    emit_fn: Optional[Callable[[str, int], None]] = None
    cancel_event: threading.Event = field(default_factory=threading.Event)
    _lock: threading.Lock = field(default_factory=threading.Lock)
    _progress: int = 0


_HOOKS_LOCK = threading.Lock()
_HOOKS_BY_RUN_ID: Dict[str, RuntimeHooks] = {}


def register_hooks(run_id: str, hooks: RuntimeHooks) -> None:
    """登记本次图执行的运行时回调对象。"""
    with _HOOKS_LOCK:
        _HOOKS_BY_RUN_ID[run_id] = hooks


def unregister_hooks(run_id: str) -> None:
    """清理已完成或中止的运行时回调对象。"""
    with _HOOKS_LOCK:
        _HOOKS_BY_RUN_ID.pop(run_id, None)


def get_hooks(hook_run_id: str) -> RuntimeHooks:
    """获取本次运行句柄；未找到时返回空操作默认实例。"""
    if not hook_run_id:
        return RuntimeHooks()
    with _HOOKS_LOCK:
        return _HOOKS_BY_RUN_ID.get(hook_run_id) or RuntimeHooks()


def emit_progress(hooks: RuntimeHooks, stage: str, target_pct: Optional[int] = None) -> None:
    """线程安全地单调推进并广播阶段事件。"""
    with hooks._lock:
        if target_pct is not None:
            hooks._progress = max(hooks._progress, min(100, int(target_pct)))
        pct = hooks._progress
    if hooks.emit_fn is not None:
        try:
            hooks.emit_fn(stage, pct)
        except Exception:
            pass


def bump_progress(hooks: RuntimeHooks, delta: int, cap: int, stage: str) -> None:
    """在指定上限内单调递增进度并推送事件。"""
    with hooks._lock:
        if hooks._progress < cap:
            hooks._progress = min(cap, hooks._progress + delta)
        pct = hooks._progress
    if hooks.emit_fn is not None:
        try:
            hooks.emit_fn(stage, pct)
        except Exception:
            pass


def is_cancelled(hooks: RuntimeHooks, state_cancelled: bool = False) -> bool:
    """联合检查状态标记与外部实时取消信号。"""
    if state_cancelled or hooks.cancel_event.is_set():
        hooks.cancel_event.set()
        return True
    if hooks.check_cancel_fn is not None:
        try:
            if hooks.check_cancel_fn():
                hooks.cancel_event.set()
                return True
        except Exception:
            pass
    return False


def normalize_steering_item(item: Any) -> Optional[tuple[str, str]]:
    """统一将干预项归一化为 (event_id, text)。"""
    if isinstance(item, dict):
        text = str(item.get("text") or item.get("input") or "").strip()
        if not text:
            return None
        eid = str(item.get("id") or "").strip() or f"str:{hashlib.sha256(text.encode('utf-8')).hexdigest()[:16]}"
        return eid, text
    text = str(item or "").strip()
    if not text:
        return None
    return f"str:{hashlib.sha256(text.encode('utf-8')).hexdigest()[:16]}", text


def collect_new_steering(state: AutonomousAgentState, hooks: RuntimeHooks) -> tuple[List[str], List[str], List[str]]:
    """拉取并按 event_id 幂等去重外部动态干预指令。"""
    existing = list(state.get("steering_inbox") or [])
    seen_ids = set(state.get("steering_event_ids") or [])
    seen_texts = set(existing)
    delta_texts: List[str] = []
    delta_ids: List[str] = []
    if hooks.fetch_steering_fn is not None:
        try:
            remote_items = list(hooks.fetch_steering_fn() or [])
            for raw_item in remote_items:
                norm = normalize_steering_item(raw_item)
                if norm is None:
                    continue
                eid, text = norm
                if eid in seen_ids:
                    continue
                if not isinstance(raw_item, dict) and text in seen_texts:
                    continue
                seen_ids.add(eid)
                seen_texts.add(text)
                delta_texts.append(text)
                delta_ids.append(eid)
        except Exception:
            return [], [], existing
    return delta_texts, delta_ids, existing + delta_texts


def attach_steering_delta(res: Dict[str, Any], delta_texts: List[str], delta_ids: List[str]) -> Dict[str, Any]:
    """仅当本轮产生新增干预时写入累加通道，避免空增污染状态。"""
    if delta_texts:
        res["steering_inbox"] = delta_texts
    if delta_ids:
        res["steering_event_ids"] = delta_ids
    return res


def make_failover_emitter(hooks: RuntimeHooks) -> Callable[[Dict[str, Any]], None]:
    """构建脱敏故障转移事件实时推送回调（接入 SSE 事件流）。"""

    def _on_failover(ev: Dict[str, Any]) -> None:
        if ev.get("outcome") == "ok":
            emit_progress(hooks, f"[system] [模型容灾] 通道自适应切换至 {ev.get('label')} ({ev.get('region')} · {ev.get('model')})")
        else:
            emit_progress(hooks, f"[system] [模型容灾] {ev.get('label')} 异常({ev.get('reason')})，正在切换下一可用商汤通道")

    return _on_failover


def attach_route_delta(res: Dict[str, Any], events: List[Dict[str, Any]], last_ep: str) -> Dict[str, Any]:
    """将脱敏路由决策轨迹与最新健康端点写入 LangGraph Checkpoint 状态增量。"""
    if events:
        res["llm_route_history"] = events
    if last_ep:
        res["active_llm_endpoint"] = last_ep
    return res
