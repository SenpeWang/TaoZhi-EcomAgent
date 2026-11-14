"""淘智 AI 大模型服务客户端。

统一集成商汤 sensenova-6.8-flash 国内/海外自适应容灾路由中间件、并发控制与结构化解析。
严格遵循 Google Python Style 规范。
"""

from __future__ import annotations

import threading
import time
from enum import Enum
from typing import Any, Dict, List, Optional, Type, Union

import httpx
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage
from langchain_core.output_parsers import JsonOutputParser
from langchain_openai import ChatOpenAI
from pydantic import BaseModel

from ..config import Settings, get_settings
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
    ) -> None:
        """初始化客户端。

        Args:
            settings: 全局运行时配置，默认从环境获取。
            allow_overseas: 是否允许在主通道失效时自动故障转移到商汤海外端点（企业涉密资料应设为 False）。
            registry: 可选自定义熔断注册表（默认使用全局共享注册表）。
        """
        self.settings = settings or get_settings()
        self.allow_overseas = allow_overseas
        self._registry = registry or get_circuit_registry()
        self._clients: Dict[str, ChatOpenAI] = {}
        self._slots = threading.BoundedSemaphore(self.settings.llm_max_concurrency)
        self._client_lock = threading.Lock()
        self._http_by_trust_env: Dict[bool, httpx.Client] = {
            False: httpx.Client(trust_env=False),
            True: httpx.Client(trust_env=True),
        }
        self._async_http_by_trust_env: Dict[bool, httpx.AsyncClient] = {
            False: httpx.AsyncClient(trust_env=False),
            True: httpx.AsyncClient(trust_env=True),
        }
        # 兼容外部直接引用属性
        default_trust = not self.settings.llm_direct_connection
        self._http = self._http_by_trust_env[default_trust]
        self._async_http = self._async_http_by_trust_env[default_trust]
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

    def _client(self, tier: ModelTier) -> ChatOpenAI:
        """兼容旧调用入口：返回首选商汤端点客户端。"""
        cfg = self.settings
        model = self._model_for_tier(tier)
        direct = cfg.llm_direct_connection
        trust_env = not bool(direct)
        with self._client_lock:
            if model not in self._clients:
                self._clients[model] = ChatOpenAI(
                    base_url=cfg.base_url,
                    api_key=cfg.api_key,
                    model=model,
                    temperature=cfg.llm_temperature,
                    max_tokens=cfg.llm_max_tokens,
                    timeout=cfg.llm_timeout,
                    max_retries=cfg.llm_max_retries,
                    http_client=self._http_by_trust_env[trust_env],
                    http_async_client=self._async_http_by_trust_env[trust_env],
                )
            return self._clients[model]

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
        self.last_route_events.append(event)
        if len(self.last_route_events) > 20:
            self.last_route_events = self.last_route_events[-20:]
        recorder = get_active_route_recorder()
        if recorder is not None:
            recorder.record(event)

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
            max_tokens: 最大生成 Token 数。
            _empty_retry: 空回复内部重试计数器。
            allow_overseas: 可选覆盖是否允许转移至商汤海外版。

        Returns:
            str: 模型生成的答复文本。
        """
        messages = self._to_messages(prompt)
        if system:
            messages = [SystemMessage(content=system)] + messages

        effective_overseas = self.allow_overseas if allow_overseas is None else allow_overseas
        budget = max(2048, min(max_tokens or self.settings.llm_max_tokens, self.settings.llm_output_cap))
        eff_temp = temperature if temperature is not None else self.settings.llm_temperature

        endpoints = build_sensenova_endpoints(self.settings)
        preferred = _PREFERRED_ENDPOINT.get()
        candidates = self._registry.select_candidates(
            endpoints,
            allow_overseas=effective_overseas,
            preferred_endpoint_id=preferred,
        )

        # 若测试桩仅模拟了默认通道或未配置 api_key，保留基础 _client 兜底路径
        if not candidates:
            fallback_ep = SensenovaEndpoint(
                endpoint_id="sensenova_default",
                label="商汤默认通道",
                region="domestic",
                base_url=self.settings.base_url,
                api_key=self.settings.api_key or "",
                model=self._model_for_tier(tier),
                direct_connection=self.settings.llm_direct_connection,
            )
            candidates = [fallback_ep]

        with self._slots:
            last_exc: Optional[Exception] = None
            result: Optional[AIMessage] = None
            had_failover = False

            for idx, ep in enumerate(candidates):
                try:
                    raw_client = (
                        self._client(tier)
                        if ep.endpoint_id == "sensenova_default"
                        else self._client_for_endpoint(ep)
                    )
                    bound_client = raw_client.bind(temperature=eff_temp, max_tokens=budget)
                    t0 = time.perf_counter()
                    result = bound_client.invoke(messages)
                    latency_ms = int((time.perf_counter() - t0) * 1000)
                    self._registry.record_success(ep, latency_ms)
                    self._record_route(
                        {
                            "endpoint_id": ep.endpoint_id,
                            "label": ep.label,
                            "region": ep.region,
                            "model": ep.model,
                            "outcome": "ok",
                            "reason": "failover_recovered" if had_failover else "primary_ok",
                            "latency_ms": latency_ms,
                            "failover": had_failover,
                        }
                    )
                    break
                except Exception as exc:
                    last_exc = exc
                    reason, cooldown = self._registry.classify_error(exc, self.settings)
                    self._registry.record_failure(ep, reason, cooldown)
                    has_next = idx < len(candidates) - 1
                    self._record_route(
                        {
                            "endpoint_id": ep.endpoint_id,
                            "label": ep.label,
                            "region": ep.region,
                            "model": ep.model,
                            "outcome": "failed",
                            "reason": reason,
                            "latency_ms": 0,
                            "failover": has_next,
                        }
                    )
                    if has_next:
                        had_failover = True
                        continue

                    # 仅剩最后一条通道且属瞬时抖动/限流时，额外退避重试一次
                    if len(candidates) == 1 and reason in ("rate_limit", "network_timeout", "upstream_error"):
                        time.sleep(0.5)
                        try:
                            t0 = time.perf_counter()
                            result = raw_client.bind(temperature=eff_temp, max_tokens=budget).invoke(messages)
                            latency_ms = int((time.perf_counter() - t0) * 1000)
                            self._registry.record_success(ep, latency_ms)
                            self._record_route(
                                {
                                    "endpoint_id": ep.endpoint_id,
                                    "label": ep.label,
                                    "region": ep.region,
                                    "model": ep.model,
                                    "outcome": "ok",
                                    "reason": "single_endpoint_retry_ok",
                                    "latency_ms": latency_ms,
                                    "failover": False,
                                }
                            )
                            break
                        except Exception as exc2:
                            last_exc = exc2
                    raise last_exc

        if result is None:
            raise last_exc or RuntimeError("所有商汤模型通道均不可用")

        text = result.content if isinstance(result.content, str) else str(result.content)
        text = clean_model_output(text)

        if not text and _empty_retry < self.settings.llm_empty_retries and budget < self.settings.llm_output_cap:
            return self.chat(
                prompt,
                tier=tier,
                system=system,
                temperature=temperature,
                max_tokens=min(self.settings.llm_output_cap, budget * 3),
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


_DEFAULT_CLIENT: Optional[LLMClient] = None


def get_llm() -> LLMClient:
    """获取全局默认 LLMClient 实例。"""
    global _DEFAULT_CLIENT
    if _DEFAULT_CLIENT is None:
        _DEFAULT_CLIENT = LLMClient()
    return _DEFAULT_CLIENT
