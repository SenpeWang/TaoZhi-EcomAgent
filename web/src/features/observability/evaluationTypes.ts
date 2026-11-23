export type EvaluationKind = 'agent' | 'rag'
export type EvaluationMode = 'live' | 'synthetic_trace_regrade'
export type EvaluationStatus = 'passed' | 'failed' | 'unassessable'
export type EvaluationScope = 'full' | 'quick' | 'retrieval' | 'adversarial' | 'behavior'
export type EvaluationJobState =
  'queued' | 'running' | 'review' | 'completed' | 'cancelled' | 'failed' | 'rejected' | 'legacy'
export type EvaluationAuditAction =
  'register_dataset' | 'register_profile' | 'trigger_run' | 'publish_run' | 'set_baseline'
export type EvaluationLayerName = 'retrieval' | 'adversarial' | 'behavior' | 'six_metrics'

export interface EvaluationJudgeSummary {
  judge: string
  dimension: string
  passed: number
  failed: number
  unassessable: number
}

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
}

export interface EvaluationDataset {
  dataset_id: string
  name: string
  eval_kind: EvaluationKind
  version: string
  description: string
  source: EvaluationMode
  suite: string
  archived: boolean
  content_sha256: string
  metadata: Record<string, unknown>
  case_count: number
  batch_count: number
  last_run_at: string | null
  avg_pass_rate: number | null
  best_pass_rate: number | null
  passed_batches: number
}

export interface EvaluationDatasetDetail {
  dataset: EvaluationDataset
  cases: Record<string, unknown>[]
  next_cursor: string | null
  total_cases: number
}

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

export interface TriggerEvaluationPayload {
  eval_kind: EvaluationKind
  dataset_id: string
  profile_id: string
  scope: EvaluationScope
  mode: EvaluationMode
  note: string
  mark_baseline: boolean
}

export interface EvaluationTriggerReceipt {
  job_id: string
  audit_id: string
  status: EvaluationJobState
}

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

export interface SetBaselinePayload {
  run_id: string
  note: string
}

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
}

export interface EvaluationTrendAggregate {
  batches: number
  avg_pass_rate: number | null
  best_pass_rate: number | null
  latest_pass_rate: number | null
}

export interface EvaluationBaselineSummary {
  baseline_id: string
  run_id: string
  note: string
  created_at: string | null
  pass_rate: number | null
  gate_passed: boolean | null
  report_sha256: string | null
}

export interface EvaluationTrends {
  dataset_version: string | null
  scope: EvaluationScope | null
  mode: EvaluationMode | null
  items: EvaluationTrendItem[]
  aggregate: EvaluationTrendAggregate
  baseline: EvaluationBaselineSummary | null
}

export interface EvaluationLayers {
  run_id: string
  eval_kind: EvaluationKind
  layers: Partial<Record<EvaluationLayerName, Record<string, unknown>>>
}

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

export interface EvaluationAuditPage {
  items: EvaluationAuditLog[]
  next_cursor: string | null
}

export interface EvaluationCase {
  case_id: string
  name: string
  category: string | null
  suite: string
  status: EvaluationStatus
  score: number | null
  hard_failure: boolean
}

export interface EvaluationStep {
  step: number | null
  tool_name: string
  status: string | null
  dispatched: boolean | null
  data_count: number | null
  parameter_keys: string[] | null
  parameters_match: boolean | null
}

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
}

interface EvaluationChanges {
  stable_passed: string[]
  stable_failed: string[]
  fixed: string[]
  regressed: string[]
}

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

export interface EvaluationRatio {
  numerator: number
  denominator: number
  rate: number | null
}

export const evaluationMeasures = {
  required_tool_coverage: '必调工具覆盖率',
  parameter_accuracy: '工具参数准确率',
  reference_fact_coverage: '参考事实覆盖率',
  call_coverage_among_known: 'Token 用量采集覆盖率',
} as const

export interface EvaluationMetrics extends Record<
  keyof typeof evaluationMeasures,
  EvaluationRatio
> {
  measured_prompt_tokens: number | null
  measured_completion_tokens: number | null
}

export interface EvaluationRunDetail {
  metrics: EvaluationMetrics
  run: EvaluationRun
  comparison: EvaluationComparison | null
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

export const evaluationModes = {
  live: '真实调研',
  synthetic_trace_regrade: '合成记录（回放与反例）',
}
export const evaluationScopes = {
  full: '全量',
  quick: '快速',
  retrieval: '检索层',
  adversarial: '对抗层',
  behavior: '行为层',
}
export const evaluationStatuses = { passed: '通过', failed: '失败', unassessable: '不可判定' }
export const evaluationTags = { passed: 'success', failed: 'danger', unassessable: 'info' } as const
export const evaluationSuites: Record<string, string> = {
  benchmark: '基准集',
  golden: '黄金集',
  regression: '回归集',
  challenge: '挑战集',
}
export const evaluationDimensions: Record<string, string> = {
  outcome: '交付结果',
  trajectory: '执行过程',
  governance: '治理约束',
}
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
export const evaluationToolStatuses: Record<string, string> = {
  ok: '成功',
  empty: '无匹配结果',
  error: '错误',
  timeout: '超时',
  cancelled: '取消',
}
export const evaluationAuditActions: Record<EvaluationAuditAction, string> = {
  register_dataset: '登记数据集',
  register_profile: '登记算法版本',
  trigger_run: '触发评测',
  publish_run: '入库批次',
  set_baseline: '设置基线',
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
