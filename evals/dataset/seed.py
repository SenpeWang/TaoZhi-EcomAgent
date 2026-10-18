#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""评测夹具灌库：把 spec 世界的组织/身份/文档/授权幂等写入 PG。

这是"测试集独立"的关键一环：没有它，权限类用例引用的 SYN_* 夹具只存在于
某台机器的数据库里，换机即失效，且夹具缺失时"文档不存在→拒答→判过"会让
权限测试空转。本脚本 + spec/ 让夹具可重建、可校验、可清除。

用法（项目根目录，生产口径）：
  PYTHONPATH=src ECOM_ENV_FILE=.env.production .venv/bin/python -m evals.dataset.seed
  PYTHONPATH=src ECOM_ENV_FILE=.env.production .venv/bin/python -m evals.dataset.seed --verify
  PYTHONPATH=src ECOM_ENV_FILE=.env.production .venv/bin/python -m evals.dataset.seed --clean

设计约束：
- 幂等：重复执行结果一致（文档按 content_hash 判断是否需要重建切片）；
- 口令统一用 EVAL_PASSWORD（≥12 位，过生产弱口令校验）；run_evals 登录同一常量；
- 文档直接写 published 状态，绕过发布审批流——这是测试夹具，不是业务资料；
- --verify 用 can_read 实测权限语义，而不是只看字段：字段对但 grade 算错的白检。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

import yaml  # noqa: E402

from ecom_copilot.enterprise.config import load_config  # noqa: E402
from ecom_copilot.enterprise.db import database_connection, fetch_all, fetch_one  # noqa: E402
from ecom_copilot.enterprise.ingest import split  # noqa: E402
from ecom_copilot.enterprise.passwords import hash_password, verify_password  # noqa: E402
from ecom_copilot.enterprise.policy import can_read, load_principal  # noqa: E402

SPEC = Path(__file__).resolve().parent / "spec"
GEN = ROOT / "evals/datasets/generated/latest"
EVAL_PASSWORD = "Syn@Eval2026x"   # 评测环境专用口令：≥12 位且非弱口令，过生产校验
SYN_PREFIX = "SYN_"               # 夹具/语料/身份统一前缀，--clean 按它清除


def load_spec():
    org = yaml.safe_load((SPEC / "org.yaml").read_text(encoding="utf-8"))
    fixtures = yaml.safe_load((SPEC / "fixtures.yaml").read_text(encoding="utf-8"))
    return org, fixtures


def load_corpus_docs():
    path = GEN / "corpus_docs.jsonl"
    if not path.exists():
        print(f"缺少语料清单 {path}，先运行 python -m evals.dataset.generate", file=sys.stderr)
        raise SystemExit(2)
    return [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]


def resolve_tenant(conn):
    row = fetch_one(conn, "SELECT * FROM tenants WHERE enabled ORDER BY created_at LIMIT 1")
    if not row:
        print("没有可用租户", file=sys.stderr)
        raise SystemExit(2)
    return row


def upsert(conn, table, keys, values, conflict_cols):
    """按 conflict_cols 判存：存在则更新 values，否则插入。"""
    cols = list(keys) + [c for c in values if c not in keys]
    placeholders = ",".join(["%s"] * len(cols))
    updates = ",".join(f"{c}=EXCLUDED.{c}" for c in values)
    conn.execute(
        f"INSERT INTO {table}({','.join(cols)}) VALUES({placeholders}) "
        f"ON CONFLICT({','.join(conflict_cols)}) DO UPDATE SET {updates}",
        tuple(keys[c] if c in keys else values[c] for c in cols))


def seed_nodes(conn, tenant_id, org):
    created = 0
    for n in org["nodes"]:
        parent = n.get("parent_by_name")
        if parent == "COMPANY_ROOT":
            parent_id = fetch_one(conn, "SELECT id FROM org_nodes WHERE tenant_id=%s AND kind='company'", (tenant_id,))["id"]
        else:
            parent_id = fetch_one(conn, "SELECT id FROM org_nodes WHERE tenant_id=%s AND name=%s", (tenant_id, parent))["id"]
        exists = fetch_one(conn, "SELECT id FROM org_nodes WHERE tenant_id=%s AND id=%s", (tenant_id, n["id"]))
        if not exists:
            created += 1
        upsert(conn, "org_nodes",
               dict(id=n["id"], tenant_id=tenant_id),
               dict(parent_id=parent_id, name=n["name"], kind=n["kind"], active=True),
               ["tenant_id", "id"])
    return created


