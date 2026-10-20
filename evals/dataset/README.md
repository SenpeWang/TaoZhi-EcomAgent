# evals/dataset/ — 独立评测数据包（spec 生成 + 幂等灌库）

把评测集从"某台机器数据库里的隐式状态"变成**随代码分发的数据包**：
规格（spec）进 Git，用例由规格确定性生成，夹具由 seed 幂等灌库，权限语义有实测矩阵背书。
退役手写集（200 例）的三个结构性问题（夹具不存在、身份塌缩、语料不可重建）已固化为
下列设计约束，不保留历史叙述。

## 目录结构

```
evals/dataset/
├── spec/                  规格（唯一事实来源，进 Git）
│   ├── org.yaml           7 个逻辑身份 → 登录名映射；2 个评测组织节点
│   ├── fixtures.yaml      5 篇权限夹具文档、2 个金丝雀、3 条授权（含过期=撤权、deny）
│   ├── corpus.yaml        6 个产品、5 条兼容关系、3 条政策、2 对冲突文档
│   └── templates.yaml     26 个模板覆盖 11 个类别；槽位语法 {a.b.c}
├── schema.py              契约校验：必填字段、类别合法、身份/文档/金丝雀可解析、查重
├── generate.py            确定性生成器 → datasets/generated/latest/（唯一一份，版本由 Git 承担）
└── seed.py                幂等灌库：组织/身份/文档/切片/向量/ES/授权 + 权限实测校验
```

生成产物在 `evals/datasets/generated/`（进 Git）：`cases.jsonl`（44 例）、
`corpus_docs.jsonl`（16 篇语料）、`manifest.json`（spec sha256 + 种子 + 计数）。

## 使用

```bash
# 1. 生成（改 spec 后必须重跑；CI 有测试盯住"快照与 spec 一致"）
PYTHONPATH=src .venv/bin/python -m evals.dataset.generate

# 2. 契约校验
PYTHONPATH=src .venv/bin/python -m evals.dataset.schema

# 3. 灌库（生产口径；幂等，可反复执行）
PYTHONPATH=src ECOM_ENV_FILE=.env.production .venv/bin/python -m evals.dataset.seed

# 4. 只校验不写入 / 清除全部夹具
PYTHONPATH=src ECOM_ENV_FILE=.env.production .venv/bin/python -m evals.dataset.seed --verify
PYTHONPATH=src ECOM_ENV_FILE=.env.production .venv/bin/python -m evals.dataset.seed --clean

# 5. 跑评测（预期文档按 id 解析，身份按 spec 登录；完整命令见上一级 README）
```

## 设计约束

- **确定性**：同 spec 同种子，输出逐字节一致；manifest 不含时间戳，版本信息由 git 承担。
- **预期文档用 id 不用路径**：`expected_documents: ["SYN_CORPUS_LX20"]`——路径口径
  依赖 `data/` 目录（不进 Git，换机即失效）；id 口径由 seed 保证存在，
  缺一个即整例不可评估（覆盖率显式暴露，不按部分预期打分）。
- **身份是数据不是代码**：逻辑身份→登录名在 org.yaml，seed 建档、run_evals 解析。
  口令统一 `EVAL_PASSWORD`（≥12 位，过生产弱口令校验）。
- **夹具可直接写 published**：绕过发布审批流——这是测试夹具，不是业务资料。
- **`COMPANY_ROOT` 占位**：身份/文档可写 `node: COMPANY_ROOT`，seed 插入前解析为
  当前租户的公司根节点（`kind='company'`），规格不写死 UUID。
- **权限语义实测**：`seed --verify` 用 `can_read()` 跑 10 条矩阵
  （有效授权/过期=撤权/deny 优先/密级通路/草稿状态），字段对但 grade 算错的白检过不了。
- **`SYN_` 前缀即命名空间**：文档/账号/节点/授权统一前缀，`--clean` 按它整包清除，
  不碰业务数据。
- **不编造标准答案**：答案只能从实际交付资料推导；资料标"未知"的题，
  预期行为是如实说明资料不足。
- **金丝雀标记（`SYN-CANARY-*`）只允许出现**在 `forbidden_disclosure` 与
  `evidence_location`。
- **对抗用例判定必须可机读**：`judge` 规则挂在 `basis` 语料溯源上，评测链路
  不留人工复核口子（协议见 BENCHMARK 第 8 节）。
- **权限类用例绑定真实夹具状态**：不得通过问题正文指定角色绕过认证。
