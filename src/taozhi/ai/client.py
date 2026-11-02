"""淘智 AI 大模型服务客户端。

统一管理模型分级路由 (fast / strong / vision)、超时重试、并发控制与结构化解析。
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
from .usage import extract_usage

Message = Union[str, BaseMessage, Dict[str, str]]


class ModelTier(str, Enum):
    """模型梯度分级。"""
    FAST = "fast"
    STRONG = "strong"
    VISION = "vision"


class LLMClient:
    """大模型调用客户端，统一封装模型路由与上下文预算保护。"""

    def __init__(self, settings: Optional[Settings] = None) -> None:
        """初始化客户端。

        Args:
            settings: 全局运行时配置，默认从环境获取。
        """
        self.settings = settings or get_settings()
        self._clients: Dict[str, ChatOpenAI] = {}
        self._slots = threading.BoundedSemaphore(self.settings.llm_max_concurrency)
        self._client_lock = threading.Lock()
        self._http = httpx.Client(trust_env=not self.settings.llm_direct_connection)
        self._async_http = httpx.AsyncClient(trust_env=not self.settings.llm_direct_connection)

    def _client(self, tier: ModelTier) -> ChatOpenAI:
        cfg = self.settings
        model = {
            ModelTier.FAST: cfg.llm_fast_model,
            ModelTier.STRONG: cfg.llm_strong_model,
            ModelTier.VISION: cfg.llm_vision_model,
        }[tier]
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
                    http_client=self._http,
                    http_async_client=self._async_http,
                )
            return self._clients[model]

    def _fallback_client(self, tier: ModelTier) -> Optional[ChatOpenAI]:
        cfg = self.settings
        fb_base = getattr(cfg, "llm_fallback_base_url", "")
        fb_key = getattr(cfg, "llm_fallback_api_key", "")
        fb_model = getattr(cfg, "llm_fallback_model", "")
        if not fb_base and not fb_model:
            return None
        model = fb_model or cfg.llm_fast_model or cfg.model
        key = f"fallback_{model}"
        with self._client_lock:
            if key not in self._clients:
                self._clients[key] = ChatOpenAI(
                    base_url=fb_base or cfg.base_url,
                    api_key=fb_key or cfg.api_key,
                    model=model,
                    temperature=cfg.llm_temperature,
                    max_tokens=cfg.llm_max_tokens,
                    timeout=cfg.llm_timeout,
                    max_retries=1,
                    http_client=self._http,
                    http_async_client=self._async_http,
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

    def chat(
        self,
        prompt: Union[str, List[Message]],
        tier: ModelTier = ModelTier.FAST,
        system: str = "",
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
        _empty_retry: int = 0,
    ) -> str:
        """执行对话请求并返回响应纯文本。

        Args:
            prompt: 提示词或消息列表。
            tier: 模型等级 (FAST, STRONG, VISION)。
            system: 系统提示词。
            temperature: 采样温度。
            max_tokens: 最大生成 Token 数。
            _empty_retry: 空回复内部重试计数器。

        Returns:
            str: 模型生成的答复文本。
        """
        messages = self._to_messages(prompt)
        if system:
            messages = [SystemMessage(content=system)] + messages
        started = time.perf_counter()
        client = self._client(tier)
        budget = max(2048, min(max_tokens or self.settings.llm_max_tokens, self.settings.llm_output_cap))
        client = client.bind(
            temperature=temperature if temperature is not None else self.settings.llm_temperature,
            max_tokens=budget,
        )

        with self._slots:
            max_attempts = 3
            backoff = 0.5
            last_exc = None
            result: Optional[AIMessage] = None
            for attempt in range(max_attempts):
                try:
                    result = client.invoke(messages)
                    break
                except Exception as exc:
                    last_exc = exc
                    err_str = str(exc).lower()
                    is_rate_limit = "429" in err_str or "rate limit" in err_str or "quota" in err_str
                    is_timeout = "timeout" in err_str or "timed out" in err_str
                    if (is_rate_limit or is_timeout) and attempt < max_attempts - 1:
                        time.sleep(backoff)
                        backoff *= 2
                        continue
                    fb_client = self._fallback_client(tier)
                    if fb_client:
                        try:
                            result = fb_client.bind(
                                temperature=temperature if temperature is not None else self.settings.llm_temperature,
                                max_tokens=budget,
                            ).invoke(messages)
                            break
                        except Exception:
                            pass
                    raise last_exc

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
                    full_prompt if attempt == 0
                    else f"{full_prompt}\n\n上一次输出无法解析为合法 JSON：\n{last_raw[:400]}\n请严格重新输出规范 JSON。",
                    tier=tier,
                    system=system or "你是严谨的电商业务结构化信息抽取引擎。",
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
