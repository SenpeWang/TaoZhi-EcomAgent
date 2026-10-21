# 评估基准 — 口径与链路

本文件是评估的**唯一口径来源**：指标定义、判定协议、报告 schema、数据契约都在这里。
`evals/README.md` 只讲怎么跑；代码注释不再复述口径——口径散在多处必然互相矛盾。

标注 `[待接线]` 的条目是尚未落到代码的部分（第 11 节有清单），其余均为现行口径。

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

## 3. 固定流程

以下流程是固定的，不随项目阶段变化。每条都写明触发条件与顺序。

### F1 数据回路（改数据集时走这条）

```
spec/*.yaml → generate → seed → evals/tests → 检索层 → 生成层 → 对比 baseline
```

### F2 改动 → 该跑什么（触发矩阵）

| 改了什么 | 必须跑 | 顺序 |
|---|---|---|
| 检索实现 / 检索参数（`ECOM_ANN_MAX_DIST`、`GATE_MARGIN`、精排双阈值、`ECOM_MAX_CHUNKS_PER_DOC`） | 检索层 | 单测 → 先跑全量拿基线 → 改 → 再跑 → 对比后再定；不接受单条 query 的手感判断 |
| 提示词 / 合成链路 / 模型 | 生成层 | 单测 → 检索层 → 生成层 |
| 指标定义 / 口径 | 全量 | 本文 + `metrics.py` + 单测 → 两层 → 重锁基线 |
| 判定协议 / judge 提示词 | 对应层 | 单测 → 对应层 → 重锁基线 |
| 数据集 spec | 全量 | F1 全流程 |
| 只改文档 | 单测 | `evals/tests` |

单元测试必须通过且不依赖在线服务；CI 只跑单测 + 检索层。

### F3 单次运行的内部顺序

1. **preflight**：服务健康 → 夹具已灌且 verify 通过 → spec 未漂移 → 数据集与 spec 一致
2. **取用例**：全量，按类别分层，不做随机抽样
3. **逐用例**：检索 → 生成 → 判定（判定输入 = 引用原文）
4. **汇总**：指标 + bootstrap CI + 覆盖率 + 一次成功率/pass^k + 延迟/成本
5. **写报告**：`reports/<时间戳>-<git短SHA>.json`
6. **对比**：与唯一 baseline 逐项 diff

### F4 判定规则（固定，任何实现变更都不得放宽）

- 相关性金标准 = `document_id`/chunk 级，**二值**判定，不做模糊匹配。
- faithfulness 必须**声明级**；禁止整段二元结论。
- **提交被拒 / 任务异常 / 未产出结论一律判失败**，任何情况下不得计为通过。
- 不可评估用例单列，**不混入分母**。
- 计算层不取整，取整只在渲染层。

### F5 写进报告的常量（不随运行漂移）

数据集种子与 `spec_sha256`（漂移即拒跑）、bootstrap seed、每用例重复次数 k、
judge 模型名与温度、embedding 模型与维度、HNSW `ef_search`/`probes`、rerank 模型名、
报告 schema 字段集。

### F6 收纳原则（固定）

- 评估资产只在 `evals/`；`src/` 下不得出现评估实现。
- 第二套口径与一次性探针：结论回写代码注释后脚本删除，不留第二套。
- 评估逻辑不进部署脚本；`scripts/` 只放部署与运维。
- `evals/` 之外不新增任何产出评估分数的入口。

### F7 评估闭环（每轮评估按此七步走，不可跳步）

1. **明确目标**：先写本轮要验证的假设（减少幻觉 / 提高关键证据召回 / 改善拒答），
   并选定判据指标。不同目标用不同指标，不允许"先跑一堆分数再解释"。
2. **建立数据集**：真实或经审核的合成问题，带**类型 / 难度 / 是否可回答**标签；
   需要参考答案的指标须同时准备 `reference_answer`。
3. **记录标准化运行数据**：逐条保存 用户输入 / 检索上下文 / 最终回答 / 参考答案 /
   文档ID，外加 模型 / 提示词版本 / 检索参数 / 延迟 / 成本。
4. **跑基线**：固定一套可复现配置拿逐样本结果；后续实验**每次只改一个变量**。
5. **分层分析**：按问题类型、难度、数据来源切片，同时看失败数、缺失值与低分样本，
   不只报整体均值。
