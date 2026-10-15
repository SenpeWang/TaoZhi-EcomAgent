#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""在企业正式库创建/补齐标准角色真实账号（员工 / 组长 / 管理员；老板由 manage_database.py bootstrap-owner 初始化）。

与已删除的 seed_demo 区别：
- 只写入**真实企业租户**（由 ECOM_TENANT_CODE 指定），不建 tenant_demo；
- 口令为 16 位强随机（满足生产 ≥12 位且非弱口令要求）；内网快捷登录开启时由 manage_database.py quick-passwords 统一下发默认口令；
- 账号挂载到**真实组织节点**（部门/小组），用于权限校验与资料可见性验证；
- 凭据仅写入 ~/.local/share/ecom-agent/accounts.json（0600），便于运维快速接入。

用法：
    PYTHONPATH=src ECOM_ENV_FILE=.env.production .venv/bin/python scripts/provision_accounts.py [--reset]
"""
from __future__ import annotations

import argparse
import json
import secrets
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from ecom_copilot.enterprise.config import load_config  # noqa: E402
from ecom_copilot.enterprise.db import database_connection, fetch_one  # noqa: E402
from ecom_copilot.enterprise.passwords import hash_password  # noqa: E402
from ecom_copilot.enterprise.services import generate_id  # noqa: E402

STATE = Path.home() / ".local/share/ecom-agent"

# (用户名, 显示名, 角色, 组织类型, 系统管理员)
BLUEPRINT = [
    ("admin", "系统管理员", "admin", "company", True),
    ("leader", "运营组长", "leader", "team", False),
    ("staff", "运营员工", "employee", "team", False),
]


def pick_node(conn, tenant: str, kind: str) -> str:
    """按组织类型挑一个真实节点：company→公司根，team→第一个小组。"""
    row = fetch_one(conn, "SELECT id FROM org_nodes WHERE tenant_id=%s AND kind=%s ORDER BY name LIMIT 1",
                    (tenant, kind))
    if not row:
        raise SystemExit(f"未找到 kind={kind} 的组织节点，请先执行 manage_database.py migrate")
    return row["id"]


def main() -> int:
    ap = argparse.ArgumentParser(description="创建企业标准角色真实账号")
    ap.add_argument("--reset", action="store_true", help="已存在账号也重置口令")
    args = ap.parse_args()

    cfg = load_config()
    if cfg.mode != "production":
        print(f"当前模式为 {cfg.mode}；生产账号请用 ECOM_ENV_FILE=.env.production 运行", file=sys.stderr)

    result = {}
    with database_connection() as c:
        tenant = fetch_one(c, "SELECT id FROM tenants WHERE code=%s", (cfg.tenant_code,))
        if not tenant:
            print(f"未找到企业租户 code={cfg.tenant_code}", file=sys.stderr)
            return 1
        tid = tenant["id"]
        company = pick_node(c, tid, "company")
        team = pick_node(c, tid, "team")

        for username, display, role, kind, is_admin in BLUEPRINT:
            node = company if kind == "company" else team
            existing = fetch_one(c, "SELECT id FROM users WHERE tenant_id=%s AND username=%s", (tid, username))
            if existing and not args.reset:
                print(f"  跳过已存在账号：{username}")
                continue
            password = secrets.token_urlsafe(12)[:16]
            if len(password) < 12:
                password = (password + "Mofa@2026")[:16]
            hashed = hash_password(password, cfg.mode)
            uid = existing["id"] if existing else generate_id("user")
            if existing:
                c.execute("UPDATE users SET password_hash=%s, display_name=%s, system_admin=%s, "
                          "active=true, must_change=false WHERE id=%s", (hashed, display, is_admin, uid))
            else:
                c.execute("INSERT INTO users(id,tenant_id,username,display_name,password_hash,"
                          "system_admin,must_change) VALUES(%s,%s,%s,%s,%s,%s,false)",
                          (uid, tid, username, display, hashed, is_admin))
            if role == "admin":
                c.execute("DELETE FROM memberships WHERE tenant_id=%s AND user_id=%s", (tid, uid))
            else:
                c.execute("INSERT INTO memberships(tenant_id,user_id,node_id,role) VALUES(%s,%s,%s,%s) "
                          "ON CONFLICT(user_id,node_id,role) DO UPDATE SET active=true", (tid, uid, node, role))
            result[username] = {"password": password, "display_name": display, "role": role}
            print(f"  已创建/更新账号：{username} ({display}, {role})")

    STATE.mkdir(parents=True, exist_ok=True)
    out = STATE / "accounts.json"
    merged = {}
    if out.exists():
        try:
            merged = json.loads(out.read_text())
        except json.JSONDecodeError:
            merged = {}
    merged.update({u: {**v, "tenant_code": cfg.tenant_code} for u, v in result.items()})
    out.write_text(json.dumps(merged, ensure_ascii=False, indent=2))
    out.chmod(0o600)

    print(f"\n凭据已写入 {out} (0600)：")
    for u, v in result.items():
        print(f"  {u:8s} {v['password']}  ({v['display_name']} / {v['role']})")
    print("\n提示：生产环境请妥善保管；如需强制首次登录改密，可在创建后将该账号 must_change 置 true。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