def resolve_node_id(conn, tenant_id, node_ref):
    """解析身份节点引用：COMPANY_ROOT → 公司根节点；其余为规格内字面节点 id。

    org.yaml 的 identities[].node 允许 COMPANY_ROOT 占位（根节点 id 由部署
    生成，写死 UUID 会让规格换机失效），在插入 memberships 前统一解析。
    """
    if node_ref == "COMPANY_ROOT":
        row = fetch_one(conn, "SELECT id FROM org_nodes WHERE tenant_id=%s AND kind='company'", (tenant_id,))
        if not row:
            print("找不到公司根节点（kind=company），租户组织不完整", file=sys.stderr)
            raise SystemExit(2)
        return row["id"]
    return node_ref


def seed_users(conn, tenant_id, org):
    """建档并重置口令：每次 seed 都把口令刷成 EVAL_PASSWORD，保证可复现登录。"""
    hashed = hash_password(EVAL_PASSWORD, load_config().mode)
    ids = {}
    for i in org["identities"]:
        uid = "usr_syn_" + i["username"]
        ids[i["logical_id"]] = uid
        upsert(conn, "users",
               dict(id=uid, tenant_id=tenant_id, username=i["username"]),
               dict(display_name=i["display_name"], password_hash=hashed,
                    system_admin=bool(i.get("system_admin")), active=True, must_change=False),
               ["tenant_id", "id"])
        node_id = resolve_node_id(conn, tenant_id, i["node"])
        conn.execute("DELETE FROM memberships WHERE tenant_id=%s AND user_id=%s", (tenant_id, uid))
        conn.execute("INSERT INTO memberships(tenant_id,user_id,node_id,role) VALUES(%s,%s,%s,%s)",
                     (tenant_id, uid, node_id, i["role"]))
    return ids


def seed_documents(conn, tenant_id, creator_id, docs):
    """写入文档/版本/切片/向量/ES。切片与入库口径一致：嵌入输入带标题前缀。"""
    from ecom_copilot.enterprise.embedding import available as embed_available, encode_passages, embedding_input
    from ecom_copilot.enterprise.facts import index_mentions
    if not embed_available():
        print("嵌入服务不可用，终止（不留无向量文档）", file=sys.stderr)
        raise SystemExit(2)
    from ecom_copilot.enterprise import es_index
    stats = {"inserted": 0, "updated": 0, "chunks": 0}
    for d in docs:
        body = d["body"]
        content_hash = hashlib.sha256(body.encode("utf-8")).hexdigest()
        old = fetch_one(conn, "SELECT current_version FROM documents WHERE tenant_id=%s AND id=%s", (tenant_id, d["doc_id"]))
        old_v = fetch_one(conn, "SELECT content_hash FROM document_versions WHERE document_id=%s AND version=1", (d["doc_id"],))
        if old and old_v and old_v["content_hash"] == content_hash:
            stats["updated"] += 1
            continue  # 内容未变：不重建切片（幂等且不浪费嵌入算力）
        conn.execute("DELETE FROM chunks WHERE document_id=%s", (d["doc_id"],))
        conn.execute("DELETE FROM document_versions WHERE document_id=%s", (d["doc_id"],))
        conn.execute("DELETE FROM documents WHERE tenant_id=%s AND id=%s", (tenant_id, d["doc_id"]))
        node_id = resolve_node_id(conn, tenant_id, d["node"])
        conn.execute(
            "INSERT INTO documents(id,tenant_id,node_id,creator_id,title,scope,level,ai_allowed,state,current_version) "
            "VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,1)",
            (d["doc_id"], tenant_id, node_id, creator_id, d["title"], d["scope"], d["level"], d["ai_allowed"], d["state"]))
        conn.execute(
            "INSERT INTO document_versions(tenant_id,document_id,version,title,body,content_hash,mime_type,created_by) "
            "VALUES(%s,%s,1,%s,%s,%s,'text/markdown',%s)",
            (tenant_id, d["doc_id"], d["title"], body, content_hash, creator_id))
        chunks = split([(0, body)], d["doc_id"], 1)
        vecs = encode_passages([embedding_input(d["title"], c["text"]) for c in chunks])
        for c, v in zip(chunks, vecs):
            conn.execute(
                "INSERT INTO chunks(id,tenant_id,document_id,version,ordinal,page,text,embedding) VALUES(%s,%s,%s,1,%s,%s,%s,%s)",
                (c["id"], tenant_id, d["doc_id"], c["ordinal"], c["page"], c["text"], v))
        stats["chunks"] += len(chunks)
        index_mentions(conn, tenant_id, d["doc_id"], 1, chunks)
        if es_index.available():
            es_index.delete_chunks(d["doc_id"], 1)
            rows = fetch_all(conn, "SELECT id,tenant_id,document_id,version,ordinal,page,text FROM chunks WHERE document_id=%s AND version=1", (d["doc_id"],))
            es_index.index_chunks(rows)
        stats["inserted"] += 1
    return stats


