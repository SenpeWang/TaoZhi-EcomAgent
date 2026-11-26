"""公网 URL 与 SSRF 安全边界校验纯领域规则（零网络 I/O 发包，仅做语法与解析地址判定）。"""
from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urlsplit

from taozhi.domain.errors import Denied

_ALLOWED_SCHEMES = frozenset({"http", "https"})
_BLOCKED_HOST_SUFFIXES = (".localhost", ".local", ".internal", ".arpa")


def _is_public_ip(ip_str: str) -> bool:
    """判断单个 IPv4/IPv6 地址是否属于严格公网可路由段。"""
    ip_obj = ipaddress.ip_address(ip_str.split("%", 1)[0])
    return (
        ip_obj.is_global
        and not ip_obj.is_private
        and not ip_obj.is_loopback
        and not ip_obj.is_link_local
        and not ip_obj.is_multicast
        and not ip_obj.is_reserved
        and not ip_obj.is_unspecified
    )


def resolve_public_http_target(url: str) -> tuple[str, str]:
    """校验目标 URL 并返回标准 URL 与用于固定连接的已校验 IP。

    仅放行 ``http://`` 与 ``https://`` 协议，并拦截回环地址、RFC1918 私网段、
    云主机元数据段（如 ``169.254.169.254``）以及解析到非公网 IP 的域名。

    Args:
        url: 待校验的目标网页链接。

    Returns:
        ``(标准 URL, 已校验 IP)`` 二元组；调用方应把 TCP 连接固定到该 IP，
        但请求 URL 仍保留原主机名以维持 TLS SNI 与证书校验。

    Raises:
        Denied: 当协议非法、主机名为内网标识或解析 IP 非全局公网地址时抛出 ``SSRF_BLOCKED``。
    """
    raw = str(url or "").strip()
    parsed = urlsplit(raw)
    if parsed.scheme.lower() not in _ALLOWED_SCHEMES or not parsed.hostname:
        raise Denied("仅允许访问公网 HTTP/HTTPS 网页链接", "SSRF_BLOCKED", 403)

    host = parsed.hostname.rstrip(".").lower()
    if host == "localhost" or any(host.endswith(suffix) for suffix in _BLOCKED_HOST_SUFFIXES):
        raise Denied("目标链接被安全策略拦截：禁止访问本地或内网域名", "SSRF_BLOCKED", 403)

    try:
        ip_literal = ipaddress.ip_address(host.split("%", 1)[0])
    except ValueError:
        ip_literal = None

    if ip_literal is not None:
        if not _is_public_ip(str(ip_literal)):
            raise Denied("目标链接被安全策略拦截：禁止访问内网或保留 IP", "SSRF_BLOCKED", 403)
        return raw, str(ip_literal)

    port = parsed.port or (443 if parsed.scheme.lower() == "https" else 80)
    try:
        infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except OSError as exc:
        raise Denied(f"目标域名解析失败：{exc}", "SSRF_DNS_FAILED", 400) from exc

    resolved_ips = sorted({item[4][0] for item in infos if item and item[4]})
    if not resolved_ips or any(not _is_public_ip(ip_s) for ip_s in resolved_ips):
        raise Denied("目标链接被安全策略拦截：域名解析包含非公网地址", "SSRF_BLOCKED", 403)
    return raw, resolved_ips[0]


def validate_public_http_url(url: str) -> str:
    """校验目标 URL 并返回标准 URL；需要固定连接时使用 :func:`resolve_public_http_target`。"""
    return resolve_public_http_target(url)[0]