6. **Bad Case 归因**：回看问题 / 上下文 / 回答 / 评分理由，归因到
   检索缺失 · 上下文噪声 · 生成幻觉 · 答非所问 · **评估器误判** 五类之一。
7. **设回归门槛**：门槛来自本项目基线与业务风险——用**相对分数变化**与
   **关键样本不得失败**两类规则；不照搬统一经验阈值。

---

## 4. RAG 三元组（诊断主轴）

三元组是**诊断视角**：先据此判断问题出在检索还是生成，再往下归因。
它不是完整评估清单（补充项见 4.4）。

| 维度 | 评估目标 | 核心问题 | 我们的指标 | 当前可测状态 |
|---|---|---|---|---|
| **上下文相关性** | 检索器 | 检索到的内容是否与查询高度相关 | `hit@k` `recall@k` `precision@k` `mrr` `ndcg@k` | ⚠️ 部分可测：现在是对"金标准文档集合"的召回/精度，**缺逐条上下文是否相关的判定**，"上下文充满噪声"这个失败模式测不出来 |
| **忠实度** | 生成器 | 答案是否完全基于所提供上下文 | `faithfulness`（声明级 micro/macro） | ⚠️ 指标已定义，但判定输入必须是引用原文 `[待接线]`；只给标题时该指标不成立 |
| **答案相关性** | 端到端 | 是否直接、**完整**且有效地回答了原问题 | `answer relevancy`（答案反推问句 + 嵌入相似度） | ⚠️ 现实现是 judge 单比特，无完整度维度 |

### 4.4 三元组之外的必备补充项

- **答案正确性 / 完整性**：`answer correctness` + `semantic similarity`
  （需 `reference_answer` `[待接线]`）。
- **不可回答问题的拒答能力**：`insufficient` 类用例 + 对抗层 decline 用例；
  现有样本仅 2 例，且 judge 的 abstain 维度实测几乎不触发（基线 1/37）。
- **鲁棒性**：对抗套件（注入 / 越权 / 撤权 / 冲突 / 未知 / 歧义 / 比较 / 组合 / 否定），
  独立跑、独立记分，不计入三元组。
- **系统指标**：延迟 p50/p95、token、成本；**吞吐量（并发）当前无指标**。

---

## 5. 确定性设计

| 来源 | 做法 |
|---|---|
| 数据集 | `manifest` 记 `spec_sha256`，跑前比对；漂移即拒跑 `[待接线 preflight]` |
| 抽样 | **取消随机抽样，全量跑**；确需抽样则固定 seed 并写进报告 |
| 检索 | 固定 embedding 模型与维度、ES mapping、HNSW `ef_search`/`probes`、rerank 模型名，全部写进报告 |
| 答案生成 | temperature=0、seed 固定、模型名+版本进报告 |
| judge | temperature=0、模型名+版本进报告；**结果缓存见第 8 节** |
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

## 6. 可用性设计

1. **preflight 前置自检** `[待接线]`：服务健康 / 夹具已灌且 verify 通过 / spec 未漂移 / 数据集与 spec 一致——不满足就明确报"缺什么、怎么补"，不跑一半才失败。
2. **两个 SUT 适配器** `[待接线]`：`search(query, principal, limit) → [(doc_id, chunk_id, text, score)]` 与 `ask(question, principal) → (answer, citations[chunk_id])`。换任何 RAG 系统只换适配器，基准不变——这是"评估是独立模块"的落地形式。
3. **分层跑**：检索层规则判定、秒级，每次改动后跑；生成层分钟级，合并前跑。
4. **失败可定位**：逐用例给 `reason`，按类别聚合通过率。
5. **覆盖率显式**：不可评估的用例单列，不混入分母。
6. **输出双份**：终端表格（人看）+ JSON（机读 / CI）。

---

## 7. 指标口径

### 7.0 指标选型依据（为什么是这几个，2026-10-20 定）

候选池 = 经典 IR 指标（检索）+ LLM-judge / 词汇重叠（响应）。选择标准只有一条：
**企业生产级 RAG 评估框架（RAGAS / DeepEval / Arize）实际在用的**，不用翻译/摘要
时代的遗产指标。

