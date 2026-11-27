/**
 * 评测类型：agent 为智能体调研评测，rag 为知识问答评测。
 */
export type EvaluationKind = 'agent' | 'rag'
/**
 * 评测执行方式：live 为真实调研，synthetic_trace_regrade 为合成记录回放与反例重判。
 */
export type EvaluationMode = 'live' | 'synthetic_trace_regrade'
/**
 * 单条用例的判定结果：passed 通过、failed 失败、unassessable 不可判定。
 */
export type EvaluationStatus = 'passed' | 'failed' | 'unassessable'
/**
 * 评测口径：full 全量、quick 快速、retrieval 检索层、adversarial 对抗层、behavior 行为层。
 */
export type EvaluationScope = 'full' | 'quick' | 'retrieval' | 'adversarial' | 'behavior'
/**
 * 评测任务状态：queued 排队、running 执行中、review 待复核、completed 已完成、
 * cancelled 已取消、failed 失败、rejected 被拒绝、legacy 为历史遗留数据。
 */
export type EvaluationJobState =
  'queued' | 'running' | 'review' | 'completed' | 'cancelled' | 'failed' | 'rejected' | 'legacy'
/**
 * 评测审计动作：登记数据集、登记算法版本、触发评测、批次入库、设置基线、清理评测数据。
 */
export type EvaluationAuditAction =
  | 'register_dataset'
  | 'register_profile'
  | 'trigger_run'
  | 'publish_run'
  | 'set_baseline'
  | 'purge_data'
  | 'migrate_dataset_versions'
/**
 * 评测分层名称：retrieval 检索层、adversarial 对抗层、behavior 行为层、six_metrics 六项指标层。
 */
export type EvaluationLayerName = 'retrieval' | 'adversarial' | 'behavior' | 'six_metrics'

/**
 * 单个评测指标的口径定义。
 * key、label 为编码与名称；group 为分组；unit 为展示单位；
 * direction 表示数值越高越好、越低越好或中性；description 为口径说明。
 */
export interface EvaluationMetricSpec {
  key: string
  label: string
  group: string
  unit: string
  direction: string
  description: string
}

/**
 * 某类评测在指定口径下的指标目录。
 * version 为目录版本；eval_kind 为评测类型，scope 为口径；metrics 为指标定义列表。
 */
export interface EvaluationMetricSchema {
  version: number
  eval_kind: EvaluationKind
  scope: EvaluationScope
  label: string
  metrics: EvaluationMetricSpec[]
}

/**
 * 评测类型下的指标目录，label 为类型名称，scopes 按口径保存各自的指标目录。
 */
export interface EvaluationMetricKind {
  label: string
  scopes: Record<string, EvaluationMetricSchema>
}

/**
 * 全量评测指标目录，version 为目录版本，kinds 按评测类型组织指标。
 */
export interface EvaluationMetricCatalog {
  version: number
  kinds: Record<string, EvaluationMetricKind>
}

/**
 * 单个判定器的结果汇总。
 * judge、dimension 为判定器编码与所属维度；passed、failed、unassessable 为三态计数。
 */
export interface EvaluationJudgeSummary {
  judge: string
  dimension: string
  passed: number
  failed: number
  unassessable: number
}

/**
 * 评测批次记录。
 * run_id、created_at 定位批次；mode、scope 为执行方式与口径；
 * dataset_sha256、judges_sha256、transcript_sha256 等摘要固定数据集、判定器、日志与模型配置版本；
 * total_cases、passed_cases 等与 pass_rate、mean_score 为通过统计；
 * latency_p50_ms、latency_p95_ms、total_tokens 为时延与 Token 用量；
 * is_baseline、comparable、report_available 表示基线、可比与报告状态。
 */
export interface EvaluationRun {
  run_id: string
  owner_id: string
  owner_name: string
  created_at: string
  mode: EvaluationMode
  scope: EvaluationScope
  git_sha: string | null
  dataset_sha256: string
  judges_sha256: string
  transcript_sha256: string | null
  counterexamples_sha256: string | null
  model_config_sha256: string | null
  gate_passed: boolean
  total_cases: number
  passed_cases: number
  failed_cases: number
  unassessable_cases: number
  dataset_id: string | null
  dataset_name: string | null
  dataset_version: string | null
  dataset_snapshot_id: string | null
  run_seq: number | null
  parent_run_id: string | null
  trigger_note: string | null
  eval_kind: EvaluationKind
  profile_id: string | null
  profile_version_tag: string | null
  profile_version_no: number | null
  is_baseline: boolean
  report_available: boolean
  comparable: boolean
  pass_rate: number | null
  mean_score: number | null
  latency_p50_ms: number | null
  latency_p95_ms: number | null
  total_tokens: number | null
  judges: EvaluationJudgeSummary[] | null
  summary: Record<string, number | null> | null
  metric_schema_version: number | null
}

