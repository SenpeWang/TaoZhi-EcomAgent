"""平台调研多智能体：关键词 → 多平台子智能体并行抓取 → 汇总调研报告。

平台数据源通过环境变量接入（未配置端点的平台如实标记"未接入"，
本模块不产出任何模拟/合成数据）：
  ECOM_RESEARCH_PDD_URL / ECOM_RESEARCH_TAOBAO_URL / ECOM_RESEARCH_JD_URL
  （可选配 ECOM_RESEARCH_<平台大写>_KEY，将以 X-API-Key 请求头发送）
协议：POST JSON {"query": str, "limit": int} → {"items": [
  {"title": str, "price": number|str, "sales": str, "shop": str, "url": str}]}
接入方式：自建平台抓取服务或第三方电商数据 API，返回该 JSON 即可。
"""

from __future__ import annotations

import json
import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Callable, Dict, List, Tuple

import httpx

from ..llm.client import LLMClient, ModelTier

BUILTIN_PLATFORMS: Tuple[Tuple[str, str], ...] = (("pdd", "拼多多"), ("taobao", "淘宝"), ("jd", "京东"))

_TIMEOUT = 25.0
_LABEL = dict(BUILTIN_PLATFORMS)


def _endpoint(name: str) -> str:
    return os.environ.get(f"ECOM_RESEARCH_{name.upper()}_URL", "").strip()


def _api_key(name: str) -> str:
    return os.environ.get(f"ECOM_RESEARCH_{name.upper()}_KEY", "").strip()


def platform_status() -> List[Dict[str, Any]]:
    """内置平台与接入状态（供管理端/前端展示）。"""
    return [
        {"name": n, "label": label, "configured": bool(_endpoint(n))}
        for n, label in BUILTIN_PLATFORMS
    ]


def _fetch_platform(name: str, query: str, limit: int) -> List[Dict[str, Any]]:
    """抓取单个平台商品数据，返回归一化后的 items。"""
    headers = {"Content-Type": "application/json"}
    key = _api_key(name)
    if key:
        headers["X-API-Key"] = key
    with httpx.Client(timeout=_TIMEOUT) as client:
        r = client.post(_endpoint(name), json={"query": query, "limit": limit}, headers=headers)
        r.raise_for_status()
        items = (r.json() or {}).get("items") or []
    out: List[Dict[str, Any]] = []
    for it in items[:limit]:
        if not isinstance(it, dict):
            continue
        out.append(
            {
                "title": str(it.get("title") or "")[:200],
                "price": it.get("price"),
                "sales": str(it.get("sales") or "")[:40],
                "shop": str(it.get("shop") or "")[:80],
                "url": str(it.get("url") or "")[:300],
            }
        )
    return out


def _agent_brief(name: str, query: str, items: List[Dict[str, Any]]) -> str:
    """平台子智能体的小结：有数据做数据分析；模型不可用时降级为数据清单。"""
    if not items:
        return f"{_LABEL.get(name, name)}：未获取到商品数据"
    listing = "\n".join(
        f"{i + 1}. {d['title']} | 价格 {d['price']} | 销量 {d['sales']} | 店铺 {d['shop']}"
        for i, d in enumerate(items[:20])
    )
    try:
        return LLMClient().chat(
            f"平台：{_LABEL.get(name, name)}\n调研关键词：{query}\n"
            f"抓取到 {len(items)} 条商品数据：\n{listing}\n\n"
            "用 3-5 条要点总结该平台调研发现：价格带、热销卖点、销量分布、值得注意的竞品。"
            "只依据上面数据，不得编造数字。",
            tier=ModelTier.FAST,
            max_tokens=700,
            system="你是电商市场调研分析师，输出简洁中文要点。",
        )
    except Exception as exc:  # noqa: BLE001 分析降级，保留原始清单
        rows = "\n".join(f"- {d['title']}（{d['price']}，{d['sales']}）" for d in items[:15])
        return f"{_LABEL.get(name, name)}：分析模型暂不可用（{type(exc).__name__}），原始数据：\n{rows}"


