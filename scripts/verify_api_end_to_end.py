"""生产 API 端到端回归：Cookie 登录 -> 提问 -> 轮询任务结果，验证 pgvector 迁移后的问答链路。"""
import http.cookiejar
import json
import time
import urllib.request

BASE = "http://127.0.0.1:18080"
STAFF_PW = "6Pi6oX1SE7KEEEFC"

jar = http.cookiejar.CookieJar()
opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))


def post(path, body, csrf=None):
    req = urllib.request.Request(BASE + path, data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json", **({"X-CSRF-Token": csrf} if csrf else {})})
    return json.loads(opener.open(req, timeout=30).read())


def get(path):
    return json.loads(opener.open(BASE + path, timeout=30).read())


# 1. 登录（Cookie 会话）
login = post("/api/auth/login", {"username": "staff", "password": STAFF_PW})
csrf = login["csrf_token"]
print("登录:", login["message"], "| 用户:", login["user"]["display_name"], "| Cookie 数:", len(jar))

# 2. 健康
print("health:", get("/api/health"))

# 3. 提问（检索链路：ES BM25 + PG ANN(HNSW) + RRF；写操作需带 CSRF 头）
task = post("/api/tasks", {"question": "MC-500 膜切机的切幅是多少？支持哪些膜材？"}, csrf)
tid = task["id"]
print("任务已创建:", tid, "| 初始状态:", task.get("state"))

# 4. 轮询到终态
t = task
for _ in range(60):
    t = get(f"/api/tasks/{tid}")
    if t.get("state") in ("completed", "failed", "review"):
        break
    time.sleep(2)
print("终态:", t.get("state"), "| 阶段:", t.get("stage"))
result = t.get("result") or {}
answer = result.get("answer") or ""
citations = result.get("citations") or []
print("usage:", result.get("usage"))
print("答案前 200 字:", answer[:200].replace("\n", " "))
print("引用数:", len(citations))
for c in citations[:5]:
    print("  -", c.get("title"), "| page:", c.get("page"))
