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
 * 检查数值字段是否可安全格式化：缺失为 null，存在时为有限数。
 * @param value - 响应中的数值字段。
 * @returns 是否满足数值或缺失约定。
 */
function isNullableNumber(value: unknown): boolean {
  return value === null || (typeof value === 'number' && Number.isFinite(value))
}

/**
 * 核对指标目录中的单个指标定义。
 * @param spec - 未验证的指标定义。
 * @returns 是否具备键、名称、单位与方向。
 */
function validMetricSpec(spec: unknown): boolean {
  return (
    isRecord(spec) &&
    typeof spec.key === 'string' &&
    spec.key.length > 0 &&
    typeof spec.label === 'string' &&
    typeof spec.group === 'string' &&
    typeof spec.unit === 'string' &&
    ['higher', 'lower', 'neutral'].includes(String(spec.direction))
  )
}

/**
 * 核对某一「评测类型 + 口径」的指标 schema。
 * @param schema - 未验证的指标 schema。
 * @returns 是否可据其渲染指标列与卡片。
 */
function validMetricSchema(schema: unknown): boolean {
  return (
    isRecord(schema) &&
    typeof schema.version === 'number' &&
    ['agent', 'rag'].includes(String(schema.eval_kind)) &&
    typeof schema.scope === 'string' &&
    typeof schema.label === 'string' &&
    Array.isArray(schema.metrics) &&
    schema.metrics.every(validMetricSpec)
  )
}

/**
 * 核对指标目录按类型与口径列出的完整定义。
 * @param data - 未验证的目录响应。
 * @returns 是否包含版本号与按类型分组的 schema。
 */
function validMetricCatalog(data: JsonRecord): boolean {
  if (typeof data.version !== 'number' || !isRecord(data.kinds)) return false
  const kinds = Object.values(data.kinds)
  if (!kinds.length) return false
  return kinds.every(
    (kind) =>
      isRecord(kind) &&
      typeof kind.label === 'string' &&
      isRecord(kind.scopes) &&
      Object.values(kind.scopes).every(validMetricSchema),
  )
}

/**
 * 核对趋势批次项的摘要与可比性字段。
 * @param item - 未验证的趋势批次。
 * @returns 是否可渲染为趋势行。
 */
function validTrendItem(item: unknown): boolean {
  if (
    !isRecord(item) ||
    typeof item.run_id !== 'string' ||
    typeof item.created_at !== 'string' ||
    !['live', 'synthetic_trace_regrade'].includes(String(item.mode)) ||
    typeof item.gate_passed !== 'boolean' ||
    typeof item.is_baseline !== 'boolean' ||
    typeof item.comparable !== 'boolean'
  )
    return false
  if (item.summary !== null && item.summary !== undefined) {
    if (!isRecord(item.summary)) return false
    if (!Object.values(item.summary).every(isNullableNumber)) return false
  }
  return isNullableNumber(item.pass_rate_delta)
}

/**
 * 核对指标趋势响应：schema、批次摘要与聚合字段。
 * @param data - 未验证的趋势响应。
 * @returns 是否可按同一口径渲染概览。
 */
function validTrends(data: JsonRecord): boolean {
  if (data.metric_schema !== null && data.metric_schema !== undefined) {
    if (!validMetricSchema(data.metric_schema)) return false
  }
  if (!Array.isArray(data.items) || !data.items.every(validTrendItem)) return false
  if (!isRecord(data.aggregate) || typeof data.aggregate.batches !== 'number') return false
  if (data.baseline !== null && data.baseline !== undefined) {
    if (!isRecord(data.baseline) || typeof data.baseline.run_id !== 'string') return false
  }
  return true
}

/**
 * 核对评测批次身份及页面用于识别运行来源的版本摘要。
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
    ) &&
    ['dataset_snapshot_id', 'parent_run_id', 'trigger_note'].every(
      (key) => item[key] === undefined || item[key] === null || typeof item[key] === 'string',
    ) &&
    (item.run_seq === undefined ||
      item.run_seq === null ||
      (typeof item.run_seq === 'number' && Number.isSafeInteger(item.run_seq)))
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
 * 核对用例判定与可格式化得分。
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
 * 核对数据集版本的登记字段，供列表与抽屉共用。
 * @param item - 未验证的数据集条目。
 * @returns 是否可展示该数据集版本。
 */
function validDataset(item: unknown): boolean {
  return (
    isRecord(item) &&
    typeof item.dataset_id === 'string' &&
    typeof item.name === 'string' &&
    typeof item.version === 'string' &&
    typeof item.archived === 'boolean' &&
    (item.execution_modes === undefined || isStringList(item.execution_modes)) &&
    typeof item.case_count === 'number' &&
    isRecord(item.metadata)
  )
}