/**
 * 评测数据集摘要。
 * name、version、description 为资料；source 为来源方式，suite 为所属套件；
 * content_sha256 固定内容版本；case_count、batch_count 与通过率字段为规模与历史成绩。
 */
export interface EvaluationDataset {
  dataset_id: string
  name: string
  eval_kind: EvaluationKind
  version: string
  description: string
  source: EvaluationMode
  suite: string
  archived: boolean
  execution_modes?: string[]
  content_sha256: string
  metadata: Record<string, unknown>
  case_count: number
  batch_count: number
  last_run_at: string | null
  avg_pass_rate: number | null
  best_pass_rate: number | null
  passed_batches: number
}

/**
 * 数据集详情页数据：dataset 为摘要，cases 为当前页用例，
 * next_cursor 为下一页游标，total_cases 为用例总数。
 */
export interface EvaluationDatasetDetail {
  dataset: EvaluationDataset
  cases: Record<string, unknown>[]
  next_cursor: string | null
  total_cases: number
}

/**
 * 数据集的原始文件内容：files 为文件路径到内容的映射，cases 为解析后的用例。
 */
export interface EvaluationDatasetRaw {
  dataset: EvaluationDataset
  files: Record<string, string>
  cases: Record<string, unknown>[]
}

/**
 * 评测批次使用的数据集快照。
 * snapshot_id 为快照编号；bundle_sha256 固定快照内容；
 * files、metadata 为原始文件与元数据；case_count 为用例数。
 */
export interface EvaluationDatasetSnapshot {
  snapshot_id: string
  eval_kind: EvaluationKind
  dataset_version: string
  primary_dataset_id: string
  bundle_sha256: string
  case_count: number
  files: Record<string, string>
  metadata: Record<string, unknown>
  created_at: string
}

/**
 * 批次快照中的单条用例。
 * suite 为所属套件；case_id、case_sha256 定位并固定用例内容；
 * ordinal 为顺序号；case_payload 为原始用例数据。
 */
export interface EvaluationRunSnapshotCase {
  suite: string
  case_id: string
  case_sha256: string
  ordinal: number
  case_payload: Record<string, unknown>
}

/**
 * 批次快照用例的游标分页结果：snapshot 为快照信息，items 为当前页用例，total 为总数。
 */
export interface EvaluationRunSnapshotPage {
  snapshot: EvaluationDatasetSnapshot
  items: EvaluationRunSnapshotCase[]
  next_cursor: string | null
  total: number
}

/**
 * 算法版本档案。
 * profile_id、version_no、version_tag、digest 定位并固定版本；
 * git_sha 与 dataset_version 记录代码与数据集版本；
 * tech_stack、retrieval_params、prompt_snapshots 记录可复现配置；
 * batch_count 与平均指标为历史成绩。
 */
export interface EvaluationProfile {
  profile_id: string
  eval_kind: EvaluationKind
  version_no: number
  version_tag: string
  digest: string
  git_sha: string | null
  dataset_version: string
  tech_stack: Record<string, unknown>
  retrieval_params: Record<string, unknown>
  prompt_snapshots: Record<string, unknown>
  prompt_sha256: string
  notes: string
  created_by: string
  created_at: string
  created: boolean
  batch_count: number
  avg_pass_rate: number | null
  avg_mean_score: number | null
  avg_latency_p50_ms: number | null
  avg_tokens: number | null
}

/**
 * 登记算法版本的请求参数，包含评测类型、版本标签、技术栈、检索参数、
 * 提示词快照、代码提交与说明。
 */
export interface CreateEvalProfilePayload {
  eval_kind: EvaluationKind
  version_tag: string
  tech_stack: Record<string, unknown>
  retrieval_params: Record<string, unknown>
  prompt_snapshots: Record<string, unknown>
  git_sha?: string | null
  dataset_version?: string
  notes: string
}

