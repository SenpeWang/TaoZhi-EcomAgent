"""淘智 AI 大模型服务客户端。

统一集成商汤 sensenova-6.8-flash 国内/海外自适应容灾路由中间件、并发控制与结构化解析。
严格遵循 Google Python Style 规范。
"""

from __future__ import annotations

import logging
import threading
import time
from enum import Enum
from typing import Any, Callable, Dict, List, Optional, Type, Union

import httpx
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage
from langchain_core.output_parsers import JsonOutputParser
from langchain_openai import ChatOpenAI
from openinference.semconv.trace import SpanAttributes
from pydantic import BaseModel

from ..config import Settings, get_settings
from ..observability.tracing import node_span
from .observability import get_active_usage, record_llm_call, record_llm_error
from .parsers import _JSON_RULES, clean_model_output, get_pydantic_parser
from .router import (
    _PREFERRED_ENDPOINT,
    CircuitBreakerRegistry,
    SensenovaEndpoint,
    build_sensenova_endpoints,
    get_active_route_recorder,
    get_circuit_registry,
)
from .usage import extract_usage

Message = Union[str, BaseMessage, Dict[str, str]]
_LOG = logging.getLogger(__name__)


class ModelTier(str, Enum):
    """模型调用层级（当前全线统一路由至 sensenova-6.8-flash）。"""

    FAST = "fast"
    STRONG = "strong"
    VISION = "vision"


