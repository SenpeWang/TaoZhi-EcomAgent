export type EvaluationMode = 'live' | 'synthetic_trace_regrade'
export type EvaluationStatus = 'passed' | 'failed' | 'unassessable'
export type EvaluationApi = <T>(path: string) => Promise<T>

export interface EvaluationRun {
  run_id: string
  owner_id: string
  owner_name: string
  created_at: string
  mode: EvaluationMode
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
  expected: string[] | null
  observed: EvaluationStep[] | null
  steps_complete: boolean | null
  verdicts: {
    judge: string
    dimension: string
    status: EvaluationStatus
    reason: string
    issues: { failure_type: string; tool_name: string | null; step: number | null }[]
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

export interface EvaluationMetrics {
  required_tool_coverage: EvaluationRatio | null
  parameter_accuracy: EvaluationRatio | null
  reference_fact_coverage: EvaluationRatio | null
  call_coverage_among_known: EvaluationRatio | null
  measured_prompt_tokens: number | null
  measured_completion_tokens: number | null
}

export interface EvaluationRunDetail {
  metrics: EvaluationMetrics
  run: EvaluationRun
  comparison: EvaluationComparison | null
}

export interface EvaluationPage<T> {
  items: T[]
  next_cursor: string | null
}

export const evaluationModes = {
  live: '真实调研',
  synthetic_trace_regrade: '合成记录（回放与反例）',
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

/**
 * 按执行方式和反例来源区分报告的实际覆盖范围。
 * @param run - 来源已经由服务端解析的评测批次。
 * @returns 面向工作台用户的执行来源名称。
 */
export function evaluationSource(run: EvaluationRun): string {
  if (run.mode === 'live') return '真实调研'
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

export const evaluationFailures: Record<string, string> = {
  missing_observation: '缺少可判定的观测',
  run_status: '执行未按预期结束',
  tool_forbidden: '实际调用了禁用工具',
  tool_missing: '缺少必调工具',
  tool_arguments: '工具入参未满足契约',
  tool_status: '工具返回状态与预期不符',
  tool_data: '工具数据量与预期不符',
  tool_order: '工具调用依赖顺序不符',
  deliverable_structure: '交付内容缺少约定结构',
  fabricated_trace: '报告包含模拟执行文案',
  steering: '运行期干预未满足约定',
  reference_fact: '报告缺少参考事实',
  evidence_citation: '参考事实缺少对应证据',
  forbidden_claim: '报告出现标注禁止的结论',
  unclassified: '未分类',
}
