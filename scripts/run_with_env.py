"""先载入本地 .env 再执行命令，让宿主侧评测拿到工具端点与模型配置。

评测运行器直接读取进程环境中的工具端点，而 `ECOM_ENV_FILE` 只负责企业运行时配置；
本入口把 `.env` 注入子进程环境，使本地与容器内的取值一致。
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parents[1]


def merged_environment() -> dict[str, str]:
    """合并当前进程环境与本地 `.env`，已存在的进程变量优先。

    Returns:
        传给子进程的环境变量字典。
    """
    env = dict(os.environ)
    local = ROOT / ".env"
    if local.is_file():
        for key, value in dotenv_values(local).items():
            if value is not None:
                env.setdefault(key, value)
        env.setdefault("ECOM_ENV_FILE", str(local))
    env.setdefault("PYTHONPATH", str(ROOT / "src"))
    return env


def main(argv: list[str]) -> int:
    """以合并后的环境执行给定命令。

    Args:
        argv: 目标命令及其参数。

    Returns:
        子进程退出码。
    """
    if not argv:
        print("用法：python3 scripts/run_with_env.py <命令> [参数...]")
        return 2
    try:
        return subprocess.run(argv, cwd=ROOT, env=merged_environment(), check=False).returncode
    except FileNotFoundError:
        print(f"找不到命令：{argv[0]}")
        return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))