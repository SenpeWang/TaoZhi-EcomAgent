"""从供应商流中提取可展示的正文，并在一次端点尝试内合并细碎增量。"""
from __future__ import annotations

import re
import time
from collections.abc import Callable, Iterable, Iterator
from itertools import chain

from langchain_core.messages import AIMessage, BaseMessage

from .usage import extract_usage

_THINK_TAG = re.compile(r"</?think(?:ing)?>", re.IGNORECASE)
_TAG_PREFIXES = {tag[:i] for tag in ("<think>", "</think>", "<thinking>", "</thinking>") for i in range(1, len(tag))}


class OutputDeliveryError(RuntimeError):
    """正文事件未能持久交付，保留原始异常给任务执行方。"""


def _public_content(chunks: Iterable[str]) -> Iterator[str]:
    """忽略可能跨分片的思考区段，仅输出已确定可展示的字符。"""
    pending = ""
    depth = 0
    for chunk in chunks:
        text = pending + chunk
        pending = ""
        last_open = text.rfind("<")
        if last_open >= 0 and text[last_open:].lower() in _TAG_PREFIXES:
            text, pending = text[:last_open], text[last_open:]
        offset = 0
        for match in _THINK_TAG.finditer(text):
            if depth == 0 and offset < match.start():
                yield text[offset:match.start()]
            depth = max(0, depth - 1) if match.group().startswith("</") else depth + 1
            offset = match.end()
        if depth == 0 and offset < len(text):
            yield text[offset:]
    if depth == 0 and pending:
        yield pending


def _unwrap_markdown(parts: Iterator[str]) -> Iterator[str]:
    """移除包裹整篇报告的代码围栏，同时保留正文中的 Markdown 代码块。"""
    opening = ""
    for part in parts:
        opening = (opening + part).lstrip()
        if "```".startswith(opening):
            continue
        if not opening.startswith("```"):
            yield opening
            yield from parts
            return
        if "\n" in opening:
            break
    else:
        if opening and not opening.startswith("```"):
            yield opening
        return
    candidate: list[str] = []
    line_start = True
    for part in chain((opening.partition("\n")[2],), parts):
        visible = []
        for char in part:
            if line_start and char in " `\t\r\n":
                candidate.append(char)
                continue
            visible.extend(candidate)
            candidate.clear()
            visible.append(char)
            line_start = char == "\n"
        if visible:
            yield "".join(visible)
    tail = "".join(candidate)
    if tail and tail.strip() != "```":
        yield tail


def stream_answer(
    messages: Iterable[BaseMessage], on_delta: Callable[[str], None], on_reset: Callable[[], None],
) -> AIMessage:
    """转发单次模型尝试的可见文本和真实用量，在中断时撤回该次草稿。"""
    usage = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}

    def text_chunks() -> Iterator[str]:
        for message in messages:
            for key, count in extract_usage(message).items():
                usage[key] += count
            if isinstance(message.content, str):
                yield message.content
            else:
                for block in message.content:
                    if isinstance(block, str):
                        yield block
                    elif isinstance(block, dict) and block.get("type") == "text" and isinstance(block.get("text"), str):
                        yield block["text"]

    parts: list[str] = []
    pending = ""
    last_flush: float | None = None
    trailing_space = ""

    def publish(delta: str) -> None:
        try:
            on_delta(delta)
        except Exception as exc:
            raise OutputDeliveryError("正文增量交付失败") from exc

    try:
        for piece in _unwrap_markdown(_public_content(text_chunks())):
            piece = trailing_space + piece
            if not parts:
                piece = piece.lstrip()
            fragment = piece.rstrip()
            trailing_space = piece[len(fragment):]
            if not fragment:
                continue
            parts.append(fragment)
            pending += fragment
            now = time.monotonic()
            if last_flush is None or len(pending) >= 512 or now - last_flush >= 0.2:
                publish(pending)
                pending = ""
                last_flush = now
        if pending:
            publish(pending)
    except OutputDeliveryError:
        raise
    except Exception:
        if last_flush is not None:
            try:
                on_reset()
            except Exception as exc:
                raise OutputDeliveryError("撤回模型草稿失败") from exc
        raise
    return AIMessage(content="".join(parts), response_metadata={"token_usage": usage})
