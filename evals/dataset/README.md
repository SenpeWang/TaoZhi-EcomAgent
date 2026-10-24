# evals/dataset/ — 独立评测数据包（spec 生成 + 幂等灌库）

把评测集从"某台机器数据库里的隐式状态"变成**随代码分发的数据包**：
规格（spec）进 Git，用例由规格确定性生成，夹具由 seed 幂等灌库，权限语义有实测矩阵背书。

数据集按**五个维度**组织，不是只按"问题类型"堆类别——单看问题类型会漏掉
"不可回答""高风险""来自真实失败"这些真正决定业务风险的切片。

## 五维矩阵

| 维度 | 取值 | 判定方式 | 覆盖下限（不足即生成失败） |
|---|---|---|---|
| **可回答性** answerability | answerable / partial / unanswerable | 前两档看事实命中，`unanswerable` 看是否如实说明资料不足、未硬答 | 78 / 11 / 19（下限 60 / 8 / 18） |
| **问题类型** qtype | fact / process / multihop / compare / troubleshoot / edge / ambiguous / security | 判「考察能力」是否达标：组合证据、排查顺序、边界条件、澄清动作 | fact 59、compare 10、security 14、process/edge/troubleshoot/ambiguous/multihop 各 4-5 |
| **难度** difficulty | easy / medium / hard | easy=单文档单点；medium=单文档归纳或多文档；hard=多跳、例外条款、资料不足 | 36 / 42 / 30（下限 20 / 25 / 20） |
| **来源** source | synthetic / badcase / adversarial / real | real=线上日志采样（暂无日志通道，仅保留取值）；badcase=上一轮基线真实失败回放 | 99 / 5 / 4 / 0（下限 60 / 4 / 4） |
| **风险** risk | high / medium / low | high=合规/权限/财务口径；medium=流程指引；low=一般参数查询 | 19 / 14 / 75（下限 15 / 10 / 30） |

`qtype=security` 强制 `risk=high`；`source=badcase` 必须带 `evidence_location`（失败可追溯）。

三个特殊小集以类别承载，不另立维度：**timeliness**（文档改版后答案变化，V1/V2 同库共存）、
**citation**（答案必须标来源）、**long_context**（答案分散在多节）；对抗层仍走
`golden_adversarial.json`（18 条，机读判定）。

## 目录结构

```
evals/dataset/
├── spec/                  规格（唯一事实来源，进 Git）
│   ├── org.yaml           7 个逻辑身份 → 登录名映射；2 个评测组织节点
│   ├── fixtures.yaml      5 篇权限夹具文档、2 个金丝雀、3 条授权（含过期=撤权、deny）
│   ├── corpus.yaml        6 个产品、5 条兼容关系、3 条政策、2 对冲突文档、6 篇指引/手册类
│   ├── parsing.yaml       10 个 PDF/DOCX 解析夹具的版面特征（表格 / 页眉页脚 / 多页）
│   └── templates.yaml     90 个模板（带五维标注）；槽位语法 {a.b.c}
├── schema.py              契约校验：必填字段、类别合法、五维合法、锚点可解析、覆盖下限
├── generate.py            确定性生成器 → datasets/generated/latest/（唯一一份，版本由 Git 承担）
├── parsing.py             PDF/DOCX 渲染 → 入库解析器读回
└── seed.py                幂等灌库：组织/身份/文档/切片/向量/ES/授权 + 权限实测校验
```

生成产物在 `evals/datasets/generated/`（进 Git）：`cases.jsonl`（108 例、20 类）、
`corpus_docs.jsonl`（32 篇 = 26 篇文本 + 6 篇指引手册 + 10 篇 PDF/DOCX 解析夹具）、
`manifest.json`（spec sha256 + 种子 + 类别与五维分布）。

## 使用

```bash
# 1. 生成（改 spec 后必须重跑；CI 有测试盯住"快照与 spec 一致"）
PYTHONPATH=src .venv/bin/python -m evals.dataset.generate

# 2. 契约校验（含五维覆盖下限）
PYTHONPATH=src .venv/bin/python -m evals.dataset.schema

# 3. 灌库（生产口径；幂等，可反复执行）
PYTHONPATH=src ECOM_ENV_FILE=.env.production .venv/bin/python -m evals.dataset.seed

# 4. 只校验不写入 / 清除全部夹具
PYTHONPATH=src ECOM_ENV_FILE=.env.production .venv/bin/python -m evals.dataset.seed --verify
PYTHONPATH=src ECOM_ENV_FILE=.env.production .venv/bin/python -m evals.dataset.seed --clean
```

## 设计约束

- **确定性**：同 spec 同种子，输出逐字节一致；manifest 不含时间戳，版本信息由 git 承担。
- **预期文档用 id 不用路径**：`expected_documents: ["SYN_CORPUS_LX20"]`——路径口径
  依赖 `data/` 目录（不进 Git，换机即失效）；缺一个即整例不可评估（覆盖率显式暴露）。
- **不编造标准答案**：资料写"未知"的题，预期行为是如实说明资料不足；`unanswerable`
  用例给的是"必须出现的说明性措辞"（如 `未标注`/`待确认`），不是编出来的数值。
- **身份是数据不是代码**：逻辑身份→登录名在 org.yaml，seed 建档、run_evals 解析；
  口令统一 `EVAL_PASSWORD`（≥12 位，过生产弱口令校验）。
- **夹具可直接写 published**：绕过发布审批流——这是测试夹具，不是业务资料。
- **`COMPANY_ROOT` 占位**：身份/文档可写 `node: COMPANY_ROOT`，seed 插入前解析为公司根节点。
- **权限语义实测**：`seed --verify` 用 `can_read()` 跑 10 条矩阵
  （有效授权/过期=撤权/deny 优先/密级通路/草稿状态）。
- **`SYN_` 前缀即命名空间**：文档/账号/节点/授权统一前缀，`--clean` 按它整包清除。
- **金丝雀标记（`SYN-CANARY-*`）只允许出现**在 `forbidden_disclosure` 与 `evidence_location`。
- **对抗用例判定必须可机读**：`judge` 规则挂在 `basis` 语料溯源上（协议见 BENCHMARK 第 8 节）。
- **解析夹具与检索语料同池**：PDF/DOCX 夹具是同主题的近重复文本，灌库后构成真实干扰项；
  这是有意的——换成不同主题就测不出"解析后的同义文档会不会抢排名"。
