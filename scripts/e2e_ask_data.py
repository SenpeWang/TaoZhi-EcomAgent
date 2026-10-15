"""问数（ChatBI）端到端验收：走真实 API → worker → LangGraph → 语义层。

用法：ECOM_ENV_FILE=.env.production .venv/bin/python scripts/e2e_ask_data.py
"""
import json
import sys
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

BASE = "http://127.0.0.1:18080"
PASSWORD = "ChatBI-E2E-2026"

CASES = [
    ("chatbi_staff", "近30天退货率最高的渠道是哪个？"),
    ("chatbi_staff", "天猫近30天卖了多少？"),
    ("chatbi_boss", "近30天各渠道各品类的毛利情况"),
    ("chatbi_staff", "毛利是多少？"),
    ("chatbi_staff", "知识库现在有多少文档？"),
]


def wait_job(client, task_id, timeout=120):
    deadline = time.time() + timeout
    while time.time() < deadline:
        r = client.get(f"/api/tasks/{task_id}").json()
        if r.get("state") in ("completed", "review", "failed", "cancelled"):
            return r
        time.sleep(2)
    return {"state": "timeout"}


def main() -> int:
    ok = 0
    for user, question in CASES:
        with httpx.Client(base_url=BASE, timeout=30) as s:
            r = s.post("/api/auth/login", json={"username": user, "password": PASSWORD})
            if r.status_code != 200:
                print(f"[{user}] 登录失败：{r.status_code} {r.text[:120]}")
                continue
            csrf = r.json().get("csrf_token", "")
            r = s.post("/api/tasks", json={"question": question, "input_level": 1},
                       headers={"X-CSRF-Token": csrf})
            if r.status_code != 202:
                print(f"[{user}] 提交失败：{r.status_code} {r.text[:200]}")
                continue
            task_id = r.json()["task_id"]
            done = wait_job(s, task_id)
            result = done.get("result") or {}
            answer = (result.get("answer") or done.get("error") or "")[:420]
            state = done.get("state")
            print(f"━" * 72)
            print(f"[{user}] {question}")
            print(f"  state={state}  stage={done.get('stage','')}")
            print("  " + answer.replace("\n", "\n  "))
            if state in ("completed", "review"):
                ok += 1
    print("━" * 72)
    print(f"完成 {ok}/{len(CASES)} 个用例成功返回")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