/**
 * 触发评测的请求参数。
 * dataset_id、profile_id 指定数据集与算法版本；scope、mode 为口径与执行方式；
 * note 为触发说明；parent_run_id、mark_baseline 用于关联父批次与标记基线。
 */
export interface TriggerEvaluationPayload {
  eval_kind: EvaluationKind
  dataset_id: string
  profile_id: string
  scope: EvaluationScope
  mode: EvaluationMode
  note: string
  parent_run_id?: string
  mark_baseline: boolean
}

/**
 * 触发评测后的回执：job_id 为任务编号，audit_id 为审计编号，status 为任务初始状态。
 */
export interface EvaluationTriggerReceipt {
  job_id: string
  audit_id: string
  status: EvaluationJobState
}

/**
 * 评测基线记录。
 * baseline_id、run_id 定位基线与批次；eval_kind、dataset_id、scope、mode 定义适用范围；
 * active、superseded_at 表示是否生效；run_* 字段为对应批次的成绩摘要。
 */
export interface EvaluationBaseline {
  baseline_id: string
  eval_kind: EvaluationKind
  dataset_id: string
  dataset_version: string
  scope: EvaluationScope
  mode: EvaluationMode
  run_id: string
  note: string
  created_by: string
  created_at: string
  active: boolean
  superseded_at: string | null
  run_created_at: string | null
  run_gate_passed: boolean | null
  run_pass_rate: number | null
  run_git_sha: string | null
  run_report_sha256: string | null
  algorithm_version: string | null
}

/**
 * 设置基线的请求参数：run_id 为批次编号，note 为设置说明。
 */
export interface SetBaselinePayload {
  run_id: string
  note: string
}

/**
 * 趋势图中的单个批次。
 * run_id、created_at 定位批次；mode、scope、dataset_version 为口径与版本；
 * gate_passed、pass_rate、mean_score 为成绩；latency_* 与 total_tokens 为性能与用量；
 * pass_rate_delta 为相对基线差值，comparable 表示是否可横向比较。
 */
export interface EvaluationTrendItem {
  run_id: string
  created_at: string
  mode: EvaluationMode
  scope: EvaluationScope
  dataset_version: string | null
  algorithm_version: string | null
  gate_passed: boolean
  pass_rate: number | null
  mean_score: number | null
  latency_p50_ms: number | null
  latency_p95_ms: number | null
  total_tokens: number | null
  is_baseline: boolean
  comparable: boolean
  pass_rate_delta: number | null
  summary: Record<string, number | null> | null
}

/**
 * 趋势区间的汇总：batches 为批次数，avg、best、latest_pass_rate 为平均、最佳与最新通过率。
 */
export interface EvaluationTrendAggregate {
  batches: number
  avg_pass_rate: number | null
  best_pass_rate: number | null
  latest_pass_rate: number | null
}

/**
 * 趋势图中基线的摘要。
 * baseline_id、run_id 定位基线；pass_rate、gate_passed 为成绩；
 * report_sha256 固定报告内容；created_at 为设置时间。
 */
export interface EvaluationBaselineSummary {
  baseline_id: string
  run_id: string
  note: string
  created_at: string | null
  pass_rate: number | null
  gate_passed: boolean | null
  report_sha256: string | null
}

/**
 * 评测趋势数据。
 * metric_schema 为当前指标目录；dataset_version、scope、mode 为筛选条件；
 * items 为批次序列；aggregate 为汇总；baseline 为基线摘要。
 */
export interface EvaluationTrends {
  metric_schema: EvaluationMetricSchema | null
  dataset_version: string | null
  scope: EvaluationScope | null
  mode: EvaluationMode | null
  items: EvaluationTrendItem[]
  aggregate: EvaluationTrendAggregate
  baseline: EvaluationBaselineSummary | null
}

/**
 * 批次的分层明细：run_id、eval_kind 定位批次，layers 按层名保存各层原始结果。
 */
export interface EvaluationLayers {
  run_id: string
  eval_kind: EvaluationKind
  layers: Partial<Record<EvaluationLayerName, Record<string, unknown>>>
}

/**
 * 评测任务进度：state、stage、progress 为状态、阶段与进度，
 * result 为完成结果，error 为失败原因，created_at 等为时间信息。
 */
export interface EvaluationJob {
  job_id: string
  state: EvaluationJobState
  stage: string
  progress: number
  result: Record<string, unknown> | null
  error: string
  created_at: string
  started_at: string | null
  finished_at: string | null
}

