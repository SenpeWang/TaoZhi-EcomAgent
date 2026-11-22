#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把已交付资料通过 /api/v2 正式入库到演示环境（tenant_demo）。

- 产品资料 md：公司共享、员工级，解析完成后由 boss 批准发布。
- 制度草案 md：按建议密级上传，停在 review 状态，不做发布审批（需企业确认）。
- 幂等：标题已存在的资料跳过。
- 不修改源码，不写数据库以外的项目文件；报告写 data/runtime/。
"""
import json
import time
from pathlib import Path

import httpx

BASE = "http://127.0.0.1:18501"
ROOT = Path("/home/quanyj/wsp-workspace/00_base/multi-agent")
DATA = ROOT / "data"
PRODUCT_DIRS = ["apple", "huawei", "thirdparty", "other_brands"]
REPORT = DATA / "runtime/ingest_report_20261008.json"


def title_of(path: Path) -> str:
    for line in path.read_text(encoding="utf-8").splitlines():
        s = line.strip()
        if s.startswith("#"):
            return s.lstrip("#").strip()[:80]
    return path.stem


def login(client):
    r = client.post("/api/v2/auth/login", json={"username": "boss", "password": "123456"})
    r.raise_for_status()
    j = r.json()
    client.headers["x-csrf-token"] = j["csrf_token"]
    return j


def queued_running(client):
    r = client.get("/api/v2/system/status")
    return sum(x["count"] for x in r.json()["counts"] if x["state"] in ("queued", "running"))


def existing_titles(client):
    r = client.get("/api/v2/documents")
    r.raise_for_status()
    return {d["title"]: d["state"] for d in r.json()["items"]}


def upload(client, titles, path, level):
    title = title_of(path)
    if title in titles:
        return {"file": str(path), "title": title, "skipped": True, "state": titles[title]}
    r = client.post("/api/v2/documents/upload",
                    files={"file": (path.name, path.read_bytes(), "text/plain")},
                    data={"title": title, "node_id": ROOT_NODE, "scope": "company", "level": str(level)})
    ok = r.status_code == 201
    if not ok:
        return {"file": str(path), "title": title, "ok": False, "status": r.status_code, "resp": r.json()}
    return {"file": str(path), "title": title, "ok": True, "doc_id": r.json()["id"], "task_id": r.json()["task_id"]}


def main():
    global ROOT_NODE
    client = httpx.Client(base_url=BASE, trust_env=False, timeout=60)
    login(client)
    nodes = client.get("/api/v2/organizations").json()["items"]
    ROOT_NODE = next(n["id"] for n in nodes if n["kind"] == "company")

    # 制度草案建议密级映射
    grade = {}
    idx = DATA / "corpus/policies/source_index.jsonl"
    for line in idx.read_text(encoding="utf-8").splitlines():
        if line.strip():
            rec = json.loads(line)
            g = rec.get("suggested_grade") or ""
            grade[rec["file"]] = 3 if "老板" in g else (2 if any(k in g for k in ("组长", "秘密", "管理层", "部门", "相关岗", "行政")) else 1)

    product_files = sorted(p for d in PRODUCT_DIRS for p in (DATA / f"incoming/待映射/products_official/{d}").glob("*.md"))
    policy_files = sorted((DATA / "corpus/policies").glob("*.md"))
    print(f"产品资料 {len(product_files)} 份，制度草案 {len(policy_files)} 份")

    titles = existing_titles(client)
    results, product_ids, policy_ids = [], [], []
    for i, p in enumerate(product_files):
        rec = upload(client, titles, p, 1)
        results.append(rec)
        if rec.get("doc_id"):
            product_ids.append(rec["doc_id"])
        titles[rec["title"]] = "ingesting"
        if (i + 1) % 30 == 0:
            while queued_running(client) > 10:
                time.sleep(3)
    while queued_running(client) > 10:
        time.sleep(3)
    for p in policy_files:
        rec = upload(client, titles, p, grade.get(p.name, 2))
        results.append(rec)
        if rec.get("doc_id"):
            policy_ids.append(rec["doc_id"])
        titles[rec["title"]] = "ingesting"

    # 等产品资料解析完成并进入 review，然后批准发布
    published, review = 0, 0
    for _ in range(120):
        r = client.get("/api/v2/documents").json()["items"]
        st = {d["id"]: d["state"] for d in r if d["id"] in product_ids}
        review = sum(1 for v in st.values() if v == "review")
        published = sum(1 for v in st.values() if v == "published")
        if published + review >= len(product_ids) and all(v in ("review", "published") for v in st.values()) or not product_ids:
            break
        time.sleep(5)
    approved, failed = 0, []
    for _ in range(60):
        items = client.get("/api/v2/approvals").json()["items"]
        pending = [a for a in items if a["kind"] == "publish" and a["state"] == "pending" and a["target_id"] in product_ids]
        if not pending:
            break
        for a in pending:
            r = client.post(f"/api/v2/approvals/{a['id']}/decision", json={"approve": True, "comment": "公开采集产品资料，员工级公司共享，批量发布"})
            if r.status_code == 200:
                approved += 1
            else:
                failed.append({"approval": a["id"], "resp": r.json()})
        time.sleep(2)

    r = client.get("/api/v2/documents").json()["items"]
    final = {"published": sum(1 for d in r if d["state"] == "published"),
             "review": sum(1 for d in r if d["state"] == "review"),
             "other": sum(1 for d in r if d["state"] not in ("published", "review"))}
    report = {"at": time.strftime("%Y-%m-%d %H:%M:%S"), "env": BASE,
              "product_files": len(product_files), "policy_files": len(policy_files),
              "product_published_approved": approved, "approval_failures": failed,
              "tenant_totals": final,
              "skipped": [x for x in results if x.get("skipped")],
              "errors": [x for x in results if x.get("ok") is False],
              "details": results}
    REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print("入库完成:", json.dumps({k: report[k] for k in ("product_files", "policy_files", "product_published_approved", "tenant_totals", "errors")}, ensure_ascii=False))
    print("报告:", REPORT)


if __name__ == "__main__":
    main()
