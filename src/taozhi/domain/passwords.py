"""口令策略校验与基于 scrypt 的抗碰撞口令哈希实现。"""
from __future__ import annotations

import hashlib
import hmac
import secrets
import threading

from taozhi.domain.errors import Denied

_SLOTS = threading.BoundedSemaphore(4)

WEAK_PASSWORDS = ("123456", "123456789012", "password123456", "admin12345678")


def hash_password(value: str, mode: str, allow_weak: bool = False) -> str:
    """生成口令哈希；allow_weak 仅用于内网快捷体验默认口令。

    Args:
        value: 明文口令。
        mode: 运行模式（test 或 production）。
        allow_weak: 是否放行内网默认体验口令。

    Returns:
        形如 ``scrypt$16384$8$5$<salt_hex>$<hash_hex>`` 的存储摘要。
    """
    minimum = 6 if (mode == "test" or allow_weak) else 12
    if not isinstance(value, str) or not minimum <= len(value) <= 128:
        raise Denied("测试环境密码须至少 6 位；生产环境密码须至少 12 位", "INVALID_PASSWORD", 400)
    if mode == "production" and not allow_weak and value in WEAK_PASSWORDS:
        raise Denied("生产环境禁止使用常见弱口令", "INVALID_PASSWORD", 400)
    salt = secrets.token_bytes(16)
    with _SLOTS:
        hash_token = hashlib.scrypt(
            value.encode(),
            salt=salt,
            n=16384,
            r=8,
            p=5,
            dklen=32,
            maxmem=64 * 1024 * 1024,
        ).hex()
    return f"scrypt$16384$8$5${salt.hex()}${hash_token}"


def verify_password(value: str, stored: str) -> bool:
    """恒定时间校验明文口令与已存储 scrypt 摘要是否匹配。"""
    try:
        parts = stored.split("$")
        if len(parts) == 3:
            scheme, salt, expected = parts
            p = 1
        elif len(parts) == 6:
            scheme, n, r, p_str, salt, expected = parts
            if (n, r, p_str) != ("16384", "8", "5"):
                return False
            p = 5
        else:
            return False
        if scheme != "scrypt" or len(salt) != 32 or len(expected) != 64 or len(value) > 128:
            return False
        with _SLOTS:
            actual = hashlib.scrypt(
                value.encode(),
                salt=bytes.fromhex(salt),
                n=16384,
                r=8,
                p=p,
                dklen=32,
                maxmem=64 * 1024 * 1024,
            ).hex()
        return hmac.compare_digest(actual, expected)
    except (ValueError, TypeError):
        return False