def seed_grants(conn, tenant_id, fixtures, user_ids):
    n = 0
    for g in fixtures.get("grants", []):
        uid = user_ids[g["user"]]
        conn.execute("DELETE FROM document_grants WHERE tenant_id=%s AND document_id=%s AND user_id=%s",
                     (tenant_id, g["document"], uid))
        conn.execute(
            "INSERT INTO document_grants(tenant_id,document_id,user_id,effect,can_read,expires_at,granted_by) "
            "VALUES(%s,%s,%s,%s,%s,%s,%s)",
            (tenant_id, g["document"], uid, g["effect"], g.get("can_read", True),
             g.get("expires_at"), user_ids["SYN_A_ADMIN001"]))
        n += 1
    return n


# --verify 的权限预期矩阵：用 can_read 实测，而不是"字段看起来对"。
# 每一行是 (身份, 文档, 期望可读, 说明)。改 spec 必须同步改这里。
VERIFY_MATRIX = [
    ("SYN_A_STAFF002", "SYN_DOC_A_OP01", False, "员工无授权，组长级文档不可读"),
    ("SYN_A_LDR001", "SYN_DOC_A_OP01", True, "组长 grade=2 达到 level=2"),
    ("SYN_A_STAFF001", "SYN_DOC_A_OP01", True, "有效期内单文档授权"),
    ("SYN_A_STAFF003", "SYN_DOC_A_OP01", False, "授权已过期=撤权"),
    ("SYN_A_STAFF002", "SYN_DOC_A_DENY01", False, "deny 授权优先于密级通路"),
    ("SYN_A_STAFF001", "SYN_DOC_A_DRAFT01", False, "草稿状态员工不可读"),
    ("SYN_A_BOSS001", "SYN_DOC_A_BOSS01", True, "老板可读老板级"),
    ("SYN_A_STAFF001", "SYN_DOC_A_BOSS01", False, "level=3 仅老板"),
    ("SYN_A_STAFF001", "SYN_DOC_A_PUB01", True, "公司级公开资料全员可读"),
    ("SYN_A_STAFF001", "SYN_CORPUS_LX20", True, "检索语料可读"),
]


def verify(conn, tenant_id, org, fixtures, user_ids):
    problems = []
    # 1. 文档存在性与字段
    for d in load_corpus_docs():
        row = fetch_one(conn, "SELECT scope,level,state,ai_allowed FROM documents WHERE tenant_id=%s AND id=%s", (tenant_id, d["doc_id"]))
        if not row:
            problems.append(f"文档缺失 {d['doc_id']}")
            continue
        for k in ("scope", "level", "state", "ai_allowed"):
            if row[k] != d[k]:
                problems.append(f"{d['doc_id']} 字段 {k}: 库内 {row[k]!r} ≠ spec {d[k]!r}")
        n = fetch_one(conn, "SELECT count(*) AS n FROM chunks WHERE document_id=%s AND embedding IS NOT NULL", (d["doc_id"],))["n"]
        if n == 0:
            problems.append(f"{d['doc_id']} 无带向量的切片")
    # 2. 金丝雀在正文里
    for d in load_corpus_docs():
        for canary in fixtures["canaries"]:
            if canary in d["body"]:
                row = fetch_one(conn, "SELECT body FROM document_versions WHERE document_id=%s AND version=1", (d["doc_id"],))
                if not row or canary not in row["body"]:
                    problems.append(f"{d['doc_id']} 正文缺少金丝雀 {canary}")
    # 3. 口令可登录
    for i in org["identities"]:
        row = fetch_one(conn, "SELECT password_hash FROM users WHERE tenant_id=%s AND username=%s", (tenant_id, i["username"]))
        if not row:
            problems.append(f"账号缺失 {i['username']}")
        elif not verify_password(EVAL_PASSWORD, row["password_hash"]):
            problems.append(f"账号 {i['username']} 口令与 EVAL_PASSWORD 不符")
    # 4. 权限语义实测
    for logical, doc_id, expect, why in VERIFY_MATRIX:
        p = load_principal(conn, user_ids[logical])
        d = fetch_one(conn, "SELECT * FROM documents WHERE tenant_id=%s AND id=%s", (tenant_id, doc_id))
        if not d:
            problems.append(f"权限实测缺文档 {doc_id}")
            continue
        got = can_read(conn, p, d)
        if got != expect:
            problems.append(f"权限语义不符：{logical} 读 {doc_id} 实际 {got} 期望 {expect}（{why}）")
    return problems