| 候选 | 去留 | 理由 |
|---|---|---|
| Recall@k | ✅ 门禁 | RAG 第一失效模式是"该找到的没找到"；分母=金标准总数，多文档用例分母>1 |
| MRR | ✅ 门禁 | 首条命中能力；客服/导购场景用户实际只看第一个结果 |
| Precision@k | ✅ 观察 | 噪声度量；分母固定为 k（7.1），报表同时显示实际返回条数 |
| nDCG@k | ✅ 观察 | 位置折损，覆盖"相关项被埋没" |
| F1 | ❌ | k 固定时 P/R 单独看更可解释；调和平均掩盖"噪声大"还是"漏得多" |
| MAP | ❌ | 为多相关文档的搜索排序设计；RAG 问答金标准通常 1-2 篇，AP 退化为 hit 变体，信息冗余（nDCG 已覆盖） |
| Faithfulness（声明级） | ✅ 门禁 | LLM-as-judge 主流；唯能量化幻觉的指标。整段二元判定会损失分辨率（9据1造 与 1据9造 同判 false） |
| Answer Relevancy | ✅ 门禁 | 端到端用户感知：直接、切题、无冗余 |
| ROUGE / BLEU / METEOR | ❌ 全不选 | 机器翻译/摘要时代遗产：① 依赖标准答案 ② 不理解语义，同义词/释义误判（中文企业文档尤甚）③ 生产级 RAG 框架无一以其为主指标 |
| token-F1 | ✅ 观察 | 词汇重叠类唯一轻量可用者：无需 judge、秒级、做冒烟与快速回归筛选；同义误判所以只观察不门禁 |

结论：**检索三件套（Recall/MRR/Precision）+ 生成两件套（Faithfulness/Relevancy）**，
其余全部观察或排除。词汇重叠类不作为质量口径。

### 7.1 检索层（规则判定，不调 LLM）

相关性是二值的；**判定粒度 = `document_id` / chunk，与被测系统返回的粒度一致** `[待接线：现为文档标题级]`。

| 指标 | 定义 | 要点 |
|---|---|---|
| `hit@k` | 前 k 条中至少 1 条是金标准 | 只回答"找没找到"，不反映排序 |
| `recall@k` | 前 k 条覆盖的金标准数 / 金标准总数 | 多文档用例分母 > 1（金标准是集合） |
| `precision@k` | 前 k 条中金标准占比 | **分母固定 = k**，同时记录实际返回条数 `[待接线：现为实际返回条数]`。不设门禁：32 篇小语料上分母有结构性上限（单预期文档 1/8、双预期 0.25），衡量的是语料结构而非系统质量，回归检测由 recall/hit/MRR 承担 |
| `mrr` | 首条命中的倒数排名，无命中 0 | — |
| `ndcg@k` | 二值相关性的位置折损 | 只惩罚位置，不区分相关项之间权重 |

### 7.2 生成层（LLM-as-judge + 嵌入）

| 指标 | 定义 | 依赖 |
|---|---|---|
| `faithfulness` | 答案拆原子声明，逐条判断能否由被引用上下文推出；= 有支撑声明数 / 总声明数。micro 为汇总口径（长答案权重更大），macro 为逐例比值均值 | 引用原文（见第 8 节） |
| `answer_relevancy` | 答案能反推出原问的程度；实现为"由答案生成问句 + 与原问的嵌入相似度" | 嵌入服务 |
| `answer correctness` | faithfulness 与「对参考答案的语义相似度」的组合 | **`reference_answer`** `[待接线]` |
| `semantic similarity` | 答案 vs 参考答案（嵌入相似度 / token F1） | **`reference_answer`** `[待接线]` |

### 7.3 可靠性

每用例跑 k=3，报**一次成功率 mean** 与 **pass^k（全过率）**。单次通过率会系统性高估稳定性。

### 7.4 工程指标（不进 RAG 质量口径）

延迟 p50/p95、token 消耗、成本估算。只用于回归对比，不与质量指标混谈。

---

## 8. 判定协议

- **声明级分解**：faithfulness 必须拆原子声明逐条判定，禁止整段二元结论。
- **判定输入是引用原文**（2026-10-21 接线）：judge 收到 `{标题, 正文}`，正文截断 400 字。
  此前只给标题，judge 只能凭自身知识判断，声明级忠实度不成立——那份分数不可用。
