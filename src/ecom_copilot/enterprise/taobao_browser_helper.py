"""淘宝浏览器搜索执行器：复用扫码沉淀的持久化登录设备档案抓取实时商品。

由 browser_search.py 以独立子进程调用（scraper-venv 的 python，自带
playwright+chromium）。stdout 只输出一行 JSON 结果，诊断信息走 stderr。

为何必须 headful + persistent profile（2026-10-17 实测）：
- 纯 HTTP 直签 MTOP → RGV587 风控拦截；
- headless 复用 profile → 浏览器指纹与登录时不符，会被打回登录墙，
  且可能触发服务端吊销该 profile 的登录会话（headless 一次都不许开）；
- headful + profile → 搜索页正常渲染商品；
- 同设备短时间高频搜索触发频控（小时级冷却），表现为数据接口返回空。

数据接口（2026-10-17 抓包确认）：移动版搜索结果不走独立的 appsearch
接口，而是 feed 流接口 mtop.relationrecommend.wirelessrecommend.recommend
（h5api.m.taobao.com，JSONP 包裹）。故本执行器拦截全部 h5/mtop 响应、
剥 JSONP 外壳后按"含 title 的商品列表"特征定位数据，不假设固定路径与字段。
"""
import argparse
import asyncio
import fcntl
import json
import os
import re
import sys
import time
from pathlib import Path
from urllib.parse import quote

TAOBAO_SEARCH = "https://main.m.taobao.com/search/index.html?q={}"
LOGIN_COOKIES = {"lgc", "t", "uc3"}  # 扫码+手机确认完成后才有的登录 cookie
SAMPLE_PATH = Path.home() / ".local/share/ecom-agent/taobao_search_sample.json"


def _log(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)


def _strip_jsonp(text: str):
    """剥掉 mtop JSONP 的 callback 外壳，返回内层 JSON 对象。"""
    m = re.match(r"^\s*[\w.]+\((.*)\)\s*;?\s*$", text, re.S)
    return json.loads(m.group(1) if m else text)


def _pick(d: dict, *keys, default=""):
    for k in keys:
        v = d.get(k)
        if v not in (None, ""):
            return v
    return default


def _hunt_items(obj):
    """深度优先找第一个"元素为含 title 字段的 dict"的列表。"""
    if isinstance(obj, list):
        if obj and isinstance(obj[0], dict) and "title" in obj[0]:
            return obj
        for v in obj:
            r = _hunt_items(v)
            if r:
                return r
    elif isinstance(obj, dict):
        for v in obj.values():
            r = _hunt_items(v)
            if r:
                return r
    return None


def _normalize(arr: list, limit: int) -> list:
    """商品列表 → 归一化条目（字段名防御式轮询，接口改字段时自动兼容）。"""
    out = []
    for it in arr[:limit]:
        if not isinstance(it, dict):
            continue
        title = str(_pick(it, "title", "titleStr", default=""))[:200]
        if not title:
            continue
        price = _pick(it, "priceShow", "price", "nakedPrice", "priceText", default=None)
        try:
            price = round(float(str(price).replace("¥", "").strip()), 2) if price is not None else None
        except (TypeError, ValueError):
            price = None
        item_id = str(_pick(it, "itemId", "nid", "item_id", default=""))
        url = (
            "https://item.taobao.com/item.htm?id=" + item_id
            if item_id else str(_pick(it, "itemUrl", "url", "clickUrl", default=""))[:300]
        )
        out.append(
            {
                "title": title,
                "price": price,
                "sales": str(_pick(it, "sold", "sales", "quantitySold", "realSales", "sellCount", default=""))[:40],
                "shop": str(_pick(it, "shopName", "shopNick", "shopTitle", "shopNameForDisplay", default=""))[:80],
                "url": url,
                "pic": str(_pick(it, "picUrl", "pic_path", "img", "image", default=""))[:300],
            }
        )
    return out


async def _search(profile: str, keyword: str, limit: int, wait_s: float) -> dict:
    from playwright.async_api import async_playwright

    async with async_playwright() as pw:
        ctx = await pw.chromium.launch_persistent_context(
            profile, headless=False, locale="zh-CN",
            viewport={"width": 420, "height": 760},
        )
        try:
            names = {c["name"] for c in await ctx.cookies() if "taobao" in c.get("domain", "")}
            if not LOGIN_COOKIES <= names:
                return {"state": "need_login", "items": [],
                        "note": "登录态缺失或已过期，请重新扫码（运维脚本 taobao_setup_v2.py）"}
            page = ctx.pages[0] if ctx.pages else await ctx.new_page()
            candidates: list = []   # (api, body_text)
            recommend_seen = False

            async def on_response(resp):
                nonlocal recommend_seen
                u = resp.url
                if "h5api.m.taobao.com/h5/mtop" not in u:
                    return
                if "wirelessrecommend.recommend" in u:
                    recommend_seen = True
                try:
                    text = await resp.text()
                except Exception:
                    return
                if '"title"' in text and len(text) > 1000:
                    candidates.append((u.split("/h5/mtop")[1].split("/")[0], text))

            page.on("response", lambda r: asyncio.ensure_future(on_response(r)))
            await page.goto(TAOBAO_SEARCH.format(quote(keyword)),
                            wait_until="domcontentloaded")
            deadline = time.monotonic() + wait_s
            while time.monotonic() < deadline:
                await asyncio.sleep(2)
                for api, text in candidates:
                    try:
                        data = _strip_jsonp(text)
                    except (json.JSONDecodeError, ValueError):
                        continue
                    arr = _hunt_items(data)
                    items = _normalize(arr or [], limit)
                    if items:
                        try:  # 留档原始结构，供接口字段改版时诊断
                            SAMPLE_PATH.write_text(
                                json.dumps({"api": api, "sample": arr[:3]}, ensure_ascii=False, indent=1))
                        except OSError:
                            pass
                        return {"state": "ok", "items": items, "note": ""}
            if recommend_seen:
                return {"state": "rate_limited", "items": [],
                        "note": "淘宝搜索频控中（数据接口返回空，小时级自愈），请稍后再试"}
            return {"state": "empty", "items": [],
                    "note": "等待窗口内数据接口未响应（风控或页面改版）"}
        finally:
            await ctx.close()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--keyword", required=True)
    ap.add_argument("--limit", type=int, default=20)
    ap.add_argument("--profile", required=True)
    ap.add_argument("--wait", type=float, default=40.0)
    args = ap.parse_args()

    # persistent profile 同一时间只能被一个进程打开 → 文件锁串行化
    lock_path = args.profile.rstrip("/") + ".lock"
    with open(lock_path, "w") as lf:
        try:
            fcntl.flock(lf, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            print(json.dumps({"state": "busy", "items": [],
                              "note": "浏览器设备档案正被其他会话占用"}, ensure_ascii=False))
            return
        os.environ.setdefault("DISPLAY", ":0")  # WSLg X0；无图形栈时启动异常走下方兜底
        try:
            result = asyncio.run(_search(args.profile, args.keyword, args.limit, args.wait))
        except Exception as exc:  # noqa: BLE001 任何异常都输出结构化结果而非崩溃
            result = {"state": "error", "items": [],
                      "note": f"{type(exc).__name__}: {exc}"[:200]}
        print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
