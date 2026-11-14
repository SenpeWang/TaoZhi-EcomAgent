"""商汤 sensenova-6.8-flash 国内/海外自适应容灾路由中间件与熔断器。

核心职责：
1. 统一收口商汤国内版（token.sensenova.cn）多 Key 与商汤海外版（token.sensenova.ai），
   全链路锁定 sensenova-6.8-flash 模型（已移除第三方异构云，模型行为完全一致）；
2. 每个通道拥有独立的传输策略（直连 / 系统代理），互不覆盖；
3. 按错误类型（429 限流 / 401-404 鉴权或模型错误 / 网络超时）自动熔断冷却并毫秒级
   故障转移至下一个健康通道，冷却期满自动半开探活恢复；
4. 支持合规门控（allow_overseas=False 时仅在国内节点内切换）及无密钥脱敏路由审计轨迹，
   便于与 LangGraph PostgresSaver Checkpointer 联动记录与断点续跑。
"""

from __future__ import annotations

import hashlib
import threading
import time
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any, Callable, Dict, Iterator, List, Literal, Optional, Tuple

from ..config import Settings, get_settings

RegionType = Literal["domestic", "overseas"]


@dataclass(frozen=True)
class SensenovaEndpoint:
    """单个商汤模型端点 Profile（含独立域名、密钥、模型与网络传输模式）。"""

    endpoint_id: str
    label: str
    region: RegionType
    base_url: str
    api_key: str
    model: str = "sensenova-6.8-flash"
    direct_connection: Optional[bool] = None
    priority: int = 0

    def fingerprint(self) -> str:
        """返回仅用于索引和审计去重的密钥指纹（绝不暴露原文）。"""
        return hashlib.sha256(self.api_key.encode("utf-8")).hexdigest()[:12]

    def cache_key(self) -> str:
        """返回用于内部客户端复用的唯一标识键。"""
        return f"{self.endpoint_id}:{self.base_url}:{self.model}:{self.fingerprint()}:{self.direct_connection}"


@dataclass
class _CircuitRecord:
    """单个端点的实时健康与熔断窗口记录。"""

    open_until: float = 0.0
    consecutive_failures: int = 0
    success_count: int = 0
    last_error_reason: str = ""
    last_latency_ms: int = 0
    last_checked_at: float = 0.0


class _RouteAuditRecorder:
    """线程安全的脱敏路由事件收集器（供 Checkpointer 状态持久化与 SSE 透传）。"""

    def __init__(
        self,
        max_events: int = 40,
        on_failover: Optional[Callable[[Dict[str, Any]], None]] = None,
    ) -> None:
        self._lock = threading.Lock()
        self._events: List[Dict[str, Any]] = []
        self._max_events = max_events
        self._on_failover = on_failover
        self.last_active_endpoint: str = ""

    def record(self, event: Dict[str, Any]) -> None:
        """写入脱敏路由事件，并在发生切线时通知实时回调。"""
        clean = {
            "endpoint_id": str(event.get("endpoint_id") or ""),
            "label": str(event.get("label") or ""),
            "region": str(event.get("region") or "domestic"),
            "model": str(event.get("model") or "sensenova-6.8-flash"),
            "outcome": str(event.get("outcome") or ""),
            "reason": str(event.get("reason") or ""),
            "latency_ms": int(event.get("latency_ms") or 0),
            "failover": bool(event.get("failover", False)),
            "ts": round(time.time(), 3),
        }
        with self._lock:
            if clean["outcome"] == "ok":
                self.last_active_endpoint = clean["endpoint_id"]
            if len(self._events) < self._max_events:
                self._events.append(clean)
        if clean["failover"] and self._on_failover is not None:
            try:
                self._on_failover(clean)
            except Exception:
                pass

    def snapshot(self) -> List[Dict[str, Any]]:
        """返回当前累积的脱敏路由轨迹副本。"""
        with self._lock:
            return list(self._events)


_ACTIVE_RECORDER: ContextVar[Optional[_RouteAuditRecorder]] = ContextVar(
    "taozhi_llm_route_recorder", default=None
)
_PREFERRED_ENDPOINT: ContextVar[Optional[str]] = ContextVar(
    "taozhi_llm_preferred_endpoint", default=None
)


@contextmanager
def route_audit_scope(
    preferred_endpoint_id: Optional[str] = None,
    on_failover: Optional[Callable[[Dict[str, Any]], None]] = None,
) -> Iterator[_RouteAuditRecorder]:
    """在当前任务/节点作用域内挂载脱敏路由收集器，供 LangGraph Checkpoint 采集。"""
    recorder = _RouteAuditRecorder(on_failover=on_failover)
    tok_rec = _ACTIVE_RECORDER.set(recorder)
    tok_pref = _PREFERRED_ENDPOINT.set(preferred_endpoint_id or None)
    try:
        yield recorder
    finally:
        _PREFERRED_ENDPOINT.reset(tok_pref)
        _ACTIVE_RECORDER.reset(tok_rec)


