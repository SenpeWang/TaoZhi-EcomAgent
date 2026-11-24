# evals/rag_eval/ — 企业知识文档（RAG）评测维护约定与记录路由

> 本文件仅用于维护**第一套：企业知识库问答 & 受控问数 Workflow**（`src/taozhi/application/rag/`、`src/taozhi/infrastructure/knowledge/`、`evals/rag_eval/`）的评测标准。  
> 与**第二套：市场调研自治 Agent 系统**（`src/taozhi/application/research/`、`evals/agent_eval/`、`../agent_eval/测评记录.md`）**完全独立、互不混用数据集、指标与基线**。  
> 本文件随仓库版本化，作为该子系统的评测维护约定。

---

## 1. 测试目的（企业级核心质量目标）

1. **权限与安全合规零越权（零容忍）**
   - 严格守住租户隔离、组织树范围、密级分级（L1/L2/L3）、例外授权有效期与 `deny` 撤权优先级；
   - 涉密文档严禁出境（`allow_overseas=False`），金丝雀标记（`SYN-CANARY-*`）在答案与 SSE 事件流中 **零泄漏**。
2. **反幻觉与忠实度底线（宁可拒答，绝不编造）**
   - 生成结论必须有授权原文的逐字引用支撑；资料不足、未标注或无权限时必须按规范口径正确拒答，严禁掺入模型参数先验知识。
3. **解析保真与高精度检索**
   - 真实 PDF / DOCX（含页眉/页脚版本号、表格结构）入库不丢关键事实；
   - 新旧版本政策不发生时效倒挂、多实体对比提问不漏召回专属文档、排序精度（`Hit@1`/`MRR`/`nDCG@8`）与端到端延迟在可控范围内。
4. **统计严谨与可审计（防假绿）**
   - 禁止“看单条手感”或按小样本点估计波动判回归；一切迭代采用固定随机种子数据集（`spec_sha256` 校验）与配对 Bootstrap 95% 置信区间检验。

---

## 2. 评测记录路由（Mandatory Routing）

- **唯一问答测评记录文件**：[`./测评记录.md`](./测评记录.md)（相对路由 `./测评记录.md`）
- **强制更新触发条件**：
  1. 对 `src/taozhi/infrastructure/knowledge/`、`src/taozhi/application/rag/`、`src/taozhi/server/worker.py`（入库/问答分支）、`evals/metrics.py`、`evals/rag_eval/` 进行任何功能或口径修改；
  2. 运行任意 `evals.rag_eval.run`（`--quick` / `--full` / `--behavior-only` / `--adversarial-only`）；刷新门禁基线在评测中心把批次设为 active（`--baseline` 入库或接口设置）；仓库内基线记录用 `make evals-baseline` + `make evals-archive` 更新（可修订）。

---

## 3. 企业级三层 × 六维度评测体系与核心指标

> 详细数学公式与 JSON Schema 以唯一口径源 [`../BENCHMARK.md`](../BENCHMARK.md) 和 [`../metrics.py`](../metrics.py) 为准。