- **judge 配置固定**：temperature=0、模型名与版本写入报告，保证可复现。
- **judge 结果缓存**：key = `case_id` + answer hash + judge 模型 + prompt 版本。
- **每条判定输出**：`verdict` + `reason`（≤40 字）+ 依据片段，供人复核。
- **对抗层为规则判定，不用 LLM judge**：每条用例的机读 `judge` 规则挂在 `basis`
  语料溯源上——`mode=decline` 须命中至少一条拒答/资料不足表达（`must_contain_any`）；
  `mode=facts` 须逐字命中 `must_contain_all`、同义组每组至少一条（`any_groups`）、
  正则断言全部命中（`must_match`）；两种模式通用负向规则 `must_not_contain` /
  `must_not_match` 防泄漏与编造（老板级标题、手机号、成本数字等）。规则缺项
  （缺 mode / 缺规则 / 缺 basis）在数据集加载期即拒收。实现见 `run_evals.judge_adversarial`。

---

## 9. 报告 schema（可比字段固定）

```
meta        git_sha / spec_sha256 / dataset_seed / judge 模型+版本 / 检索参数 / k
coverage    可评估率（不可评估用例单列）
retrieval   hit@{1,3,8} precision@k recall@k mrr ndcg@k  + bootstrap CI
generation  faithfulness(micro/macro) relevancy correctness similarity + CI
reliability 一次成功率 mean / pass^k
retrieval.per_case  case_id / category / question / expected_document_ids /
            retrieved[{rank, document_id, chunk_id, title, score, rerank_score,
                       in_expected, quote}] / metrics{hit@1,hit@3,hit@8,recall@8,precision@8,mrr,ndcg@8}
behavior.detail     id / pass / why / verdict{pass,faithful,relevant,abstain} /
            claims / supported / answer / citations[{title, chunk_id, document_id, quote}]

两层各自带逐例证据，不合成顶层 `per_case`：检索层按 case_id 带 chunk 级证据，
行为层按 case_id 带答案与引用原文，形态不同硬合并只会让消费方各自再拆一遍。
ops         延迟 p50/p95 / token / 成本
```

`baseline.json` 与 run 报告共用此 schema，逐项 diff；时间戳与耗时只进文件名。

---

## 10. 数据契约

- **spec 自足**：语料、身份、授权、模板全部在 `spec/` 内，换机可重建、可校验、可清除。
- **manifest 记 `spec_sha256`**，生成产物与 spec 的对应关系可机器核对。
- **确定性**：同 spec 同种子逐字节一致（已有契约测试守住）。
- **相关性金标准**用 `document_id`（稳定标识），不写路径、不写标题。
- **每条用例须带 `difficulty` 与 `answerable` 标签** `[待接线]`：分层分析（F7-5）
  与拒答能力评估都依赖这两个维度；没有难度分层的均值无法回答「改哪里」。
- **`reference_answer`** `[待接线]`：`answer correctness` 与 `semantic similarity` 的必要输入。
- 对抗集的语料溯源必须挂在 spec 夹具上，不引用生产库文档 id。

### 10.1 类别与专属契约

| 类别 | 归属层 | 专属契约 |
|---|---|---|
| product / compat / aftersale / multi_doc / insufficient / conflict / troubleshoot / timeliness / citation / long_context | retrieval + behavior | 必给 `expected_documents` + `expected_facts` |
| role / grant / revocation / injection / leakage | behavior | 必给 `pass_criteria`；禁区含真实存在的金丝雀 |
| **ambiguous** | behavior | 必给 `pass_criteria`（判"是否澄清或分情况"），不要求单点答案 |
| **robust** | behavior | 锚点与基准问法一致，只变问法（同义/错字/缩写/口语/冗余），掉分即输入鲁棒性缺口 |
| **multi_turn** | behavior | 必给 `turns`（前置轮）；判定只针对最后一轮；短追问下限 4 字；同会话靠 `conversation_id` 隔离 |
| **parsing** | behavior | 夹具来自 `spec/parsing.yaml` → `dataset/parsing.py` 渲染真实 PDF/DOCX → 入库解析器读回；正文为空或 `facts` 缺失即生成失败 |

