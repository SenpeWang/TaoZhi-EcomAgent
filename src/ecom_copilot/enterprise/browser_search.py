"""淘宝浏览器搜索底座：复用扫码登录的持久化设备档案抓取实时商品。

链路：research.py → 本模块（subprocess 隔离执行）→
~/.local/share/ecom-agent/scraper-venv/bin/python 运行同目录
taobao_browser_helper.py（playwright headful + persistent profile，
拦截页面自身 mtop 响应取数）。

- 扫码一次性：登录态/设备指纹沉淀在 persistent profile，此后无需再登录；
- 登录态过期 → state="need_login"（重新扫码即可恢复）；
- profile 进程独占 → helper 内 flock 串行化，占用中返回 state="busy"；
- 搜索频控 → state="rate_limited"（临时状态，冷却自愈）。
"""
from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from typing import Any, Dict

_PROFILE = Path.home() / ".local/share/ecom-agent/taobao-profile"
_SCRAPER_PY = Path.home() / ".local/share/ecom-agent/scraper-venv/bin/python"
_HELPER = Path(__file__).resolve().parent / "taobao_browser_helper.py"


def browser_available() -> bool:
    """浏览器搜索三要素就绪：scraper-venv、执行脚本、已扫码的设备档案。"""
    return _SCRAPER_PY.exists() and _HELPER.exists() and _PROFILE.exists()


def taobao_browser_search(keyword: str, limit: int = 20, timeout_s: float = 75.0) -> Dict[str, Any]:
    """执行一次淘宝浏览器搜索。

    返回 {"state": ok|need_login|rate_limited|busy|empty|error|not_available,
          "items": [...], "note": str}；除 ok 外均如实上抛，不做模拟数据。
    """
    if not browser_available():
        return {"state": "not_available", "items": [],
                "note": "浏览器搜索组件未就绪（未扫码或 scraper-venv 缺失）"}
    env = {**os.environ, "DISPLAY": os.environ.get("DISPLAY") or ":0"}
    try:
        proc = subprocess.run(
            [str(_SCRAPER_PY), str(_HELPER),
             "--keyword", keyword, "--limit", str(limit), "--profile", str(_PROFILE)],
            capture_output=True, text=True, timeout=timeout_s, env=env,
        )
    except subprocess.TimeoutExpired:
        return {"state": "rate_limited", "items": [],
                "note": "浏览器搜索超时（疑似风控拖慢），请稍后重试"}
    for line in reversed((proc.stdout or "").strip().splitlines()):
        line = line.strip()
        if line.startswith("{"):
            try:
                return json.loads(line)
            except json.JSONDecodeError:
                break
    return {"state": "error", "items": [],
            "note": f"浏览器搜索无结构化输出：{(proc.stderr or '')[-160:]}"}
