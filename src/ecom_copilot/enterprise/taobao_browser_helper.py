"""淘宝浏览器搜索执行器：复用扫码沉淀的持久化登录设备档案抓取实时商品。

由 browser_search.py 以独立子进程调用（scraper-venv 的 python，自带
playwright+chromium）。stdout 只输出一行 JSON 结果，诊断信息走 stderr。

为何必须 headful + persistent profile（2026-10-17 实测）：
- 纯 HTTP 直签 MTOP → RGV587 风控拦截；
- headless 复用 profile → 浏览器指纹与登录时不符，被打回登录墙；
- headful + profile → 正常出搜索结果（价格/销量/店铺）；
- 同设备短时间高频搜索会触发频控（表现为搜索页要求重新登录），
  属临时状态，冷却后自愈——每次调研每平台只搜一次，失败如实标注。
"""
import argparse
import asyncio
import fcntl
import json
import os
import sys
import time
from urllib.parse import quote

TAOBAO_SEARCH = "https://main.m.taobao.com/search/index.html?q={}"
LOGIN_COOKIES = {"lgc", "t", "uc3"}  # 扫码+手机确认完成后才有的登录 cookie


def _log(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)


def _pick(d: dict, *keys, default=""):
    for k in keys:
        v = d.get(k)
        if v not in (None, ""):
            return v
    return default


def _normalize(arr: list, limit: int) -> list:
    """appsearch itemsArray → 归一化商品项（字段名防御式轮询）。"""
    out = []
    for it in arr[:limit]:
        if not isinstance(it, dict):
            continue
        title = str(_pick(it, "title", "titleStr", default=""))[:200]
        if not title:
            continue
        price = _pick(it, "priceShow", "price", "nakedPrice", default=None)
        try:
            price = round(float(str(price).replace("¥", "").strip()), 2) if price is not None else None
        except (TypeError, ValueError):
            price = None
        item_id = str(_pick(it, "itemId", "nid", default=""))
        url = (
            "https://item.taobao.com/item.htm?id=" + item_id
            if item_id else str(_pick(it, "itemUrl", "url", default=""))[:300]
        )
        out.append(
            {
                "title": title,
                "price": price,
                "sales": str(_pick(it, "sold", "sales", "quantitySold", "realSales", default=""))[:40],
                "shop": str(_pick(it, "shopName", "shopNick", "shopTitle", default=""))[:80],
                "url": url,
                "pic": str(_pick(it, "picUrl", "pic_path", "img", default=""))[:300],
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
                        "note": "登录态缺失或已过期，请重新扫码（运维脚本 taobao_profile_setup.py）"}
            page = ctx.pages[0] if ctx.pages else await ctx.new_page()
            captured: list = []

            async def on_response(resp):
                if "appsearch" not in resp.url:
                    return
                try:
                    captured.append(await resp.json())
                except Exception:
                    pass

            page.on("response", lambda r: asyncio.ensure_future(on_response(r)))
            await page.goto(TAOBAO_SEARCH.format(quote(keyword)),
                            wait_until="domcontentloaded")
            deadline = time.monotonic() + wait_s
            while time.monotonic() < deadline:
                await asyncio.sleep(2)
                for body in captured:
                    ret = "::".join(map(str, body.get("ret", [])))
                    if "SUCCESS" in ret:
                        items = _normalize((body.get("data") or {}).get("itemsArray") or [], limit)
                        if items:
                            return {"state": "ok", "items": items, "note": ""}
                if "login." in page.url:
                    return {"state": "rate_limited", "items": [],
                            "note": "淘宝风控临时要求重新登录（疑似搜索频控），稍后自愈"}
            return {"state": "empty", "items": [],
                    "note": "等待窗口内未拦截到搜索响应（风控或页面改版）"}
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
            result = _search_sync(args)
        except Exception as exc:  # noqa: BLE001 任何异常都输出结构化结果而非崩溃
            result = {"state": "error", "items": [],
                      "note": f"{type(exc).__name__}: {exc}"[:200]}
        print(json.dumps(result, ensure_ascii=False))


def _search_sync(args) -> dict:
    return asyncio.run(_search(args.profile, args.keyword, args.limit, args.wait))


if __name__ == "__main__":
    main()
