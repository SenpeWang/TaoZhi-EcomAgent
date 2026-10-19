# 评估基准 — 口径与链路

本文件是评估的**唯一口径来源**：指标定义、判定协议、报告 schema、数据契约都在这里。
`evals/README.md` 只讲怎么跑；代码注释不再复述口径——口径散在多处必然互相矛盾。

标注 `[待接线]` 的条目是尚未落到代码的部分（第 10 节有清单），其余均为现行口径。

---

## 1. 模块边界

**属于本模块**：数据集（spec / generate / seed）、指标、判定协议、运行器、基线、报告、单元测试。

**不属于本模块**（发现即删，不留第二套口径）：

| 不属于 | 实际情况 |
|---|---|
| `src/ecom_copilot/enterprise/metrics.py` | 业务指标语义层（ChatBI 的 metric_key / dimensions / SQL 模板白名单），不是评估 |
| `src/ecom_copilot/observability/metrics.py` | 运行时 token / 延迟观测，链路算成本依赖它 |
| `migrations/versions/007_metrics.py`、`scripts/seed_*_metrics.py` | 建表迁移与业务数据灌入 |
| `src/ecom_copilot/hypothesis/tracker.py` 的 `evaluate()` | 业务假设评估 |
| 一次性探针 / 阈值实证 / 消融对比脚本 | 实验属实验，不属评估：结论回写代码注释后脚本不再保留（已取消） |
| 安全对抗（金丝雀 / 越权 / 注入 / 泄漏） | 独立 red-team 套件，单独跑、单独记分，不计入 RAG 有效性指标 |
| `seed.py --verify` 的权限实测矩阵 | 灌库自检，属数据准备而非评估 |

---

## 2. 链路

```
spec/*.yaml ──generate(固定种子)──> cases.jsonl + corpus_docs.jsonl + manifest(spec_sha256)
    │                                              │
    │                                        seed(幂等, --verify 自检)
    │                                              ↓
    └──────────> 检索层 run ───── 生成层 run ──> report.json ──compare──> baseline.json
```

数据集进 Git（可 diff、可追溯）；运行产物进 `reports/`（gitignored）；基线进 Git（唯一一份）。

---

## 3. 确定性设计

| 来源 | 做法 |
|---|---|
| 数据集 | `manifest` 记 `spec_sha256`，跑前比对；漂移即拒跑 `[待接线 preflight]` |
| 抽样 | **取消随机抽样，全量跑**（检索池 28 例）；确需抽样则固定 seed 并写进报告 |
| 检索 | 固定 embedding 模型与维度、ES mapping、HNSW `ef_search`/`probes`、rerank 模型名，全部写进报告 |
| 答案生成 | temperature=0、seed 固定、模型名+版本进报告 |
| judge | 同上 + **结果缓存**（key = `case_id` + answer hash + judge 模型 + prompt 版本）`[待接线]` |
| 报告 | 固定 schema；时间戳/耗时只进文件名与 metadata，不进可比字段；计算层不取整；bootstrap 固定 seed |

**已知耦合（必须在报告中显式记录，否则结果不可解释）**：检索链路的 TF-IDF 分支每次查询
按当次候选集现场拟合，IDF 随之变化——同一 query 在不同候选集下得分不同。这是生产链路的
真实行为，评测不改造它，但报告必须记录候选数。

### 非确定性的正确处理

答案与 judge 都是 LLM 调用，不可能完全确定。不假装确定，做三件事：

1. **量化**：每用例跑 k=3，报**一次成功率 mean** 与 **pass^k（全过率）**，两个数一起报；
2. **冻结**：judge 走缓存，同输入必同输出，重跑完全可复现且不重复烧钱；
3. **隔离**：被测版本（git SHA + 模型版本）变化时，报告显式标注"基线不可比"。

---

## 4. 可用性设计

1. **preflight 前置自检** `[待接线]`：服务健康 / 夹具已灌且 verify 通过 / spec 未漂移 / 数据集与 spec 一致——不满足就明确报"缺什么、怎么补"，不跑一半才失败。
2. **两个 SUT 适配器** `[待接线]`：`search(query, principal, limit) → [(doc_id, chunk_id, text, score)]` 与 `ask(question, principal) → (answer, citations[chunk_id])`。换任何 RAG 系统只换适配器，基准不变——这是"评估是独立模块"的落地形式。
3. **分层跑**：检索层规则判定、秒级，每次改动后跑；生成层分钟级，合并前跑。
4. **失败可定位**：逐用例给 `reason`，按类别聚合通过率。
5. **覆盖率显式**：不可评估的用例单列，不混入分母。
6. **输出双份**：终端表格（人看）+ JSON（机读 / CI）。

---

## 5. 指标口径

### 5.1 检索层（规则判定，不调 LLM）

相关性是二值的；**判定粒度 = `document_id` / chunk，与被测系统返回的粒度一致** `[待接线：现为文档标题级]`。