def _plan_outline(keyword: str) -> List[str]:
    """调研大纲：LLM 生成失败时回退默认维度。"""
    try:
        raw = LLMClient().chat(
            f"电商调研关键词：{keyword}\n给出 4-6 个调研维度，JSON 数组输出，每项一句话。",
            tier=ModelTier.FAST,
            max_tokens=300,
            system="只输出 JSON 数组，不要其他文字。",
        )
        from ..llm.json_utils import extract_json

        arr = extract_json(raw)
        if isinstance(arr, list) and arr:
            return [str(x)[:60] for x in arr[:6]]
    except Exception:  # noqa: BLE001
        pass
    return ["市场概览与主要玩家", "价格带分布", "热销卖点与差异化", "用户关注点与机会点"]


def run_research(keyword: str, platforms: List[str], emit: Callable[[str, int], None]) -> Dict[str, Any]:
    """执行一次平台调研。

    emit(stage, progress) 实时上报进度；每个平台一个子智能体并行
    （抓取 → 平台小结），最后汇总为 markdown 调研报告。
    """
    started = time.perf_counter()
    picked = [p for p in platforms if p in _LABEL] or [n for n, _ in BUILTIN_PLATFORMS]

    emit("生成调研大纲", 10)
    outline = _plan_outline(keyword)

    emit(f"派出 {len(picked)} 个平台智能体并行抓取", 30)

    def agent_work(name: str):
        if not _endpoint(name):
            return name, "", [], "not_configured", "数据源端点未配置"
        try:
            items = _fetch_platform(name, keyword, 20)
        except Exception as exc:  # noqa: BLE001
            return name, "", [], "error", f"抓取失败：{type(exc).__name__}"
        return name, _agent_brief(name, keyword, items), items, "ok", ""

    platform_results: List[Dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=min(4, len(picked))) as pool:
        futures = [pool.submit(agent_work, n) for n in picked]
        for fut in as_completed(futures):
            name, brief, items, state, err = fut.result()
            platform_results.append(
                {
                    "name": name,
                    "label": _LABEL.get(name, name),
                    "state": state,
                    "state_label": {"ok": "已完成", "not_configured": "未接入", "error": "抓取失败"}.get(state, state),
                    "error": err,
                    "brief": brief,
                    "items": items[:20],
                    "items_count": len(items),
                }
            )

    emit("汇总各智能体发现，生成调研报告", 70)
    platform_results.sort(key=lambda r: list(_LABEL).index(r["name"]) if r["name"] in _LABEL else 99)
    # 线程完成顺序不定，报告需按内置平台顺序稳定输出
    data_blocks = "\n\n".join(
        f"## {r['label']}（{r['state_label']}）\n{r['brief']}" for r in platform_results
    )
    any_data = any(r["state"] == "ok" and r["items_count"] > 0 for r in platform_results)
    try:
        if any_data:
            instruction = "汇总各平台智能体的发现为一份完整的电商调研报告。"
        else:
            instruction = (
                "注意：本次所有平台数据源均未接入，没有任何实测数据。请输出："
                "1) 开头明确说明平台数据源未接入（需配置 ECOM_RESEARCH_<平台大写>_URL 环境变量，协议见代码注释）；"
                "2) 基于公开常识给出该品类的调研框架与分析建议，并明确标注这是分析框架而非实测数据，"
                "不得编造任何价格、销量数字。"
            )
        report = LLMClient().chat(
            f"调研关键词：{keyword}\n调研大纲：{'；'.join(outline)}\n\n各平台智能体的发现：\n{data_blocks}\n\n{instruction}",
            tier=ModelTier.FAST,
            max_tokens=4096,
            system="你是资深电商市场调研负责人，输出结构化 markdown 调研报告。数据必须来自给定发现，禁止编造数字。",
        )
    except Exception as exc:  # noqa: BLE001 汇总失败降级为原始发现
        report = f"# 调研报告：{keyword}\n\n汇总模型暂不可用（{type(exc).__name__}），以下为各智能体原始发现。\n\n{data_blocks}"

    emit("调研报告已生成", 100)
    return {
        "report": report,
        "outline": outline,
        "platforms": platform_results,
        "model_calls": 2 + sum(1 for r in platform_results if r["state"] == "ok"),
        "elapsed": round(time.perf_counter() - started, 1),
    }