/**
 * 评测审计记录。
 * action 为动作；target_id 为作用对象；actor_id、tenant_id 为操作人与租户；
 * git_sha 为代码版本；summary 为动作摘要。
 */
export interface EvaluationAuditLog {
  audit_id: string
  tenant_id: string
  actor_id: string
  action: EvaluationAuditAction
  target_id: string
  git_sha: string | null
  summary: Record<string, unknown>
  created_at: string
}

/**
 * 评测审计的游标分页结果：items 为当前页记录，next_cursor 为下一页游标。
 */
export interface EvaluationAuditPage {
  items: EvaluationAuditLog[]
  next_cursor: string | null
}

/**
 * 批次中的单条评测用例：name、suite 为名称与套件，status 为判定结果，
 * score 为得分，hard_failure 表示硬性失败。
 */
export interface EvaluationCase {
  case_id: string
  name: string
  category: string | null
  suite: string
  status: EvaluationStatus
  score: number | null
  hard_failure: boolean
}

/**
 * 用例期望或实际执行的工具步骤。
 * tool_name 为工具名；status 为执行状态；dispatched 表示是否派发；
 * data_count 为返回条数；parameter_keys、parameters_match 为参数校验信息。
 */
export interface EvaluationStep {
  step: number | null
  tool_name: string
  status: string | null
  dispatched: boolean | null
  data_count: number | null
  parameter_keys: string[] | null
  parameters_match: boolean | null
}

/**
 * 用例详情。
 * expected 为期望步骤，observed 为实际步骤，steps_complete 表示步骤是否完整；
 * verdicts 为各判定器的结论与问题项；trace_link 关联轨迹；
 * dataset_case、dataset_case_sha256 为数据集原始用例及其摘要。
 */
export interface EvaluationCaseDetail {
  case: EvaluationCase
  expected: string[]
  observed: EvaluationStep[] | null
  steps_complete: boolean | null
  verdicts: {
    judge: string
    dimension: string
    status: EvaluationStatus
    reason: string
    issues: {
      failure_type: string
      reason: string
      tool_name: string | null
      step: number | null
    }[]
  }[]
  trace_link: { task_id: string; trace_id: string } | null
  dataset_case: Record<string, unknown> | null
  dataset_case_sha256: string | null
}

interface EvaluationChanges {
  stable_passed: string[]
  stable_failed: string[]
  fixed: string[]
  regressed: string[]
}

/**
 * 批次对比结果。
 * comparable、passed 表示是否可比与是否达标；paired_counts 为两侧配对用例数；
 * changes 为稳定通过、稳定失败、修复与回归的用例编号；
 * excluded 为新增、仅基线、定义变更与不可判定等被排除的用例。
 */
export interface EvaluationComparison {
  comparable: boolean
  passed: boolean
  paired_counts: { standard: number; challenge: number }
  changes: { standard: EvaluationChanges; challenge: EvaluationChanges }
  excluded_count: number
  excluded: {
    new: string[]
    baseline_only: string[]
    definition_changed: string[]
    unassessable: string[]
  }
}

/**
 * 指标比例：numerator、denominator 为分子与分母，rate 为比例，分母为零时为空。
 */
export interface EvaluationRatio {
  numerator: number
  denominator: number
  rate: number | null
}

/**
 * 评测指标编码到中文名称的映射，用于报告与概览展示。
 */
export const evaluationMeasures = {
  required_tool_coverage: '必调工具覆盖率',
  parameter_accuracy: '工具参数准确率',
  reference_fact_coverage: '参考事实覆盖率',
  call_coverage_among_known: 'Token 用量采集覆盖率',
} as const

/**
 * 批次指标集合，按 evaluationMeasures 的编码提供比例，
 * 并记录实际采集到的输入与输出 Token 数。
 */
export interface EvaluationMetrics extends Record<
  keyof typeof evaluationMeasures,
  EvaluationRatio
> {
  measured_prompt_tokens: number | null
  measured_completion_tokens: number | null
}

/**
 * 批次详情页数据。
 * metrics 为指标集合；run 为批次记录；comparison 为基线对比；
 * metric_schema、dataset_snapshot 为口径与数据集快照。
 */
export interface EvaluationRunDetail {
  metrics: EvaluationMetrics
  run: EvaluationRun
  comparison: EvaluationComparison | null
  metric_schema: EvaluationMetricSchema | null
  dataset_snapshot: EvaluationDatasetSnapshot | null
}