| 指标 | 定义 | 要点 |
|---|---|---|
| `hit@k` | 前 k 条中至少 1 条是金标准 | 只回答"找没找到"，不反映排序 |
| `recall@k` | 前 k 条覆盖的金标准数 / 金标准总数 | 多文档用例分母 > 1（金标准是集合） |
| `precision@k` | 前 k 条中金标准占比 | **分母固定 = k**，同时记录实际返回条数 `[待接线：现为实际返回条数]` |
| `mrr` | 首条命中的倒数排名，无命中 0 | — |
| `ndcg@k` | 二值相关性的位置折损 | 只惩罚位置，不区分相关项之间权重 |
| `map` | 每个位置上精度的均值，再对相关文档平均 | 多相关文档场景比 nDCG 更稳 |

### 5.2 生成层（LLM-as-judge + 嵌入）

| 指标 | 定义 | 依赖 |
|---|---|---|
| `faithfulness` | 答案拆原子声明，逐条判断能否由被引用上下文推出；= 有支撑声明数 / 总声明数。micro 为汇总口径（长答案权重更大），macro 为逐例比值均值 | 引用原文（见第 6 节） |
| `answer_relevancy` | 答案能反推出原问的程度；实现为"由答案生成问句 + 与原问的嵌入相似度" | 嵌入服务 |
| `answer correctness` | faithfulness 与「对参考答案的语义相似度」的组合 | **`reference_answer`** `[待接线]` |
| `semantic similarity` | 答案 vs 参考答案（嵌入相似度 / token F1） | **`reference_answer`** `[待接线]` |

不用整段二元判定：一段里"9 条有据 + 1 条编造"与"1 条有据 + 9 条编造"都会判 false，
既丢分辨率也无法在对比里看出改善。声明级判定是唯一能反映编造比例的口径。

### 5.3 可靠性

每用例跑 k=3，报**一次成功率 mean** 与 **pass^k（全过率）**。单次通过率会系统性高估稳定性。

### 5.4 工程指标（不进 RAG 质量口径）

延迟 p50/p95、token 消耗、成本估算。只用于回归对比，不与质量指标混谈。

---

## 6. 判定协议

- **声明级分解**：faithfulness 必须拆原子声明逐条判定，禁止整段二元结论。
- **判定输入必须是引用原文** `[待接线：现为引用文档标题]`：judge 拿不到原文就只能凭自身知识判断，声明级忠实度不成立。
- **judge 配置固定**：temperature=0、模型名与版本写入报告，保证可复现。
- **judge 结果缓存**：key = `case_id` + answer hash + judge 模型 + prompt 版本。
- **每条判定输出**：`verdict` + `reason`（≤40 字）+ 依据片段，供人复核。

---

## 7. 报告 schema（可比字段固定）

```
meta        git_sha / spec_sha256 / dataset_seed / judge 模型+版本 / 检索参数 / k
coverage    可评估率（不可评估用例单列）
retrieval   hit@{1,3,5} precision@k recall@k mrr ndcg@k map  + bootstrap CI
generation  faithfulness(micro/macro) relevancy correctness similarity + CI
reliability 一次成功率 mean / pass^k
per_case    case_id / category / expected / actual / verdict / reason / 依据片段
ops         延迟 p50/p95 / token / 成本
```

`baseline.json` 与 run 报告共用此 schema，逐项 diff；时间戳与耗时只进文件名。

---

## 8. 数据契约

- **spec 自足**：语料、身份、授权、模板全部在 `spec/` 内，换机可重建、可校验、可清除。
- **manifest 记 `spec_sha256`**，生成产物与 spec 的对应关系可机器核对。
- **确定性**：同 spec 同种子逐字节一致（已有契约测试守住）。
- **相关性金标准**用 `document_id`（稳定标识），不写路径、不写标题。
- **`reference_answer`** `[待接线]`：`answer correctness` 与 `semantic similarity` 的必要输入。
- 对抗集的语料溯源必须挂在 spec 夹具上，不引用生产库文档 id。

---

## 9. 变更规则

| 改什么 | 走什么流程 |
|---|---|
| 指标定义 / 口径 | 本文件 → `metrics.py` → 单测 → 重跑全量 → 重锁基线，提交说明写清口径变化 |
| 判定协议 | 本文件 → judge 实现 → 契约测试（缺规则/缺依据必须失败） |
| 数据集 | `spec` → `generate` → `seed`；`spec_sha256` 变化必须在提交说明里写清影响面 |
| 检索参数 | 先跑全量拿基线 → 改参数 → 再跑 → 对比后再定；不接受单条 query 的手感判断 |

单元测试（`evals/tests`）必须通过且不依赖在线服务，CI 只跑这一层 + 检索层。

---

## 10. 落地清单（尚未接线的部分）

1. 相关性粒度：标题级 → `document_id`/chunk 级
2. `precision@k` 分母：实际返回条数 → 固定 k，并在报告记录实际返回条数
3. 取消 `hyde_off/hyde_on` 双跑（消融属实验）与检索层随机抽样
4. 取消 `quick/full` 两套组合语义，收敛为单一入口 + 层参数
5. `six_metrics` 改名（名说六项、实际八项）
6. `reference_answer` 字段：spec → 生成 → 数据集
7. judge：声明级输入改为引用原文 + 结果缓存 + 模型版本记录
8. k=3 与 pass^k 接线
9. SUT 适配器（检索 / 生成）抽出，指标层与项目解耦
10. preflight 前置自检
