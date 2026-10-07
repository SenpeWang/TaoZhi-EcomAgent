"""SSE 流式响应封装（PRD F5）。"""

from __future__ import annotations

import json
from typing import Any, AsyncIterator

from fastapi.responses import StreamingResponse


async def _event_generator(source: AsyncIterator[Any]):
    async for event in source:
        if not isinstance(event, dict):
            event = {"data": event}
        if event.get("event_id"):
            yield f"id: {event['event_id']}\n"
        yield f"data: {json.dumps(event, ensure_ascii=False, default=str)}\n\n"
    yield "data: [DONE]\n\n"


def sse_response(source: AsyncIterator[Any]) -> StreamingResponse:
    return StreamingResponse(
        _event_generator(source),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