| 评估维度 | 评测对象与采集来源 | 核心企业级指标 | 门禁性质 |
|---|---|---|---|
| **1. 检索与上下文信噪比** | 全量带锚点用例，以正式员工账号 `staff` 调 `search_documents(limit=8)`，按**去重 `document_id`** 统计 | `Hit@1/@3/@8`、`Precision@1/@3/@8`（分母固定 `k`）、`Recall@1/@3/@8`、`nDCG@1/@3/@8`、`MRR`、`context_relevancy`、`ann_blocked_rate`、`coverage`、`95% CI` | `Recall@8 >= 0.80` 为目标线；配对 95% CI 显著下跌判**回归（阻塞）** |
| **2. 生成质量、误拒与 ChatBI** | 全量 540 例主集 + 60 例红队端到端问答 & 受控问数（6 大类 × 6 用途），`LLM-as-judge` + 规则 + 本地语义相似度 | **`faithfulness micro/macro`**、`accuracy`、`fact_recall`、`answer_correctness`、`citation_in_bounds_rate`、`false_refusal_rate (FRR)`、`true_abstain_rate`、`chatbi (routing/metric_key/rbac_denial)` | **`faithfulness micro >= 0.85` 为硬底线（阻塞）**；配对回归**阻塞**；`relevant_rate >= 0.80` 为目标线 |
| **3. 真实 SUT Token 与成本效率** | `LLMClient.usage_scope()` → PG `model_calls` + `jobs.result.token_usage`（**与 Judge 评测费用严格分账**） | `sut_token_cost (prompt/completion/total/usd)`、未缓存单题均耗、Token 放大倍率（`avg_attempts_per_uncached_case`）、按角色分布（`hyde` vs 专家）、`cache_hit_rate` & `estimated_cache_tokens_saved`、`judge_token_cost` | 监控成本异常膨胀与放大倍率 |
| **4. 全链路耗时 SLO 与吞吐** | HTTP 端到端 + `jobs` 时间戳 + `jobs.result.timing` | 裸检索与真实用户 **E2E `p50/p95/p99/max`**、分阶段耗时（`queue_wait_ms`、`planning_ms`、`retrieval_ms`、`data_query_ms`、`specialists_ms`、`embed_ms`、`es_ms`、`pg_ann_ms`、`rerank_ms`、`hyde_ms`）、`sla_breach_rate`、`--concurrency N` 并发 `QPS` | 长链路超阈值预警与瓶颈定位 |
| **5. 系统稳定性与降级容错** | Worker/Pipeline 遥测与 PG `jobs`/`model_calls` | `task_failure_rate`、`timeout_rate`、`json_retry_rate`、`failover_rate`、`es_fallback_rate`、`rerank_fallback_rate`、`--repeat N` 稳定性 **`pass^k`** | 防御静默降级、解析重试失效与并发抖动 |
| **6. 安全对抗与涉密合规红线** | 60 例红队规则对抗 + 权限/撤权/注入/ChatBI L2-L3 行为用例 | 金丝雀零泄漏数、对抗通过率、`rejected`、**L3 零外部模型调用合规率（`model_calls == 0`）** | **金丝雀泄漏 > 0 或 L3 出境合规 < 100% 直接阻塞** |

---

## 4. `测评记录.md` 强制记录规范（Audit Contract）

向 `./测评记录.md` 追加任何一次测评记录时，**必须完整包含以下 6 个栏目**，严禁省略、模糊概括（如“效果提升”）或编造未实测数字：

1. **测试元信息**：测试时间、Git SHA、`spec_sha256`/数据集规模、执行模式（`--quick` / `--full`）、使用的现有正式业务账号（`staff`/`leader`/`owner`/`admin`）、以及归档报告路径（`evals/reports/<ts>-<sha>.json`）。
2. **Base 基线指标**：明确写出本次改造对比的基准版本（评测中心 active 基线的批次号，或 `evals/baseline/` 对应的演进快照；含数据集版本、算法版本与核心检索/对抗/行为指标数字；若发生指标口径变更须显式标注）。
3. **使用的方法与具体改动**：逐文件、逐机制说明本次采用的技术方法、修改目的与涉密/架构边界处理。
4. **本次实测指标与对比增减**：如实列出实测值、与 Base 的差值（`Δ`）及 `95% CI` / 延迟 / Token 开销变化。
5. **Badcase 归因分类**：按五类归因（`检索缺失` / `上下文噪声` / `生成幻觉` / `答非所问` / `评估器误判`）给出具体失分案例 ID 与原因定位。
6. **结论与下一步动作**：明确给出通过/阻塞结论、是否在评测中心刷新 active 基线或更新仓库基线快照（及刷新理由），以及遗留待解问题。

---

## 5. 执行与纪律红线

- **只改单变量**：同一轮评估实验聚焦明确假设，不混改数据集与评估器口径后再把分数变化全算给模型或检索优化。
- **使用正式业务账号**：检索/行为/对抗层统一使用 `staff`、`leader`、`owner`、`admin` 正式业务账号；数据集逻辑身份由 seed 映射到这些账号，不创建独立登录账号。口令由 `ECOM_EVAL_PASSWORD`/`EVAL_PASSWORD` 统一，本地演示与快捷登录口令保持一致。
- **100% 实证**：文档中出现的一切分数、耗时、Token 数、失败条数必须直接源自评测中心批次/原始报告、`evals/baseline/` 快照或运行器报告的真实输出。
- **数据集版本目录规范**：三层评测集按 `evals/datasets/rag_v3_20261124/`（当前版本；v1/v2 历史只读）分版本只读留存，每个目录须配备 `README.md` 说明样本规模与分层结构，严禁原地覆盖历史版本。
- **入库入口唯一**：RAG 报告入库统一走 `python -m evals.rag_eval.publish`（内部委托 `evals/center`），按数据集版本 + `scope` + `mode` 落库；历史报告用 `--legacy` 归入 `v0_legacy`，不得另建写库入口。
