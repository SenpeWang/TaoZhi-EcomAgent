"""公网抓取的 DNS 固定传输：连接固定到已校验 IP，URL 仍保留主机名以维持 TLS 校验。

该传输不继承 HTTP(S)_PROXY，避免代理再次解析域名绕过固定；如部署必须经代理抓网页，
应改为受控出口而非在本层恢复代理。
"""
from __future__ import annotations

from typing import Any, Optional

import httpcore
import httpx


class _PinnedBackend(httpcore.SyncBackend):
    """把指定主机名的 TCP 连接固定到已校验 IP，其余主机保持原样。"""

    def __init__(self, host: str, ip: str) -> None:
        self._host = host.lower().rstrip(".")
        self._ip = ip

    def connect_tcp(
        self,
        host: str,
        port: int,
        timeout: Optional[float] = None,
        local_address: Optional[str] = None,
        socket_options: Any = None,
    ) -> httpcore.NetworkStream:
        """连接已固定的目标 IP，TLS SNI 与证书校验仍使用原主机名。"""
        target = self._ip if str(host).lower().rstrip(".") == self._host else host
        return super().connect_tcp(
            target,
            port,
            timeout=timeout,
            local_address=local_address,
            socket_options=socket_options,
        )


class _ResponseStream(httpx.SyncByteStream):
    """把 httpcore 的响应流适配为 httpx 同步字节流。"""

    def __init__(self, stream: Any) -> None:
        self._stream = stream

    def __iter__(self):
        for chunk in self._stream:
            yield chunk

    def close(self) -> None:
        """关闭底层响应流；底层不支持关闭时忽略。"""
        close = getattr(self._stream, "close", None)
        if close is not None:
            close()


class PinnedTransport(httpx.BaseTransport):
    """为一个主机名固定解析 IP 的 httpx 传输层；重定向需按新目标重建。"""

    def __init__(self, host: str, ip: str) -> None:
        self._pool = httpcore.ConnectionPool(network_backend=_PinnedBackend(host, ip))

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        """把 httpx 请求转交 httpcore，并包装返回流。"""
        core_request = httpcore.Request(
            method=request.method,
            url=httpcore.URL(
                scheme=request.url.scheme.encode(),
                host=request.url.host.encode(),
                port=request.url.port,
                target=request.url.raw_path,
            ),
            headers=request.headers.raw,
            content=request.stream,
            extensions=request.extensions,
        )
        core_response = self._pool.handle_request(core_request)
        return httpx.Response(
            status_code=core_response.status,
            headers=core_response.headers,
            stream=_ResponseStream(core_response.stream),
            extensions=core_response.extensions,
        )

    def close(self) -> None:
        """关闭底层连接池。"""
        self._pool.close()
