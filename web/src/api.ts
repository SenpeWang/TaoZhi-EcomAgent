/** 从未知网络响应读取数据；角色判断仍由服务端统一执行。 */
type JsonRecord = Record<string, unknown>

/**
 * 判断响应是否为对象，拒绝空值和数组作为业务对象。
 * @param value - 尚未验证的 JSON 值。
 * @returns 可安全读取字段的对象判断结果。
 */
function isRecord(value: unknown): value is JsonRecord {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

const spanKinds = new Set([
  'LLM',
  'TOOL',
  'RETRIEVER',
  'EMBEDDING',
  'RERANKER',
  'AGENT',
  'CHAIN',
  'INTERNAL',
])
const activityCategories = new Set([...spanKinds, 'CONTEXT', 'GUARDRAIL'])
const diagnosticCategories = new Set([
  'model_api',
  'tool',
  'retrieval',
  'context',
  'permission',
  'storage',
  'workflow',
])

/**
 * 验证诊断日志的类型、处理结果、可展示字段与当前接口筛选条件。
 * @param entries - 此步骤记录的全部结构化诊断。
 * @param filter - 查询要求命中的故障分类。
 * @returns 是否为所选执行中可展示的诊断记录。
 */
function validDiagnostics(entries: unknown, filter: string | null): boolean {
  if (!Array.isArray(entries)) return false
  let matched = filter === null
  for (const entry of entries) {
    if (
      !isRecord(entry) ||
      typeof entry.category !== 'string' ||
      !diagnosticCategories.has(entry.category) ||
      typeof entry.timestamp !== 'string' ||
      !Number.isFinite(Date.parse(entry.timestamp)) ||
      typeof entry.code !== 'string' ||
      !entry.code ||
      typeof entry.message !== 'string' ||
      !entry.message ||
      typeof entry.level !== 'string' ||
      !['error', 'warning'].includes(entry.level) ||
      typeof entry.outcome !== 'string' ||
      !['failed', 'retry', 'fallback', 'skipped'].includes(entry.outcome) ||
      (entry.exception_type !== null && typeof entry.exception_type !== 'string') ||
      (entry.http_status !== null &&
        !(
          typeof entry.http_status === 'number' &&
          Number.isSafeInteger(entry.http_status) &&
          entry.http_status >= 100 &&
          entry.http_status <= 599
        ))
    )
      return false
    if (filter === 'all' || entry.category === filter) matched = true
  }
  return matched
}

/**
 * 校验本条链路的节点分类计数。
 * @param value - 服务端返回的全量分类计数。
 * @param kinds - 当前维度可返回的分类。
 * @returns 是否仅包含已知的类型与非负整数。
 */
function validKindCounts(value: unknown, kinds: Set<string>): boolean {
  if (!isRecord(value)) return false
  for (const [kind, count] of Object.entries(value)) {
    if (!kinds.has(kind) || typeof count !== 'number' || !Number.isSafeInteger(count) || count < 0)
      return false
  }
  return true
}

/**
 * 校验活动记录中的执行者，允许链路缺少父节点时返回空值。
 * @param actor - 服务端依据本条链路的父子关系找到的执行者。
 * @returns 是否可以在执行时间线上展示此归属。
 */
function validActivityActor(actor: unknown): boolean {
  return (
    actor === null ||
    (isRecord(actor) &&
      (actor.kind === 'agent' || actor.kind === 'workflow') &&
      typeof actor.span_id === 'string' &&
      /^[0-9a-f]{16}$/.test(actor.span_id) &&
      typeof actor.display_name === 'string' &&
      actor.display_name.length > 0)
  )
}

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
function validateEvaluationResponse(resource: string, data: JsonRecord): void {
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
  if (
    !child &&
    (!validEvaluationRun(data.run) ||
      encodeURIComponent(data.run.run_id) !== runId ||
      !validEvaluationMetrics(data.metrics))
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

/**
 * 校验集合及身份响应的基础结构，畸形响应不得被当作当前身份。
 * @param path - 同源 API 路径。
 * @param data - 未知 JSON 对象；不会用于决定后端授权。
 * @param method - HTTP 方法；只有集合 GET 要求 items，创建结果保留自己的契约。
 * @throws 响应缺少集合或身份字段时抛出中文异常。
 */
function validateResponse(path: string, data: JsonRecord, method: string): void {
  const resource = path.split('?', 1)[0]
  if (
    method === 'GET' &&
    (resource === '/evaluations/runs' || resource.startsWith('/evaluations/runs/'))
  ) {
    validateEvaluationResponse(resource, data)
    return
  }
  const collection =
    method === 'GET' &&
    ([
      '/organizations',
      '/documents',
      '/tasks',
      '/users',
      '/approvals',
      '/audit',
      '/admin/traces',
      '/admin/traces/owners',
    ].includes(resource) ||
      /^\/documents\/[^/]+\/(versions|grants)$/.test(path))
  if (
    (collection || 'items' in data) &&
    (!Array.isArray(data.items) || !data.items.every(isRecord))
  ) {
    throw new Error('资料列表响应异常，请刷新后重试')
  }
  if (
    (resource === '/admin/traces' || resource === '/admin/traces/owners') &&
    data.next_cursor !== null &&
    typeof data.next_cursor !== 'string'
  ) {
    throw new Error('轨迹分页响应异常，请刷新后重试')
  }
  if (resource === '/admin/traces/owners') {
    if (
      !Array.isArray(data.items) ||
      !data.items.every(
        (owner) =>
          isRecord(owner) &&
          typeof owner.id === 'string' &&
          typeof owner.username === 'string' &&
          typeof owner.display_name === 'string',
      )
    ) {
      throw new Error('用户筛选列表响应异常，请刷新后重试')
    }
    return
  }
  const runList = /^\/admin\/traces\/[^/]+\/runs$/.test(resource)
  const nodePage = /^\/admin\/traces\/[^/]+\/runs\/([0-9a-f]{32})\/(spans|activities)$/.exec(
    resource,
  )
  if (runList || nodePage) {
    if (!Array.isArray(data.items)) throw new Error('调用链列表响应异常，请刷新后重试')
    const query = new URLSearchParams(path.split('?')[1])
    const requestedKind = query.get('kind')
    const requestedCategory = query.get('category')
    const requestedTraceId = query.get('trace_id')
    const requestedDiagnostic = query.get('diagnostic')
    for (const item of data.items) {
      if (
        !isRecord(item) ||
        typeof item.trace_id !== 'string' ||
        !/^[0-9a-f]{32}$/.test(item.trace_id) ||
        !['running', 'ok', 'error', 'interrupted'].includes(String(item.status)) ||
        typeof item.started_at !== 'string' ||
        !Number.isFinite(Date.parse(item.started_at)) ||
        (item.ended_at !== null && !Number.isFinite(Date.parse(String(item.ended_at)))) ||
        (runList &&
          ((requestedTraceId !== null && item.trace_id !== requestedTraceId) ||
            !Number.isInteger(item.run_version) ||
            !validKindCounts(item.kind_counts, spanKinds) ||
            !validKindCounts(item.category_counts, activityCategories) ||
            !validKindCounts(item.diagnostic_counts, diagnosticCategories) ||
            !Number.isInteger(item.span_count) ||
            !Number.isInteger(item.failed_count) ||
            !Number.isInteger(item.input_tokens) ||
            !Number.isInteger(item.output_tokens))) ||
        (nodePage &&
          (typeof item.span_id !== 'string' ||
            typeof item.name !== 'string' ||
            item.trace_id !== nodePage[1] ||
            (nodePage[2] === 'activities' && !validActivityActor(item.actor)) ||
            typeof item.display_name !== 'string' ||
            typeof item.kind !== 'string' ||
            !spanKinds.has(item.kind) ||
            typeof item.category !== 'string' ||
            !activityCategories.has(item.category) ||
            (item.category !== item.kind &&
              !(item.kind === 'CHAIN' && ['CONTEXT', 'GUARDRAIL'].includes(item.category))) ||
            (requestedCategory !== null && item.category !== requestedCategory) ||
            (requestedKind !== null && item.kind !== requestedKind) ||
            !isRecord(item.attributes) ||
            !validDiagnostics(item.diagnostics, requestedDiagnostic) ||
            (item.parent_span_id !== null && typeof item.parent_span_id !== 'string') ||
            (item.error_type !== null && typeof item.error_type !== 'string') ||
            (item.duration_ms !== null && typeof item.duration_ms !== 'number')))
      )
        throw new Error('调用链节点响应异常，请刷新后重试')
    }
    if (
      (data.next_cursor !== null && typeof data.next_cursor !== 'string') ||
      (nodePage &&
        (typeof data.observed_at !== 'string' || !Number.isFinite(Date.parse(data.observed_at))))
    )
      throw new Error('调用链分页响应异常，请刷新后重试')
  }
  if (
    /^\/admin\/traces\/[^/]+$/.test(resource) &&
    (!isRecord(data.trace) ||
      typeof data.trace.id !== 'string' ||
      resource !== '/admin/traces/' + encodeURIComponent(data.trace.id) ||
      typeof data.trace.question !== 'string' ||
      !Array.isArray(data.events) ||
      !data.events.every(isRecord) ||
      typeof data.has_more_events !== 'boolean')
  ) {
    throw new Error('轨迹详情响应异常，请刷新后重试')
  }
  const identity = path === '/auth/login' ? data.user : path === '/auth/me' ? data : null
  if (path === '/auth/login' || path === '/auth/me') {
    const capabilities = isRecord(identity) ? identity.capabilities : null
    if (
      !isRecord(identity) ||
      typeof identity.id !== 'string' ||
      !Array.isArray(identity.roles) ||
      !identity.roles.every((role) => typeof role === 'string') ||
      !Array.isArray(identity.memberships) ||
      !identity.memberships.every(
        (member) =>
          isRecord(member) && typeof member.node_id === 'string' && typeof member.role === 'string',
      ) ||
      !['is_boss', 'is_leader', 'must_change'].every((key) => typeof identity[key] === 'boolean') ||
      typeof identity.role_level !== 'number' ||
      !isRecord(capabilities) ||
      !['system', 'write', 'review', 'permissions'].every(
        (key) => typeof capabilities[key] === 'boolean',
      )
    ) {
      throw new Error('登录身份响应异常，请重新登录')
    }
  }
}

/**
 * 从当前环境的非 HttpOnly CSRF Cookie 读取令牌；不读取会话 Cookie。
 * @param mode - 服务端返回的运行环境名（production / test）。
 * @returns 当前环境的 CSRF 值；不存在时返回空串，由服务端拒绝写入。
 */
function readCsrf(mode: string): string {
  const value = document.cookie
    .split('; ')
    .find((cookie) => cookie.startsWith('ecom_csrf_' + mode + '='))
    ?.split('=')
    .slice(1)
    .join('=')
  try {
    return decodeURIComponent(value || '')
  } catch {
    return ''
  }
}

/**
 * 调用同源 /api，并以未知值解析响应；写请求带 CSRF，401 清除页面资料。
 * @param path - 业务路径，身份、企业及权限由后端会话决定。
 * @param mode - Cookie 所属环境。
 * @param onUnauthorized - 旧会话失效时清除内存中的答案、资料和表单。
 * @param method - HTTP 方法；默认只读 GET。
 * @param payload - JSON 可序列化输入或上传 FormData，不允许携带私有模型凭据。
 * @param signal - 所属账号会话的取消信号，使旧身份的响应失效。
 * @returns 通过基础结构校验的契约响应；字段类型来自 types.ts。
 * @throws 网络、JSON、身份或业务拒绝均转为中文错误，不输出服务端堆栈。
 */
export async function requestApi<T>(
  path: string,
  mode: string,
  onUnauthorized: () => void,
  method = 'GET',
  payload?: unknown,
  signal?: AbortSignal,
): Promise<T> {
  const headers: Record<string, string> = {}
  if (method !== 'GET') headers['X-CSRF-Token'] = readCsrf(mode)
  if (payload && !(payload instanceof FormData)) headers['Content-Type'] = 'application/json'
  let response: Response
  try {
    response = await fetch('/api' + path, {
      method,
      signal,
      credentials: 'same-origin',
      headers,
      body: payload ? (payload instanceof FormData ? payload : JSON.stringify(payload)) : undefined,
    })
  } catch {
    signal?.throwIfAborted()
    throw new Error('暂时无法连接工作台，请稍后重试')
  }
  signal?.throwIfAborted()
  if (response.status === 401) onUnauthorized()
  let data: unknown
  try {
    data = await response.json()
  } catch {
    signal?.throwIfAborted()
    throw new Error('服务响应异常，请稍后重试')
  }
  if (!isRecord(data)) throw new Error('服务响应异常，请稍后重试')
  if (!response.ok) {
    throw new Error(typeof data.message === 'string' ? data.message : '操作未完成，请稍后重试')
  }
  signal?.throwIfAborted()
  validateResponse(path, data, method)
  return data as T
}
