"""LLM 服务层：模型分级路由 + 健壮结构化输出 + 用量统计。

模型分级路由（PRD N4）：
    fast   —— 日常解析 / 抽取 / 摘要
    strong —— 复杂研判 / 裁决 / 报告生成
    vision —— 多模态（Qwen3-VL）
"""

from __future__ import annotations

import asyncio
import time
from enum import Enum
from typing import Any, AsyncIterator, Dict, List, Optional, Type, Union

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI
import httpx
from pydantic import BaseModel

from ..config import Settings, get_settings
from ..observability import metrics as _metrics
from .json_utils import dump, extract_json, parse_model, to_json_schema_hint

Message = Union[str, BaseMessage, Dict[str, str]]


class ModelTier(str, Enum):
    FAST = "fast"
    STRONG = "strong"
    VISION = "vision"


_JSON_RULES = """【输出格式要求】
1. 只输出一个 JSON 对象，**不要**输出任何解释、标题、前言或 markdown 代码块围栏；
2. 不要使用单引号、注释或尾随逗号；
3. 所有字符串使用双引号，必须严格符合下面给出的字段结构；
4. 若某项确无信息，使用空字符串 / 空数组，禁止省略字段。
"""


class LLMClient:
    """对 LangChain ChatOpenAI 的封装，屏蔽端点差异。"""

    def __init__(self, settings: Optional[Settings] = None) -> None:
        self.settings = settings or get_settings()
        self._clients: Dict[str, ChatOpenAI] = {}
        import threading
        self._slots = threading.BoundedSemaphore(self.settings.llm_max_concurrency)
        self._client_lock = threading.Lock()
        self._http = httpx.Client(trust_env=not self.settings.llm_direct_connection)
        self._async_http = httpx.AsyncClient(trust_env=not self.settings.llm_direct_connection)

    # ───────────────── 客户端 ─────────────────
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
                msgs.append(
                    SystemMessage(content=content) if role == "system"
                    else AIMessage(content=content) if role == "assistant"
                    else HumanMessage(content=content)
                )
            else:
                msgs.append(HumanMessage(content=str(item)))
        return msgs

    # ───────────────── 同步 / 异步对话 ─────────────────
    def chat(
        self,
        prompt: Union[str, List[Message]],
        tier: ModelTier = ModelTier.FAST,
        system: str = "",
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
        _empty_retry: int = 0,
    ) -> str:
        messages = self._to_messages(prompt)
        if system:
            messages = [SystemMessage(content=system)] + messages
        started = time.perf_counter()
        client = self._client(tier)
        budget = max(2048,min(max_tokens or self.settings.llm_max_tokens,self.settings.llm_output_cap))
        client = client.bind(
            temperature=temperature if temperature is not None else self.settings.llm_temperature,
            max_tokens=budget,
        )
        try:
            with self._slots:
                result: AIMessage = client.invoke(messages)
        except Exception as exc:  # 端点异常不熔断，交给上层降级
            _metrics.record_llm_error(tier.value)
            raise
        text = result.content if isinstance(result.content, str) else str(result.content)
        usage = _extract_usage(result)
        _metrics.record_llm_call(
            tier.value,
            time.perf_counter() - started,
            usage.get("prompt_tokens", 0),
            usage.get("completion_tokens", 0),
        )
        text = _clean(text)
        # 推理型模型会把预算全部消耗在思维链上，导致正文为空 —— 自动扩容重试
        if not text and _empty_retry < self.settings.llm_empty_retries and budget < self.settings.llm_output_cap:
            _metrics.inc("llm.empty_retry")
            return self.chat(prompt, tier=tier, system=system, temperature=temperature,
                             max_tokens=min(self.settings.llm_output_cap, budget * 3), _empty_retry=_empty_retry + 1)
        return text

    async def achat(
        self,
        prompt: Union[str, List[Message]],
        tier: ModelTier = ModelTier.FAST,
        system: str = "",
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
    ) -> str:
        return await asyncio.to_thread(
            self.chat, prompt, tier, system, temperature, max_tokens
        )

    def stream(
        self,
        prompt: Union[str, List[Message]],
        tier: ModelTier = ModelTier.FAST,
        system: str = "",
    ):
        messages = self._to_messages(prompt)
        if system:
            messages = [SystemMessage(content=system)] + messages
        for chunk in self._client(tier).stream(messages):
            content = getattr(chunk, "content", "")
            if isinstance(content, str) and content:
                yield content

    async def astream(
        self,
        prompt: Union[str, List[Message]],
        tier: ModelTier = ModelTier.FAST,
        system: str = "",
    ) -> AsyncIterator[str]:
        loop = asyncio.get_running_loop()
        queue: asyncio.Queue = asyncio.Queue()

        def _producer():
            try:
                for piece in self.stream(prompt, tier=tier, system=system):
                    loop.call_soon_threadsafe(queue.put_nowait, piece)
            except Exception as exc:
                loop.call_soon_threadsafe(queue.put_nowait, exc)
            finally:
                loop.call_soon_threadsafe(queue.put_nowait, None)

        import threading

        threading.Thread(target=_producer, daemon=True).start()
        while True:
            item = await queue.get()
            if item is None:
                break
            if isinstance(item, Exception):
                raise item
            yield item

    # ───────────────── 结构化输出 ─────────────────
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
    ) -> Any:
        """请求 LLM 返回 JSON；失败时自动重生成（PRD E3 缺失即重生成）。"""
        hint = schema_hint or (to_json_schema_hint(schema) if schema else "")
        full_prompt = f"{prompt}\n\n{hint}\n{_JSON_RULES}".strip()
        last_raw = ""
        for attempt in range(retries):
            try:
                raw = self.chat(
                    full_prompt if attempt == 0
                    else f"{full_prompt}\n\n上一次输出无法解析为合法 JSON：\n{last_raw[:400]}\n请重新严格输出 JSON。",
                    tier=tier,
                    system=system or "你是严谨的产业研究结构化信息抽取引擎。",
                )
            except Exception as exc:
                _metrics.record_llm_error(tier.value)
                if attempt == retries - 1:
                    return default if default is not None else ({} if expect == "object" else [])
                continue
            last_raw = raw
            data = extract_json(raw, expect=expect)
            if data is None:
                continue
            if schema is None:
                return data
            parsed = parse_model(dump(data) if not isinstance(data, str) else data, schema, expect=expect)
            if parsed is not None:
                return parsed
        if schema is not None and default is None:
            try:
                return None
            except Exception:
                return None
        return default if default is not None else ({} if expect == "object" else [])

    async def achat_json(self, *args, **kwargs) -> Any:
        return await asyncio.to_thread(self.chat_json, *args, **kwargs)

    def chat_model(
        self,
        prompt: str,
        schema: Type[BaseModel],
        tier: ModelTier = ModelTier.FAST,
        system: str = "",
        retries: int = 3,
    ) -> BaseModel:
        result = self.chat_json(prompt, schema=schema, tier=tier, system=system, retries=retries)
        if not isinstance(result, schema):
            raise ValueError("Model output failed schema validation")
        return result


def _clean(text: str) -> str:
    """去掉部分端点会回传的思维链前缀标记。"""
    if not text:
        return ""
    text = text.strip()
    for marker in ("<think>", "<thinking>"):
        if marker in text:
            tail = text.split(marker, 1)[1]
            for end in ("</think>", "</thinking>"):
                if end in tail:
                    tail = tail.split(end, 1)[1]
            text = tail.strip()
    return text


def _extract_usage(message: AIMessage) -> Dict[str, int]:
    meta = getattr(message, "response_metadata", {}) or {}
    usage = meta.get("token_usage") or meta.get("usage") or {}
    return {
        "prompt_tokens": int(usage.get("prompt_tokens", 0) or 0),
        "completion_tokens": int(usage.get("completion_tokens", 0) or 0),
    }


_DEFAULT_CLIENT: Optional[LLMClient] = None


def get_llm() -> LLMClient:
    global _DEFAULT_CLIENT
    if _DEFAULT_CLIENT is None:
        _DEFAULT_CLIENT = LLMClient()
    return _DEFAULT_CLIENT