class LLMClient:
    """大模型调用客户端，接入商汤国内/海外故障转移中间件与熔断恢复机制。"""

    def __init__(
        self,
        settings: Optional[Settings] = None,
        allow_overseas: bool = True,
        registry: Optional[CircuitBreakerRegistry] = None,
        on_attempt: Optional[Callable[[Dict[str, Any]], None]] = None,
    ) -> None:
        """初始化客户端。

        Args:
            settings: 全局运行时配置，默认从环境获取。
            allow_overseas: 是否允许在主通道失效时自动故障转移到商汤海外端点（企业涉密资料应设为 False）。
            registry: 可选自定义熔断注册表（默认使用全局共享注册表）。
            on_attempt: 收到单次端点调用结果后的计量回调，仅传递路由元数据。
        """
        self.settings = settings or get_settings()
        self.allow_overseas = allow_overseas
        self._registry = registry or get_circuit_registry()
        self._on_attempt = on_attempt
        self._clients: Dict[str, ChatOpenAI] = {}
        self._slots = threading.BoundedSemaphore(self.settings.llm_max_concurrency)
        self._client_lock = threading.Lock()
        req_timeout = httpx.Timeout(float(self.settings.llm_timeout or 90), connect=8.0)
        self._http_by_trust_env: Dict[bool, httpx.Client] = {
            False: httpx.Client(trust_env=False, timeout=req_timeout),
            True: httpx.Client(trust_env=True, timeout=req_timeout),
        }
        self._async_http_by_trust_env: Dict[bool, httpx.AsyncClient] = {
            False: httpx.AsyncClient(trust_env=False, timeout=req_timeout),
            True: httpx.AsyncClient(trust_env=True, timeout=req_timeout),
        }
        self.last_route_events: List[Dict[str, Any]] = []

    def _model_for_tier(self, tier: ModelTier, override_model: str = "") -> str:
        """统一模型名解析：仅限 sensenova-6.8-flash 体系。"""
        cfg = self.settings
        if override_model:
            return override_model
        mapped = {
            ModelTier.FAST: cfg.llm_fast_model,
            ModelTier.STRONG: cfg.llm_strong_model,
            ModelTier.VISION: cfg.llm_vision_model,
        }.get(tier, "")
        return mapped or cfg.model or "sensenova-6.8-flash"

    def _client_for_endpoint(self, ep: SensenovaEndpoint) -> ChatOpenAI:
        """为指定商汤端点构建/复用绑定独立网络策略的 ChatOpenAI 客户端。"""
        cfg = self.settings
        direct = cfg.llm_direct_connection if ep.direct_connection is None else ep.direct_connection
        trust_env = not bool(direct)
        key = ep.cache_key()
        with self._client_lock:
            if key not in self._clients:
                self._clients[key] = ChatOpenAI(
                    base_url=ep.base_url,
                    api_key=ep.api_key,
                    model=ep.model or self._model_for_tier(ModelTier.FAST),
                    temperature=cfg.llm_temperature,
                    max_tokens=cfg.llm_max_tokens,
                    timeout=cfg.llm_timeout,
                    max_retries=cfg.llm_max_retries,
                    http_client=self._http_by_trust_env[trust_env],
                    http_async_client=self._async_http_by_trust_env[trust_env],
                )
            return self._clients[key]

    @staticmethod
    def _to_messages(payload: Union[str, List[Message]]) -> List[BaseMessage]:
        if isinstance(payload, str):
            return [HumanMessage(content=payload)]
        msgs: List[BaseMessage] = []
        for item in payload:
            if isinstance(item, BaseMessage):
                msgs.append(item)
            elif isinstance(item, dict):
                role = item.get("role", "user")
                content = item.get("content", "")
                if role == "system":
                    msgs.append(SystemMessage(content=content))
                elif role == "assistant":
                    msgs.append(AIMessage(content=content))
                else:
                    msgs.append(HumanMessage(content=content))
            else:
                msgs.append(HumanMessage(content=str(item)))
        return msgs

    def _record_route(self, event: Dict[str, Any]) -> None:
        event["started_at"] = time.time() - event["latency_ms"] / 1000
        self.last_route_events.append(event)
        if len(self.last_route_events) > 20:
            self.last_route_events = self.last_route_events[-20:]
        recorder = get_active_route_recorder()
        if recorder is not None:
            recorder.record(event)
        if self._on_attempt is not None:
            try:
                self._on_attempt(event)
            except Exception as exc:
                _LOG.warning("模型调用计量写入失败 (%s)", type(exc).__name__)

    def _on_invoke_success(
        self,
        ep: SensenovaEndpoint,
        tier: ModelTier,
        usage: Dict[str, int],
        elapsed_s: float,
        *,
        failover: bool,
        reason: str,
    ) -> None:
        latency_ms = int(elapsed_s * 1000)
        record_llm_call(
            tier.value,
            elapsed_s,
            usage["prompt_tokens"],
            usage["completion_tokens"],
            failover=failover,
        )
        self._registry.record_success(ep, latency_ms)
        self._record_route(
            {
                "endpoint_id": ep.endpoint_id,
                "label": ep.label,
                "region": ep.region,
                "model": ep.model,
                "outcome": "ok",
                "reason": reason,
                "latency_ms": latency_ms,
                "prompt_tokens": usage["prompt_tokens"],
                "completion_tokens": usage["completion_tokens"],
                "failover": failover,
            }
        )

    def chat(
        self,
        prompt: Union[str, List[Message]],
        tier: ModelTier = ModelTier.FAST,
        system: str = "",
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
        _empty_retry: int = 0,
        allow_overseas: Optional[bool] = None,
    ) -> str:
        """执行对话请求：优先选用健康商汤通道，失效时立即熔断并故障转移。

        Args:
            prompt: 提示词或消息列表。
            tier: 模型调用层级。
            system: 系统提示词。
            temperature: 采样温度。
            max_tokens: 最大生成 Token 数（显式传入时按调用方上限控制，不再被强拉高至 2048）。
            _empty_retry: 空回复内部重试计数器。
            allow_overseas: 可选覆盖是否允许转移至商汤海外版。

        Returns:
            str: 模型生成的答复文本。
        """
        messages = self._to_messages(prompt)
        if system:
            messages = [SystemMessage(content=system)] + messages

        effective_overseas = self.allow_overseas if allow_overseas is None else allow_overseas
        if max_tokens is not None:
            output_token_limit = max(64, min(int(max_tokens), self.settings.llm_output_cap))
        else:
            output_token_limit = max(2048, min(self.settings.llm_max_tokens, self.settings.llm_output_cap))
        sampling_temperature = temperature if temperature is not None else self.settings.llm_temperature

        endpoints = build_sensenova_endpoints(self.settings)
        preferred_endpoint_id = _PREFERRED_ENDPOINT.get()
        candidates = self._registry.select_candidates(
            endpoints,
            allow_overseas=effective_overseas,
            preferred_endpoint_id=preferred_endpoint_id,
        )

        if not candidates:
            fallback_endpoint = SensenovaEndpoint(
                endpoint_id="sensenova_default",
                label="商汤默认通道",
                region="domestic",
                base_url=self.settings.base_url,
                api_key=self.settings.api_key or "",
                model=self._model_for_tier(tier),
                direct_connection=self.settings.llm_direct_connection,
            )
            candidates = [fallback_endpoint]

        with self._slots:
            result: Optional[AIMessage] = None
            for endpoint_index, endpoint in enumerate(candidates):
                retried_endpoint = False
                while True:
                    attempt_started_at = time.perf_counter()
                    try:
                        model_attributes = {
                            "llm.endpoint_id": endpoint.endpoint_id,
                            SpanAttributes.LLM_MODEL_NAME: endpoint.model or self._model_for_tier(tier),
                        }
                        with node_span("llm.chat", "LLM", model_attributes) as span:
                            model_client = self._client_for_endpoint(endpoint)
                            result = model_client.bind(
                                temperature=sampling_temperature, max_tokens=output_token_limit,
                            ).invoke(messages)
                            usage = extract_usage(result)
                            if span is not None:
                                span.set_attribute(SpanAttributes.LLM_TOKEN_COUNT_PROMPT, usage["prompt_tokens"])
                                span.set_attribute(SpanAttributes.LLM_TOKEN_COUNT_COMPLETION, usage["completion_tokens"])
                                span.set_attribute(SpanAttributes.LLM_TOKEN_COUNT_TOTAL, usage["total_tokens"])
                    except Exception as exc:
                        latency_ms = int((time.perf_counter() - attempt_started_at) * 1000)
                        reason, cooldown = self._registry.classify_error(exc, self.settings)
                        self._registry.record_failure(endpoint, reason, cooldown)
                        has_next = endpoint_index < len(candidates) - 1
                        self._record_route(
                            {
                                "endpoint_id": endpoint.endpoint_id,
                                "label": endpoint.label,
                                "region": endpoint.region,
                                "model": endpoint.model,
                                "outcome": "failed",
                                "reason": reason,
                                "latency_ms": latency_ms,
                                "failover": has_next,
                            }
                        )
                        if has_next:
                            break
                        transient_failure = reason in ("rate_limit", "network_timeout", "upstream_error")
                        if len(candidates) == 1 and not retried_endpoint and transient_failure:
                            retried_endpoint = True
                            time.sleep(0.5)
                            continue
                        record_llm_error(tier.value)
                        raise
                    used_failover = endpoint_index > 0
                    reason = "failover_recovered" if used_failover else "primary_ok"
                    self._on_invoke_success(
                        endpoint, tier, usage, time.perf_counter() - attempt_started_at, failover=used_failover,
                        reason="single_endpoint_retry_ok" if retried_endpoint else reason,
                    )
                    break
                if result is not None:
                    break

        if result is None:
            raise RuntimeError("所有商汤模型通道均不可用")

        text = result.content if isinstance(result.content, str) else str(result.content)
        text = clean_model_output(text)

        if (not text and _empty_retry < self.settings.llm_empty_retries
                and output_token_limit < self.settings.llm_output_cap):
            return self.chat(
                prompt,
                tier=tier,
                system=system,
                temperature=temperature,
                max_tokens=min(self.settings.llm_output_cap, output_token_limit * 3),
                _empty_retry=_empty_retry + 1,
                allow_overseas=allow_overseas,
            )
        return text

    def chat_json(
        self,
        prompt: str,
        schema: Optional[Type[BaseModel]] = None,
        schema_hint: str = "",
        tier: ModelTier = ModelTier.FAST,
        system: str = "",
        retries: int = 3,
        default: Any = None,
        expect: str = "object",
        max_tokens: Optional[int] = None,
        allow_overseas: Optional[bool] = None,
    ) -> Any:
        """请求模型返回结构化 JSON 数据，自动执行 Pydantic 校验与重试。

        Args:
            prompt: 输入指令提示词。
            schema: 可选的 Pydantic 数据规范类。
            schema_hint: 格式提示说明。
            tier: 模型分级。
            system: 系统角色指令。
            retries: 解析失败重试次数。
            default: 终态兜底值。
            expect: 预期结构类型 ("object" 或 "array")。
            max_tokens: 最大 Token 预算。
            allow_overseas: 可选覆盖是否允许转移至商汤海外版。

        Returns:
            Any: 解析并校验后的字典或列表对象。
        """
        parser = get_pydantic_parser(schema)
        hint = schema_hint or (parser.get_format_instructions() if parser is not None else "")
        full_prompt = f"{prompt}\n\n{hint}\n{_JSON_RULES}".strip()
        last_raw = ""

        for attempt in range(retries):
            acc = get_active_usage()
            if acc is not None:
                acc.add_json_attempt(is_retry=(attempt > 0))
            try:
                raw = self.chat(
                    full_prompt
                    if attempt == 0
                    else f"{full_prompt}\n\n上一次输出无法解析为合法 JSON：\n{last_raw[:400]}\n请严格重新输出规范 JSON。",
                    tier=tier,
                    system=system or "你是严谨的电商业务结构化信息抽取引擎。",
                    allow_overseas=allow_overseas,
                    **({"max_tokens": max_tokens} if max_tokens else {}),
                )
            except Exception:
                if attempt == retries - 1:
                    return default if default is not None else ({} if expect == "object" else [])
                continue

            last_raw = raw
            try:
                return parser.parse(raw) if parser is not None else JsonOutputParser().parse(raw)
            except Exception:
                pass

        return default if default is not None else ({} if expect == "object" else [])

    def close(self) -> None:
        """关闭底层同步 HTTP 连接池。"""
        for http_client in self._http_by_trust_env.values():
            http_client.close()


_DEFAULT_CLIENT: Optional[LLMClient] = None


def get_llm() -> LLMClient:
    """获取全局默认 LLMClient 实例。"""
    global _DEFAULT_CLIENT
    if _DEFAULT_CLIENT is None:
        _DEFAULT_CLIENT = LLMClient()
    return _DEFAULT_CLIENT
