# TaoZhi-EcomAgent 生产数据库表结构速查 + SQL 入门

> 数据来源：本机 PostgreSQL（`127.0.0.1:15432/ecom_production`）实测导出。
> 库里是**真实业务数据**（10958 订单、902 任务、1970 审计事件），练习时**只读，不要写**。

---

## 概览：34 张表，分 7 组

| 组 | 表 | 行数 |
|---|---|---|
| 身份与组织 | tenants / users / org_nodes / memberships / sessions / login_attempts | 1 / 13 / 18 / 12 / 595 / 4 |
| 文档与检索 | documents / document_versions / chunks / knowledge_mentions | 163 / 163 / 455 / 417 |
| 权限与授权 | document_grants / metric_grants | 3 / 0 |
| 任务与编排 | jobs / task_events / task_outbox / task_reviews / worker_heartbeats | 902 / 4652 / 0 / 0 / 5 |
| 指标语义层 | metrics | 18 |
| 审计与观测 | audit_events / model_calls | 1970 / 1028 |
| 业务数据 | orders / after_sales / products / skus / stock / channels | 10958 / 557 / 8 / 15 / 30 / 5 |
| 框架/系统 | alembic_version / checkpoints / checkpoint_blobs / checkpoint_writes / checkpoint_migrations | 1 / 4165 / 9587 / 19176 / 10 |

---

## 一、身份与组织（6 张）

### tenants · 租户根表（1 行）
| 字段 | 类型 | 说明 |
|---|---|---|
| id | text | 主键 |
| code | text | 租户编码 |
| name | text | 租户名 |
| mode | text | 环境模式 |
| enabled | boolean | 是否启用 |
| epoch | bigint | 版本号 |
| created_at | timestamptz | 创建时间 |

### users · 账号（13 行）
| 字段 | 类型 | 说明 |
|---|---|---|
| id | text | 主键 |
| tenant_id | text | 租户 |
| username | text | 登录名 |
| display_name | text | 显示名 |
| password_hash | text | 口令哈希 |
| system_admin | boolean | 是否系统管理员 |
| active | boolean | 是否启用 |
| must_change | boolean | 是否须改口令 |
| version | bigint | 乐观锁 |
| created_at | timestamptz | 创建时间 |

### org_nodes · 组织架构树（18 行）
| 字段 | 类型 | 说明 |
|---|---|---|
| id / tenant_id | text | 主键 / 租户 |
| parent_id | text NULL | 父节点（自引用树） |
| name | text | 名称 |
| kind | text | 类型（公司/部门/小组） |
| active | boolean | 是否启用 |
| version | bigint | 版本 |

### memberships · 用户↔组织归属（12 行）★权限核心
| 字段 | 类型 | 说明 |
|---|---|---|
| tenant_id / user_id / node_id | text | 租户/用户/节点 |
| role | text | 角色（owner/admin/manager/staff/…） |
| active | boolean | 是否有效 |

> 主键是 `(user_id, node_id, role)` —— 决定"谁能看到什么"。

### sessions · 登录会话（595 行）
| 字段 | 类型 |
|---|---|
| id / tenant_id / user_id | text |
| user_version | bigint |
| csrf_hash | text |
| expires_at | timestamptz |
| revoked | boolean |
| created_at | timestamptz |

### login_attempts · 登录尝试（4 行，防爆破）
| 字段 | 类型 |
|---|---|
| key | text（主键） |
| count | integer |
| locked_until | timestamptz NULL |
| updated_at | timestamptz |

---

## 二、文档与检索（RAG 核心，4 张）

### documents · 文档元数据（163 行）★
| 字段 | 类型 | 说明 |
|---|---|---|
| id / tenant_id | text | 主键 / 租户 |
| node_id | text | 归属组织节点 |
| creator_id | text | 创建者 |
| title | text | 标题 |
| scope | text | 可见范围（company/dept/…） |
| **level** | integer | **密级（1-5）** |
| ai_allowed | boolean | 是否允许 AI 使用 |
| download_allowed | boolean | 是否允许下载 |
| state | text | 状态（draft/published/…） |
| current_version | integer | 当前版本 |
| acl_version | bigint | ACL 版本 |
| error | text | 错误信息 |
| created_at / updated_at | timestamptz | 时间戳 |

### document_versions · 版本留痕（163 行）
| 字段 | 类型 |
|---|---|
| tenant_id / document_id | text |
| version | integer |
| title / body / content_hash | text |
| blob_name | text NULL |
| mime_type | text（默认 text/plain） |
| created_by | text |
| policy | jsonb |
| created_at | timestamptz |

### chunks · 切片 + 向量（455 行）★RAG 语料
| 字段 | 类型 | 说明 |
|---|---|---|
| id / tenant_id | text | 主键 / 租户 |
| document_id | text | 归属文档 |
| version | integer | 文档版本 |
| ordinal | integer | 切片顺序 |
| page | integer | 页码 |
| text | text | 切片正文 |
| **embedding** | **vector(512)** | 语义向量 |