def get_active_route_recorder() -> Optional[_RouteAuditRecorder]:
    """返回当前上下文激活的路由收集器（未挂载则返回 None）。"""
    return _ACTIVE_RECORDER.get()


class CircuitBreakerRegistry:
    """跨请求共享的商汤多通道熔断状态注册表。"""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._records: Dict[str, _CircuitRecord] = {}

    def _state_key(self, ep: SensenovaEndpoint) -> str:
        return f"{ep.endpoint_id}:{ep.base_url}:{ep.fingerprint()}"

    def classify_error(self, exc: Exception, settings: Settings) -> Tuple[str, int]:
        """将模型异常归类为标准原因码与建议冷却秒数。"""
        msg = str(exc).lower()
        cls_name = type(exc).__name__.lower()
        combined = f"{cls_name} {msg}"

        if any(tok in combined for tok in ("401", "403", "invalid_api_key", "forbidden", "authentication")):
            return "auth_error", int(getattr(settings, "llm_circuit_cooldown_auth", 600))
        if any(tok in combined for tok in ("404", "model not found", "model_not_found")):
            return "model_not_found", int(getattr(settings, "llm_circuit_cooldown_auth", 600))
        if any(tok in combined for tok in ("429", "rate_limit", "rate limit", "tpm", "rpm", "quota")):
            return "rate_limit", int(getattr(settings, "llm_circuit_cooldown_rate_limit", 45))
        if any(tok in combined for tok in ("timeout", "timed out", "connect", "ssl", "handshake", "proxy", "protocol")):
            return "network_timeout", int(getattr(settings, "llm_circuit_cooldown_transient", 25))
        if any(tok in combined for tok in ("500", "502", "503", "504", "server_error", "overloaded")):
            return "upstream_error", int(getattr(settings, "llm_circuit_cooldown_transient", 25))
        return "invoke_error", int(getattr(settings, "llm_circuit_cooldown_transient", 25))

    def select_candidates(
        self,
        endpoints: List[SensenovaEndpoint],
        *,
        allow_overseas: bool = True,
        preferred_endpoint_id: Optional[str] = None,
    ) -> List[SensenovaEndpoint]:
        """按实时健康状态、合规范围与优先级排序返回可用候选端点列表。"""
        eligible = [ep for ep in endpoints if allow_overseas or ep.region == "domestic"]
        if not eligible:
            return []

        now = time.monotonic()
        healthy: List[SensenovaEndpoint] = []
        cooling: List[Tuple[float, int, SensenovaEndpoint]] = []

        with self._lock:
            for ep in eligible:
                rec = self._records.get(self._state_key(ep))
                if rec is None or now >= rec.open_until:
                    healthy.append(ep)
                else:
                    cooling.append((rec.open_until, ep.priority, ep))

        if preferred_endpoint_id:
            healthy.sort(key=lambda e: (0 if e.endpoint_id == preferred_endpoint_id else 1, e.priority))
        else:
            healthy.sort(key=lambda e: e.priority)

        # 若全部通道都在冷却中，仍按最早解封顺序保留探活兜底，避免彻底死锁
        cooling.sort(key=lambda item: (item[0], item[1]))
        return healthy + [item[2] for item in cooling]

    def record_success(self, ep: SensenovaEndpoint, latency_ms: int) -> None:
        """记录成功调用并闭合（恢复）断路器。"""
        now = time.monotonic()
        key = self._state_key(ep)
        with self._lock:
            rec = self._records.setdefault(key, _CircuitRecord())
            rec.open_until = 0.0
            rec.consecutive_failures = 0
            rec.last_error_reason = ""
            rec.success_count += 1
            rec.last_latency_ms = latency_ms
            rec.last_checked_at = now

    def record_failure(self, ep: SensenovaEndpoint, reason: str, cooldown_seconds: int) -> None:
        """记录失败并打开（或延长）断路器冷却窗口。"""
        now = time.monotonic()
        key = self._state_key(ep)
        with self._lock:
            rec = self._records.setdefault(key, _CircuitRecord())
            rec.consecutive_failures += 1
            multiplier = 1 if reason == "rate_limit" else min(3, rec.consecutive_failures)
            rec.open_until = now + float(cooldown_seconds * multiplier)
            rec.last_error_reason = reason
            rec.last_checked_at = now

    def snapshot(self, endpoints: List[SensenovaEndpoint]) -> List[Dict[str, Any]]:
        """返回无密钥的健康快照供诊断或监控展示。"""
        now = time.monotonic()
        rows: List[Dict[str, Any]] = []
        with self._lock:
            for ep in endpoints:
                rec = self._records.get(self._state_key(ep))
                remaining = max(0.0, rec.open_until - now) if rec else 0.0
                rows.append(
                    {
                        "endpoint_id": ep.endpoint_id,
                        "label": ep.label,
                        "region": ep.region,
                        "base_url": ep.base_url,
                        "model": ep.model,
                        "state": "open" if remaining > 0 else "closed",
                        "remaining_cooldown_s": round(remaining, 1),
                        "consecutive_failures": rec.consecutive_failures if rec else 0,
                        "success_count": rec.success_count if rec else 0,
                        "last_error_reason": rec.last_error_reason if rec else "",
                        "last_latency_ms": rec.last_latency_ms if rec else 0,
                    }
                )
        return rows

    def reset(self) -> None:
        """清空全部熔断状态（仅供测试隔离使用）。"""
        with self._lock:
            self._records.clear()