/**
 * 展示批次通过率或均分，缺失时给出占位文案。
 * @param value - 0 到 1 之间的比例或分数。
 * @returns 百分比文本或缺失提示。
 */
export function formatEvaluationPercent(value: number | null): string {
  if (value === null) return '—'
  return (value * 100).toFixed(1) + '%'
}

/**
 * 展示毫秒级时延，缺失时给出占位文案。
 * @param value - 毫秒数。
 * @returns 秒级文本或缺失提示。
 */
export function formatEvaluationLatency(value: number | null): string {
  if (value === null) return '—'
  return (value / 1000).toFixed(1) + 's'
}

/**
 * 展示相对基线的通过率差值，正负号与百分点后缀便于横向比较。
 * @param value - 通过率差值（0 到 1 之间的小数）。
 * @returns 带符号的百分点文本或缺失提示。
 */
export function formatEvaluationDelta(value: number | null): string {
  if (value === null) return '—'
  return (value > 0 ? '+' : '') + (value * 100).toFixed(1) + 'pp'
}

/**
 * 按通过率差值的正负选择展示色调。
 * @param value - 通过率差值（0 到 1 之间的小数）。
 * @returns 上调、下调或持平的样式类名。
 */
export function evaluationDeltaTone(value: number | null): string {
  if (value === null || value === 0) return 'evaluation-delta-flat'
  return value > 0 ? 'evaluation-delta-up' : 'evaluation-delta-down'
}

/**
 * 展示数据集执行方式的中文名称。
 * @param mode - 报告执行方式。
 * @returns 面向工作台用户的执行方式名称。
 */
export function evaluationModeLabel(mode: EvaluationMode): string {
  return evaluationModes[mode]
}

/**
 * 展示评测口径的中文名称。
 * @param scope - 评测口径编码。
 * @returns 面向工作台用户的口径名称。
 */
export function evaluationScopeLabel(scope: EvaluationScope): string {
  return evaluationScopes[scope]
}

/**
 * 按有效分母展示判定比例，零样本单独标识。
 * @param ratio - 批次报告中的指标计数及比例。
 * @returns 百分比和样本数，或无可判定样本的提示。
 */
export function formatEvaluationRatio(ratio: EvaluationRatio): string {
  if (ratio.rate === null) return '无可判定样本'
  return (ratio.rate * 100).toFixed(1) + '%（' + ratio.numerator + '/' + ratio.denominator + '）'
}

/**
 * 评测执行方式的中文名称映射。
 */
export const evaluationModes = {
  live: '真实调研',
  synthetic_trace_regrade: '合成记录（回放与反例）',
}
/**
 * 评测口径的中文名称映射。
 */
export const evaluationScopes = {
  full: '全量',
  quick: '快速',
  retrieval: '检索层',
  adversarial: '对抗层',
  behavior: '行为层',
}
/**
 * 用例判定结果的中文名称映射。
 */
export const evaluationStatuses = { passed: '通过', failed: '失败', unassessable: '不可判定' }
/**
 * 用例判定结果对应的展示标签色，供工作台标签组件使用。
 */
export const evaluationTags = { passed: 'success', failed: 'danger', unassessable: 'info' } as const
/**
 * 数据集套件编码的中文名称映射。
 */
export const evaluationSuites: Record<string, string> = {
  benchmark: '基准集',
  golden: '黄金集',
  regression: '回归集',
  challenge: '挑战集',
  agent_research: 'Agent 调研基准集',
  rag_core: 'RAG 主评测集',
  rag_adversarial: 'RAG 对抗集',
}
/**
 * 判定维度的中文名称映射，覆盖交付结果、执行过程与治理约束。
 */
export const evaluationDimensions: Record<string, string> = {
  outcome: '交付结果',
  trajectory: '执行过程',
  governance: '治理约束',
}
/**
 * 判定器编码的中文名称映射，用于报告与用例详情展示。
 */
export const evaluationJudges: Record<string, string> = {
  ExecutionStatusJudge: '执行状态',
  ToolExecutionJudge: '工具执行',
  ToolBoundaryJudge: '工具范围',
  DeliverableJudge: '报告结构',
  AntiHallucinationJudge: '轨迹文案核验',
  ReferenceFactJudge: '事实与引用',
  SteeringJudge: '指令采纳',
  Observation: '记录完整性',
}
/**
 * 工具执行状态的中文名称映射。
 */