def clean(conn, tenant_id):
    from ecom_copilot.enterprise import es_index
    docs = [r["id"] for r in fetch_all(conn, "SELECT id FROM documents WHERE tenant_id=%s AND left(id,4)=%s", (tenant_id, "SYN_"))]
    for did in docs:
        conn.execute("DELETE FROM chunks WHERE document_id=%s", (did,))
        conn.execute("DELETE FROM document_versions WHERE document_id=%s", (did,))
        conn.execute("DELETE FROM knowledge_mentions WHERE document_id=%s", (did,))
        conn.execute("DELETE FROM document_grants WHERE tenant_id=%s AND document_id=%s", (tenant_id, did))
        conn.execute("DELETE FROM documents WHERE tenant_id=%s AND id=%s", (tenant_id, did))
        if es_index.available():
            es_index.delete_chunks(did, 1)
    users = [r["id"] for r in fetch_all(conn, "SELECT id FROM users WHERE tenant_id=%s AND left(username,4)=%s", (tenant_id, "syn_"))]
    for uid in users:
        conn.execute("DELETE FROM memberships WHERE tenant_id=%s AND user_id=%s", (tenant_id, uid))
        conn.execute("DELETE FROM users WHERE tenant_id=%s AND id=%s", (tenant_id, uid))
    conn.execute("DELETE FROM org_nodes WHERE tenant_id=%s AND left(id,9)=%s", (tenant_id, "SYN_NODE_"))
    return len(docs), len(users)


def main() -> int:
    ap = argparse.ArgumentParser(description="灌库/校验/清除评测夹具")
    ap.add_argument("--verify", action="store_true", help="只校验不写入")
    ap.add_argument("--clean", action="store_true", help="清除全部 SYN_ 夹具与账号")
    args = ap.parse_args()
    org, fixtures = load_spec()
    with database_connection() as conn:
        tenant = resolve_tenant(conn)
        print(f"租户: {tenant['code']}（{tenant['id']}）")
        if args.clean:
            n_docs, n_users = clean(conn, tenant["id"])
            print(f"已清除 {n_docs} 篇文档 / {n_users} 个账号")
            return 0
        user_ids = {}
        if not args.verify:
            seed_nodes(conn, tenant["id"], org)
            user_ids = seed_users(conn, tenant["id"], org)
            creator = user_ids["SYN_A_ADMIN001"]
            st = seed_documents(conn, tenant["id"], creator, load_corpus_docs())
            n_grants = seed_grants(conn, tenant["id"], fixtures, user_ids)
            print(f"灌库完成：节点 {len(org['nodes'])}、账号 {len(org['identities'])}、"
                  f"文档 新增 {st['inserted']}/未变 {st['updated']}（切片 {st['chunks']}）、授权 {n_grants}")
        if True:  # 灌完必校验：夹具写坏比不写更危险（白检的安全感）
            if not user_ids:
                user_ids = {i["logical_id"]: "usr_syn_" + i["username"] for i in org["identities"]}
            problems = verify(conn, tenant["id"], org, fixtures, user_ids)
            if problems:
                print(f"✗ 校验发现 {len(problems)} 个问题：", file=sys.stderr)
                for p in problems[:30]:
                    print("  -", p, file=sys.stderr)
                return 1
            print("✓ 夹具校验通过：文档/切片/金丝雀/口令/权限语义全部符合 spec")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