### knowledge_mentions · 实体关系索引（417 行）
| 字段 | 类型 |
|---|---|
| id / tenant_id / document_id | text |
| version / chunk_id / entity | — |

---

## 三、权限与授权（2 张）

### document_grants · 显式授权（3 行）★安全红线
| 字段 | 类型 | 说明 |
|---|---|---|
| tenant_id / document_id / user_id | text | 租户/文档/用户 |
| **effect** | text | **allow / deny** |
| can_read / can_download / can_write | boolean | 细粒度权限 |
| **expires_at** | timestamptz NULL | **过期即撤权** |
| granted_by | text | 授权人 |
| created_at | timestamptz | 授权时间 |

> 主键 `(document_id, user_id)`。判定规则：**deny 优先**；**过期 = 撤权**。

### metric_grants · 指标授权（0 行）
| 字段 | 类型 |
|---|---|
| tenant_id / metric_id / user_id | text |
| effect | text |
| granted_by | text |
| created_at | timestamptz |

---

## 四、任务与编排（5 张）

### jobs · 任务主表（902 行，26 字段）
| 字段 | 类型 | 说明 |
|---|---|---|
| id / tenant_id / owner_id | text | 主键/租户/所有者 |
| owner_version | bigint | 所有者版本 |
| node_id | text | 组织节点 |
| **kind** | text | 任务类型（research/…） |
| question | text | 问题 |
| **input_level** | integer | 输入密级 |
| payload | jsonb | 参数 |
| state | text | 状态（queued/running/…） |
| result | jsonb NULL | 结果 |
| sources | jsonb | 引用来源 |
| error | text | 错误 |
| **stage** | text | 当前阶段（默认"等待处理"） |
| progress | integer | 进度 |
| run_version | bigint | 运行版本 |
| attempts | integer | 重试次数 |
| lease_until | timestamptz NULL | 租约到期（崩溃恢复） |
| worker_id | text NULL | 处理 Worker |
| cancel_requested | boolean | 是否请求取消 |
| idempotency_key | text NULL | 幂等键 |
| fingerprint | text | 指纹 |
| created_at / started_at / finished_at | timestamptz | 时间戳 |
| **conversation_id** | text | 会话 ID（多轮追问） |

### task_events · 事件流水（4652 行）
`id, tenant_id, job_id, run_version, stage, progress, status, created_at`

### task_outbox · 事务发件箱（0 行）
`id, job_id, created_at`

### task_reviews · 人工复核（0 行）
`id, tenant_id, job_id, reviewer_id, decision, comment, created_at`

### worker_heartbeats · Worker 心跳（5 行）
`id, updated_at`

---

## 五、指标语义层（1 张）· ChatBI 安全底座

### metrics（18 行，18 字段）
| 字段 | 类型 | 说明 |
|---|---|---|
| id / tenant_id | text | 主键/租户 |
| metric_key / name | text | 指标键/名 |
| aliases | ARRAY | 别名（自然语言匹配） |
| description | text | 描述 |
| **sql_template** | text | **SQL 模板（人工编写，非 LLM 生成）** |
| dimensions | ARRAY | 允许维度白名单 |
| base_table | text | 主表（白名单内） |
| unit / format | text | 单位/格式 |
| level | integer | 密级 |
| scope / node_id / owner_id | text | 归属 |
| state | text | 状态（默认 draft） |
| created_at / updated_at | timestamptz | 时间戳 |

---

## 六、审计与观测（2 张）

### audit_events（1970 行）
`id, tenant_id, actor_id, action, target_id, node_id, level, system, detail, created_at`

### model_calls（1028 行）
`id, tenant_id, job_id, role, elapsed_ms, prompt_tokens, completion_tokens, outcome, created_at`

---

## 七、业务数据（6 张）

### orders（10958 行）
`id, tenant_id, order_no, channel_id, sku_id, qty, amount_minor, cost_minor, status, node_id, owner_id, created_at, paid_at`

### after_sales（557 行）
`id, tenant_id, order_id, type, reason, amount_minor, status, created_at, resolved_at`

### products（8 行）
`id, tenant_id, spu_code, name, category, brand(默认"膜法实验室"), cost_minor, list_price_minor, state, created_at`

### skus（15 行）
`id, tenant_id, spu_id, sku_code, spec, price_minor, cost_minor, state, created_at`

### stock（30 行）
`tenant_id, sku_id, warehouse, qty, safety_qty, updated_at`（复合主键）

### channels（5 行）
`id, tenant_id, code, name, created_at`

---

## 八、框架/系统表（6 张）

- **alembic_version**(1)：迁移版本号（当前 `010_embedding_cache` 链路）
- **checkpoints**(4165)、**checkpoint_blobs**(9587)、**checkpoint_writes**(19176)、**checkpoint_migrations**(10)：LangGraph 工作流状态持久化

---

# SQL 学习：用真实数据练手