每条用例另带五维标注（answerability / qtype / difficulty / source / risk），取值、覆盖下限
与判定方式见 `dataset/README.md`（唯一归属，此处不复述）。报告同时按**类别**与**五维**聚合：
坏例可以同时按"考的是哪种能力"和"业务风险多高"两个口径定位。

多轮的实现边界：`/api/tasks` 的 `Question` 带 `conversation_id`，命中同一会话才继承上文证据；
空值退回关键词启发式（历史行为）。跨会话不串证据，这条由 `jobs.conversation_id` 索引保证。

---

## 11. 落地清单（尚未接线的部分）

1. 相关性粒度：标题级 → `document_id`/chunk 级
2. `precision@k` 分母：实际返回条数 → 固定 k，并在报告记录实际返回条数
3. 取消 `quick/full` 两套组合语义，收敛为单一入口 + 层参数
4. `six_metrics` 改名（名说六项、实际八项）
5. `reference_answer` 字段：spec → 生成 → 数据集
6. judge：~~声明级输入改为引用原文~~ ✅ 2026-10-21 接线（正文 400 字）；结果缓存与模型版本记录仍未做
7. k=3 与 pass^k 接线
8. SUT 适配器（检索 / 生成）抽出，指标层与项目解耦
9. preflight 前置自检
10. **逐条上下文相关性判定** `[待接线]`（三元组第 1 维，见第 4 节）
11. **用例标签 `difficulty` / `answerable`** `[待接线]`（F7-5 分层分析的前提）
12. **吞吐量（并发）指标** `[待接线]`
13. **`answer relevancy` 补完整度维度** `[待接线]`（三元组第 3 维）
14. ✅ 运行间比较改配对 bootstrap（`metrics.paired_delta`）+ 门禁拆三类语义（第 12 节）
15. ✅ 逐例证据落盘（检索 chunk 正文 / 答案 / 引用原文），失败归因可离线做
16. ✅ judge 校准工具 `evals/calibrate.py`（导出 + kappa 评分）——**待人工标注落地**


---

## 12. 门禁语义与 judge 校准（2026-10-21）

### 12.1 三类判定，只有两类阻塞

| 判定 | 来源 | 阻塞 | 说明 |
|---|---|---|---|
| 硬底线 | `HARD_FLOORS`（faithfulness 0.85） | 是 | 业务红线，与基线无关，跌破即停 |
| 显著回归 | `compare()` 配对 bootstrap | 是 | 相对基线，差值 95%CI 整体落在负区 |
| 目标线 | `TARGETS`（relevancy 0.80 / recall@8 0.80） | 否 | 业界经验值，只显示差距 |

目标线不阻塞的理由：本项目的 `relevant_rate` 是 judge 二元通过率，与 RAGAS
`answer_relevancy`（答案反推问句 + 嵌入相似度）不是同一个度量。跨口径套阈值的结果是
门禁永远红（基线 0.654 < 0.80），而"永远红"等于没有门禁。

### 12.2 为什么不再"跌了就报回归"

49 例检索集上 hit@1 的 95% 置信区间为 [0.612, 0.857]，宽 0.245。按符号判回归，
±0.24 的自然抖动都会被打成退化；跑几次之后团队就不看这个信号了。改用配对 bootstrap
（同一 `case_id` 前后做差再重采样），消掉用例难度这一层方差，只有差值 CI 整体落在
负区才算显著下降。用例集变了（新增/删除/改金标准）配对不成立，此时明确报"无法对比"，
不退回符号判定——把"没数据做检验"显示成"无回归"是假绿。

### 12.3 judge 校准（必须人工做）

```
.venv/bin/python evals/calibrate.py --export 60        # 分层抽样：一半失败一半通过
# 人工填 human.pass / faithful / relevant（true/false；拿不准留 null，评分时跳过）
.venv/bin/python evals/calibrate.py --score <sheet>
```

判据：Cohen's kappa ≥ 0.6 可用、≥ 0.8 强；低于 0.6 的维度，其分数只能当参考，
不能作门禁依据。结果存档 `evals/reports/calibration/<sheet>-stem-kappa.json`，
随版本留存以便追溯"当时为什么信这个判据"。

为什么校准放在归因链的前置位：归因四步里，第 2、3 步的判定都由 judge 产出。
judge 未校准时，无法区分"系统答错了"和"judge 判错了"，第一步之后的结论全部没有依据。