/**
 * 核对数据集内容快照的元信息，供批次详情与快照用例分页共用。
 * @param value - 未验证的快照元信息。
 * @returns 是否具备版本、摘要、用例数与文件清单。
 */
function validDatasetSnapshot(value: unknown): boolean {
  return (
    isRecord(value) &&
    typeof value.snapshot_id === 'string' &&
    ['agent', 'rag'].includes(String(value.eval_kind)) &&
    typeof value.dataset_version === 'string' &&
    typeof value.primary_dataset_id === 'string' &&
    typeof value.bundle_sha256 === 'string' &&
    typeof value.case_count === 'number' &&
    Number.isSafeInteger(value.case_count) &&
    isRecord(value.files) &&
    Object.values(value.files).every((entry) => typeof entry === 'string') &&
    isRecord(value.metadata) &&
    typeof value.created_at === 'string'
  )
}

/**
 * 核对快照中的单条原始用例记录。
 * @param value - 未验证的快照用例。
 * @returns 是否可展示套件、编号、摘要与完整原始用例。
 */
function validSnapshotCase(value: unknown): boolean {
  return (
    isRecord(value) &&
    typeof value.suite === 'string' &&
    typeof value.case_id === 'string' &&
    typeof value.case_sha256 === 'string' &&
    typeof value.ordinal === 'number' &&
    Number.isSafeInteger(value.ordinal) &&
    isRecord(value.case_payload)
  )
}

/**
 * 核对数据集分页详情：登记信息与原始用例数组。
 * @param data - 未验证的数据集详情。
 * @returns 是否可渲染用例列表。
 */
function validDatasetDetail(data: JsonRecord): boolean {
  return (
    validDataset(data.dataset) &&
    Array.isArray(data.cases) &&
    data.cases.every(isRecord) &&
    (data.next_cursor === null || typeof data.next_cursor === 'string') &&
    typeof data.total_cases === 'number'
  )
}

/**
 * 核对数据集原始内容响应：登记信息、文件摘要与完整原始用例。
 * @param data - 未验证的原始数据响应。
 * @returns 是否可下载与核对原始内容。
 */
function validDatasetRaw(data: JsonRecord): boolean {
  return (
    validDataset(data.dataset) &&
    isRecord(data.files) &&
    Object.values(data.files).every((value) => typeof value === 'string') &&
    Array.isArray(data.cases) &&
    data.cases.every(isRecord)
  )
}

/**
 * 校验评测结果的身份、分页边界和可展示内容，避免将另一条用例的详情显示在当前批次。
 * @param resource - 去除筛选参数的 API 路径。
 * @param data - 已解析的业务响应。
 * @throws 响应与所请求的评测资源不一致时抛出中文异常。
 */
export function validateEvaluationResponse(resource: string, data: JsonRecord): void {
  const invalid = new Error('评测记录响应异常，请刷新后重试')
  if (resource === '/evaluations/metrics/catalog') {
    if (!validMetricCatalog(data)) throw invalid
    return
  }
  if (resource === '/evaluations/metrics/trends') {
    if (!validTrends(data)) throw invalid
    return
  }
  if (resource === '/evaluations/datasets') {
    if (!Array.isArray(data.items) || !data.items.every(validDataset)) throw invalid
    return
  }
  if (resource.startsWith('/evaluations/datasets/')) {
    if (resource.endsWith('/raw')) {
      if (!validDatasetRaw(data)) throw invalid
      return
    }
    if (!validDatasetDetail(data)) throw invalid
    return
  }
  const [, , , runId, child, caseId] = resource.split('/')
  if (child === 'dataset-snapshot') {
    if (
      !runId ||
      !validDatasetSnapshot(data.snapshot) ||
      !Array.isArray(data.items) ||
      !data.items.every(validSnapshotCase) ||
      (data.next_cursor !== null && typeof data.next_cursor !== 'string') ||
      typeof data.total !== 'number'
    )
      throw invalid
    return
  }
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
        isRecord(data.observed) ||
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
      ) ||
      !(
        data.dataset_case === null ||
        data.dataset_case === undefined ||
        isRecord(data.dataset_case)
      ) ||
      !(
        data.dataset_case_sha256 === null ||
        data.dataset_case_sha256 === undefined ||
        typeof data.dataset_case_sha256 === 'string'
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
  if (
    !child &&
    data.dataset_snapshot !== null &&
    data.dataset_snapshot !== undefined &&
    !validDatasetSnapshot(data.dataset_snapshot)
  )
    throw invalid
  if (
    !child &&
    data.metric_schema !== null &&
    data.metric_schema !== undefined &&
    !validMetricSchema(data.metric_schema)
  )
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
