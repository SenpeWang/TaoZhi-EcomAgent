"""种子指标：淘智自身运营指标（基于工作台真实表，零造假数据）。

用法：ECOM_ENV_FILE=.env.production .venv/bin/python scripts/seed_metrics.py

指标口径模板约定（metrics.py 安全边界）：
- 必须含 %(tenant_id)s；可含 %(since)s/%(until)s/%(filters)s；禁止自带 LIMIT/分号/注释；
- 字面量 % 必须写成 %%；维度列必须在 dimensions 白名单内。
"""
import hashlib
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from ecom_copilot.enterprise.db import database_connection, fetch_one, as_jsonb  # noqa: E402

# (key, 名称, 别名, 口径模板, 维度, 主表, 单位, 格式, 密级, scope, 说明)
METRICS = [
    ("ops.docs_published_total", "已发布文档总数",
     ["文档总数", "发布文档数", "资料库总量", "有多少文档", "知识库规模"],
     "SELECT count(*) AS value FROM documents WHERE tenant_id=%(tenant_id)s AND state='published'",
     [], "documents", "篇", "number", 1, "company",
     "知识库中已发布（员工可检索）的文档总量。"),
    ("ops.docs_pending_review", "待审批文档数",
     ["待审批", "待审核文档", "等审批的资料", "卡在审核"],
     "SELECT count(*) AS value FROM documents WHERE tenant_id=%(tenant_id)s AND state='review'",
     [], "documents", "篇", "number", 1, "company",
     "已提交发布审批、尚未走完流程的文档数。"),
    ("ops.docs_by_level", "各密级文档分布",
     ["密级分布", "文档密级", "机密等级分布", "几级机密"],
     "SELECT level, count(*) AS value FROM documents WHERE tenant_id=%(tenant_id)s AND state='published' GROUP BY level ORDER BY level",
     ["level"], "documents", "篇", "number", 1, "company",
     "按密级（1 全员 / 2 部门 / 3 老板）统计已发布文档数。"),
    ("ops.questions_total", "提问任务量",
     ["提问量", "问答次数", "问题数", "问了多少", "咨询量"],
     "SELECT count(*) AS value FROM jobs WHERE tenant_id=%(tenant_id)s AND kind='ask' AND created_at >= %(since)s",
     [], "jobs", "次", "number", 2, "org",
     "统计周期内员工提交的问答任务数。"),
    ("ops.questions_insufficient_rate", "资料不足率",
     ["答不上来的比例", "无资料率", "未命中率", "资料不足占比"],
     "SELECT round(100.0 * count(*) FILTER (WHERE (result->>'hard_block')::boolean) / NULLIF(count(*),0), 2) AS value FROM jobs WHERE tenant_id=%(tenant_id)s AND kind='ask' AND created_at >= %(since)s",
     [], "jobs", "%", "percent", 2, "org",
     "检索授权资料不足、未给出事实结论的任务占比——知识库缺口的核心信号。"),
    ("ops.jobs_by_state", "任务状态分布",
     ["任务分布", "进行中的任务", "任务状态", "排队任务"],
     "SELECT state, count(*) AS value FROM jobs WHERE tenant_id=%(tenant_id)s AND created_at >= %(since)s GROUP BY state ORDER BY value DESC",
     ["state"], "jobs", "个", "number", 1, "company",
     "按任务状态统计周期内任务数（queued/running/completed/review 等）。"),
    ("ops.approvals_pending", "待审批量",
     ["待我审批", "审批积压", "等我审的", "审批队列"],
     "SELECT required_role, count(*) AS value FROM approvals WHERE tenant_id=%(tenant_id)s AND state='pending' AND created_at >= %(since)s GROUP BY required_role",
     ["required_role"], "approvals", "个", "number", 2, "org",
     "待处理审批按所需角色分布。"),
    ("sys.audit_events_total", "审计事件量",
     ["审计日志量", "操作记录数", "谁在操作系统"],
     "SELECT count(*) AS value FROM audit_events WHERE tenant_id=%(tenant_id)s AND created_at >= %(since)s",
     [], "audit_events", "条", "number", 3, "company",
     "统计周期内审计事件数（三级机密，仅老板可见）。"),
    ("sys.model_calls_by_role", "模型调用分布",
     ["模型调用", "智能体调用量", "token 消耗分布", "专家调用统计"],
     "SELECT role, count(*) AS value FROM model_calls WHERE tenant_id=%(tenant_id)s GROUP BY role ORDER BY value DESC",
     ["role"], "model_calls", "次", "number", 3, "company",
     "按智能体角色统计模型调用次数（三级机密，仅老板可见）。"),
]


def main() -> int:
    with database_connection() as c:
        tenant = fetch_one(c, "SELECT id FROM tenants ORDER BY created_at LIMIT 1")
        if not tenant:
            print("未找到租户，请先初始化企业库")
            return 1
        boss = fetch_one(c, """SELECT u.id FROM users u JOIN memberships m ON m.user_id=u.id
            WHERE u.tenant_id=%s AND m.role='boss' AND m.active AND u.active ORDER BY u.created_at LIMIT 1""", (tenant["id"],))
        root = fetch_one(c, "SELECT id FROM org_nodes WHERE tenant_id=%s AND kind='company'", (tenant["id"],))
        if not boss or not root:
            print("未找到老板账号或公司根节点，请先 bootstrap-owner")
            return 1
        n = 0
        for key, name, aliases, tpl, dims, table, unit, fmt, level, scope, desc in METRICS:
            mid = "met_" + hashlib.sha256(f"{tenant['id']}:{key}".encode()).hexdigest()[:24]
            c.execute("""INSERT INTO metrics(id,tenant_id,metric_key,name,aliases,description,sql_template,
                  dimensions,base_table,unit,format,level,scope,node_id,owner_id,state)
                VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'published')
                ON CONFLICT(id) DO UPDATE SET name=EXCLUDED.name,aliases=EXCLUDED.aliases,
                  description=EXCLUDED.description,sql_template=EXCLUDED.sql_template,
                  dimensions=EXCLUDED.dimensions,base_table=EXCLUDED.base_table,unit=EXCLUDED.unit,
                  format=EXCLUDED.format,level=EXCLUDED.level,scope=EXCLUDED.scope,state='published',
                  updated_at=now()""",
                (mid, tenant["id"], key, name, aliases, desc, tpl, dims, table, unit, fmt, level, scope, root["id"], boss["id"]))
            n += 1
        print(f"已注册/更新 {n} 个运营指标（负责人=老板，归属=公司根节点）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