## 0. 连库（只读方式）

```bash
ssh -F /dev/null -p 2222 -i /c/Users/84373/.ssh/id_wsl_agent -o BatchMode=yes sp@localhost \
  'cd /home/sp/TaoZhi-EcomAgent && .venv/bin/python - <<"PY"
import psycopg
from pathlib import Path
env={}
for l in Path(".env.production").read_text(encoding="utf-8").splitlines():
    if "=" in l and not l.strip().startswith("#"):
        k,v=l.split("=",1); env[k.strip()]=v.strip().strip(chr(34)).strip(chr(39))
with psycopg.connect(env["ECOM_DATABASE_URL"]) as c:
    for r in c.execute("select id from chunks limit 5"):
        print(r[0])
PY'
```

## 1. 查数据（SELECT）

```sql
-- 看全部列（练习用，生产少用 *）
SELECT * FROM documents LIMIT 5;

-- 只看关心列
SELECT id, title, level, state FROM documents LIMIT 5;

-- 去重看有哪些密级
SELECT DISTINCT level FROM documents ORDER BY level;
```

## 2. 过滤（WHERE）

```sql
-- 已发布且密级<=2 的文档
SELECT id, title, level
FROM documents
WHERE state = 'published' AND level <= 2;

-- 标题包含「充电」
SELECT id, title FROM documents
WHERE title LIKE '%充电%';

-- 空值判断
SELECT id, title FROM documents WHERE error IS NOT NULL;
```

## 3. 排序与分页（ORDER BY / LIMIT）

```sql
SELECT id, title, updated_at
FROM documents
ORDER BY updated_at DESC
LIMIT 10 OFFSET 0;      -- 第 1 页
```

## 4. 统计（聚合 + GROUP BY）

```sql
-- 各状态文档数
SELECT state, COUNT(*) AS n
FROM documents
GROUP BY state
ORDER BY n DESC;

-- 各密级的切片数
SELECT d.level, COUNT(c.id) AS chunks
FROM documents d
JOIN chunks c ON c.document_id = d.id
GROUP BY d.level
ORDER BY d.level;
```

## 5. 联表（JOIN）★最重要

四张核心表的关系：

```
tenants ──< users ──< memberships >── org_nodes
                                          │
                                    documents (node_id)
                                          │
                                      chunks (document_id)
```

**INNER JOIN** — 查「某文档的切片正文」：

```sql
SELECT d.title, c.ordinal, c.page, left(c.text, 60) AS snippet
FROM documents d
JOIN chunks c ON c.document_id = d.id
WHERE d.state = 'published'
ORDER BY d.title, c.ordinal
LIMIT 10;
```

**LEFT JOIN** — 查「每个用户有多少授权」：

```sql
SELECT u.username, COUNT(g.document_id) AS grants
FROM users u
LEFT JOIN document_grants g ON g.user_id = u.id
GROUP BY u.username
ORDER BY grants DESC;
```

## 6. 子查询 / CTE

```sql
WITH doc_stat AS (
  SELECT document_id, COUNT(*) AS n
  FROM chunks
  GROUP BY document_id
)
SELECT d.title, s.n
FROM documents d
JOIN doc_stat s ON s.document_id = d.id
ORDER BY s.n DESC
LIMIT 10;
```

## 7. JSONB 查询（你项目特色）

```sql
-- 取 payload 里的字段
SELECT id, payload->>'keyword' AS kw
FROM jobs
WHERE payload ? 'keyword'
LIMIT 5;

-- 展开 sources 数组
SELECT j.id, src->>'document_id' AS doc
FROM jobs j, jsonb_array_elements(j.sources) AS src
LIMIT 5;
```

## 8. 向量检索（pgvector，RAG 核心）

```sql
-- 查看向量维度
SELECT vector_dims(embedding) FROM chunks WHERE embedding IS NOT NULL LIMIT 1;

-- 余弦距离检索（<-> 越小越相似）
SELECT id, left(text, 50) AS snippet, embedding <=> %s::vector AS distance
FROM chunks
WHERE embedding IS NOT NULL
ORDER BY embedding <=> %s::vector
LIMIT 8;
```

> `embedding <=> query_vec` 就是"语义相似度"，距离越小越相关。

---

## 练习建议（按这个顺序）

1. **数行数**：`SELECT COUNT(*) FROM chunks;` → 应为 455
2. **看一篇文档切了几片**：按 document_id 分组统计
3. **找最相似的片段**：用向量检索跑一次
4. **查某个用户的权限**：join memberships + document_grants
5. **看一次任务的全过程**：join jobs + task_events 按时间排序

每一步都在**只读**前提下做，不会动你的生产数据。

---

## 安全提醒

- 这是**运行中的生产库**，里面是真实订单/任务/审计数据
- 练习只用 `SELECT`，**不要执行 `UPDATE` / `DELETE` / `DROP` / `TRUNCATE`**
- 密钥在 `.env` / `.env.production`，不要打印、不要入库