_GLOBAL_REGISTRY = CircuitBreakerRegistry()


def get_circuit_registry() -> CircuitBreakerRegistry:
    """获取进程级共享熔断状态注册表。"""
    return _GLOBAL_REGISTRY


def _infer_region(base_url: str) -> RegionType:
    return "overseas" if "sensenova.ai" in (base_url or "").lower() else "domestic"


def build_sensenova_endpoints(settings: Settings) -> List[SensenovaEndpoint]:
    """根据运行配置构建仅面向商汤 sensenova-6.8-flash 的国内/海外候选通道池。"""
    target_model = settings.model or "sensenova-6.8-flash"
    endpoints: List[SensenovaEndpoint] = []
    seen_pairs = set()

    def _add(
        endpoint_id: str,
        label: str,
        region: RegionType,
        base_url: str,
        api_key: str,
        model: str,
        direct_connection: Optional[bool],
    ) -> None:
        clean_url = (base_url or "").strip().rstrip("/")
        clean_key = (api_key or "").strip()
        if not clean_url or not clean_key:
            return
        fp = hashlib.sha256(clean_key.encode("utf-8")).hexdigest()[:12]
        dedup_key = (clean_url, fp, model)
        if dedup_key in seen_pairs:
            return
        seen_pairs.add(dedup_key)
        endpoints.append(
            SensenovaEndpoint(
                endpoint_id=endpoint_id,
                label=label,
                region=region,
                base_url=clean_url,
                api_key=clean_key,
                model=model or target_model,
                direct_connection=direct_connection,
                priority=len(endpoints),
            )
        )

    # 1. 国内主通道（.cn 主 Key）
    _add(
        "sensenova_cn_primary",
        "商汤国内版（主通道）",
        _infer_region(settings.base_url),
        settings.base_url,
        settings.api_key,
        target_model,
        settings.llm_direct_connection,
    )

    # 2. 国内备用 Key（.cn 第二 Key）
    secondary_cn_key = getattr(settings, "sensenova_cn_secondary_api_key", "")
    _add(
        "sensenova_cn_secondary",
        "商汤国内版（备选通道）",
        _infer_region(settings.base_url),
        settings.base_url,
        secondary_cn_key,
        target_model,
        settings.llm_direct_connection,
    )

    # 3. 商汤海外版容灾通道（token.sensenova.ai + sensenova-6.8-flash，默认可走代理）
    overseas_key = getattr(settings, "sensenova_overseas_api_key", "")
    overseas_url = getattr(settings, "sensenova_overseas_base_url", "https://token.sensenova.ai/v1")
    overseas_model = getattr(settings, "sensenova_overseas_model", "") or target_model
    overseas_direct = bool(getattr(settings, "sensenova_overseas_direct_connection", False))
    _add(
        "sensenova_overseas",
        "商汤海外版（sensenova.ai）",
        "overseas",
        overseas_url,
        overseas_key,
        overseas_model,
        overseas_direct,
    )

    # 4. 兼容传统 LLM_FALLBACK_* 环境变量备线（若仍指向商汤）
    fb_base = (getattr(settings, "llm_fallback_base_url", "") or "").strip()
    fb_key = (getattr(settings, "llm_fallback_api_key", "") or "").strip()
    fb_model = (getattr(settings, "llm_fallback_model", "") or target_model).strip()
    if fb_base or fb_key:
        eff_url = fb_base or settings.base_url
        eff_region = _infer_region(eff_url)
        _add(
            "sensenova_custom_fallback",
            "商汤自定义备线",
            eff_region,
            eff_url,
            fb_key or settings.api_key,
            fb_model,
            overseas_direct if eff_region == "overseas" else settings.llm_direct_connection,
        )

    return endpoints


def get_router_health(settings: Optional[Settings] = None) -> List[Dict[str, Any]]:
    """查询当前配置的商汤全通道健康状态快照（无密钥明文）。"""
    cfg = settings or get_settings()
    endpoints = build_sensenova_endpoints(cfg)
    return _GLOBAL_REGISTRY.snapshot(endpoints)