export const evaluationToolStatuses: Record<string, string> = {
  ok: '成功',
  empty: '无匹配结果',
  error: '错误',
  timeout: '超时',
  cancelled: '取消',
}
/**
 * 评测审计动作编码的中文名称映射。
 */
export const evaluationAuditActions: Record<EvaluationAuditAction, string> = {
  register_dataset: '登记数据集',
  register_profile: '登记算法版本',
  trigger_run: '触发评测',
  publish_run: '入库批次',
  set_baseline: '设置基线',
  purge_data: '清理评测数据',
  migrate_dataset_versions: '历史数据集版本迁移',
}

/**
 * 按执行方式和反例来源区分报告的实际覆盖范围。
 * @param run - 来源已经由服务端解析的评测批次。
 * @returns 面向工作台用户的执行来源名称。
 */
export function evaluationSource(run: EvaluationRun): string {
  if (run.mode === 'live') return evaluationModes.live
  return run.counterexamples_sha256 ? '合成反例复核' : '合成回放'
}

/**
 * 生成已编码的批次资源路径。
 * @param runId - 批次编号。
 * @returns 当前批次的查询路径。
 */
export function evaluationRunPath(runId: string): string {
  return '/evaluations/runs/' + encodeURIComponent(runId)
}

/**
 * 在同一执行模式及判定器版本中筛选可选择的基线批次。
 * @param run - 正在分析的批次。
 * @param candidate - 当前用户可见的候选批次。
 * @returns 是否满足批次比较的前提。
 */
export function isBaselineCandidate(run: EvaluationRun, candidate: EvaluationRun): boolean {
  return (
    run.run_id !== candidate.run_id &&
    run.mode === candidate.mode &&
    run.judges_sha256 === candidate.judges_sha256
  )
}

/**
 * 按指标目录声明的单位格式化数值，保证同一口径下展示规则一致。
 * @param spec - 指标目录中的口径定义。
 * @param value - 批次摘要中的指标数值。
 * @returns 带单位的数值文本或缺失占位。
 */
export function formatMetricValue(spec: EvaluationMetricSpec, value: number | null): string {
  if (value === null || !Number.isFinite(value)) return '—'
  switch (spec.unit) {
    case 'ratio':
    case 'percent':
      return (value * 100).toFixed(1) + '%'
    case 'seconds':
      return value.toFixed(2) + ' s'
    case 'milliseconds':
      return Math.round(value) + ' ms'
    case 'tokens':
    case 'count':
      return String(Math.round(value))
    case 'score':
      return value.toFixed(2)
    default:
      return Number.isInteger(value) ? String(value) : value.toFixed(2)
  }
}

/**
 * 按指标单位格式化相对基线的差值，比例指标统一换算为百分点。
 * @param spec - 指标目录中的口径定义。
 * @param delta - 相对 active 基线的差值。
 * @returns 带正负号的差值文本或缺失占位。
 */
export function formatMetricDelta(spec: EvaluationMetricSpec, delta: number | null): string {
  if (delta === null || !Number.isFinite(delta)) return '—'
  const sign = delta > 0 ? '+' : ''
  switch (spec.unit) {
    case 'ratio':
    case 'percent':
      return sign + (delta * 100).toFixed(1) + 'pp'
    case 'seconds':
      return sign + delta.toFixed(2) + ' s'
    case 'milliseconds':
      return sign + Math.round(delta) + ' ms'
    case 'tokens':
    case 'count':
      return sign + Math.round(delta)
    case 'score':
      return sign + delta.toFixed(2)
    default:
      return sign + (Number.isInteger(delta) ? String(delta) : delta.toFixed(2))
  }
}

/**
 * 按指标方向判断涨跌是否代表改善，返回样式类名。
 * @param spec - 指标目录中的口径定义。
 * @param delta - 相对 active 基线的差值。
 * @returns 改善、恶化或持平的样式类名。
 */
export function metricDeltaTone(spec: EvaluationMetricSpec, delta: number | null): string {
  if (delta === null || !Number.isFinite(delta) || delta === 0) return 'evaluation-delta-flat'
  if (spec.direction === 'neutral') return 'evaluation-delta-flat'
  const improved = spec.direction === 'lower' ? delta < 0 : delta > 0
  return improved ? 'evaluation-delta-up' : 'evaluation-delta-down'
}
