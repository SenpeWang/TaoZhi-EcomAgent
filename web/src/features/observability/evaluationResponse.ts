import { isRecord, type JsonRecord } from '../../shared/response.ts'

/**
 * 检查可按项展示的文本列表。
 * @param value - 响应中的列表字段。
 * @returns 是否为纯字符串数组。
 */
function isStringList(value: unknown): boolean {
  return Array.isArray(value) && value.every((item) => typeof item === 'string')
}

/**
 * 核对列表及详情共用的用例标识、判定与可格式化得分。
 * @param item - 接口返回的用例摘要。
 * @returns 是否具有页面所需的用例字段。
 */
function validEvaluationCase(item: unknown): item is JsonRecord & { case_id: string } {
  return (
    isRecord(item) &&
    typeof item.case_id === 'string' &&
    ['passed', 'failed', 'unassessable'].includes(String(item.status)) &&
    (item.score === null || Number.isFinite(item.score))
  )
}

/**
 * 核对批次身份及页面用于识别运行来源的版本摘要。
 * @param item - 批次列表或详情中的摘要。
 * @returns 是否能安全展示该批次。
 */
function validEvaluationRun(item: unknown): item is JsonRecord & { run_id: string } {
  return (
    isRecord(item) &&
    typeof item.run_id === 'string' &&
    ['live', 'synthetic_trace_regrade'].includes(String(item.mode)) &&
    typeof item.dataset_sha256 === 'string' &&
    typeof item.judges_sha256 === 'string' &&
    ['git_sha', 'transcript_sha256', 'counterexamples_sha256', 'model_config_sha256'].every(
      (key) => item[key] === null || typeof item[key] === 'string',
    )
  )
}

/**
 * 校验批次的四项判定比例和可缺测的 Token 计数。
 * @param metrics - 已解析的批次指标。
 * @returns 是否能区分零样本和零命中。
 */
function validEvaluationMetrics(metrics: unknown): boolean {
  if (!isRecord(metrics)) return false
  for (const key of [
    'required_tool_coverage',
    'parameter_accuracy',
    'reference_fact_coverage',
    'call_coverage_among_known',
  ]) {
    const ratio = metrics[key]
    if (!isRecord(ratio)) return false
    const { numerator, denominator, rate } = ratio
    if (
      typeof numerator !== 'number' ||
      !Number.isSafeInteger(numerator) ||
      numerator < 0 ||
      typeof denominator !== 'number' ||
      !Number.isSafeInteger(denominator) ||
      denominator < numerator
    )
      return false
    if (
      denominator === 0
        ? rate !== null
        : typeof rate !== 'number' || !Number.isFinite(rate) || rate < 0 || rate > 1
    )
      return false
  }
  return ['measured_prompt_tokens', 'measured_completion_tokens'].every((key) => {
    const count = metrics[key]
    return (
      count === null || (typeof count === 'number' && Number.isSafeInteger(count) && count >= 0)
    )
  })
}

/**
 * 校验评测结果的身份、分页边界和可展示内容，避免将另一条用例的详情显示在当前批次。
 * @param resource - 去除筛选参数的 API 路径。
 * @param data - 已解析的业务响应。
 * @throws 响应与所请求的评测资源不一致时抛出中文异常。
 */
export function validateEvaluationResponse(resource: string, data: JsonRecord): void {
  const [, , , runId, child, caseId] = resource.split('/')
  const invalid = new Error('评测记录响应异常，请刷新后重试')
  if (!runId || (child === 'cases' && !caseId)) {
    if (
      !Array.isArray(data.items) ||
      (data.next_cursor !== null && typeof data.next_cursor !== 'string')
    )
      throw invalid
    if (!data.items.every(runId ? validEvaluationCase : validEvaluationRun)) throw invalid
    return
  }
  if (caseId) {
    if (
      !validEvaluationCase(data.case) ||
      encodeURIComponent(data.case.case_id) !== caseId ||
      !isStringList(data.expected) ||
      !(data.steps_complete === null || typeof data.steps_complete === 'boolean') ||
      !(
        data.observed === null ||
        (Array.isArray(data.observed) &&
          data.observed.every(
            (step) =>
              isRecord(step) &&
              typeof step.tool_name === 'string' &&
              (step.parameter_keys === null || isStringList(step.parameter_keys)),
          ))
      ) ||
      !Array.isArray(data.verdicts) ||
      !data.verdicts.every(
        (item) =>
          isRecord(item) &&
          Array.isArray(item.issues) &&
          item.issues.every((issue) => isRecord(issue) && typeof issue.reason === 'string'),
      ) ||
      !(
        data.trace_link === null ||
        (isRecord(data.trace_link) &&
          typeof data.trace_link.task_id === 'string' &&
          data.trace_link.task_id.length > 0 &&
          typeof data.trace_link.trace_id === 'string' &&
          /^[0-9a-f]{32}$/.test(data.trace_link.trace_id))
      )
    )
      throw invalid
    return
  }
  if (child === 'layers') {
    if (
      !isRecord(data) ||
      typeof data.run_id !== 'string' ||
      encodeURIComponent(data.run_id) !== runId ||
      !['agent', 'rag'].includes(String(data.eval_kind)) ||
      !isRecord(data.layers)
    )
      throw invalid
    return
  }
  if (!child && !validEvaluationRun(data.run)) throw invalid
  if (!child && encodeURIComponent((data.run as JsonRecord).run_id as string) !== runId)
    throw invalid
  if (
    !child &&
    (data.run as JsonRecord).eval_kind !== 'rag' &&
    !validEvaluationMetrics(data.metrics)
  )
    throw invalid
  if (!child && (data.run as JsonRecord).eval_kind === 'rag' && !isRecord(data.metrics))
    throw invalid
  const comparison = child === 'comparison' ? data : data.comparison
  if (!child && comparison === null) return
  if (
    !isRecord(comparison) ||
    !isRecord(comparison.paired_counts) ||
    !isRecord(comparison.changes) ||
    !isRecord(comparison.excluded)
  )
    throw invalid
  for (const key of ['standard', 'challenge']) {
    const changes = comparison.changes[key]
    if (
      !isRecord(changes) ||
      !['regressed', 'fixed', 'stable_passed', 'stable_failed'].every((change) =>
        isStringList(changes[change]),
      )
    )
      throw invalid
  }
  const excluded = comparison.excluded
  if (
    !['new', 'baseline_only', 'definition_changed', 'unassessable'].every((key) =>
      isStringList(excluded[key]),
    )
  )
    throw invalid
}
