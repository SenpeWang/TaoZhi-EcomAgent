import { isRecord, validateItems, type JsonRecord } from '../../shared/response.ts'

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
 * 检查轨迹、调用链节点和发起人分页对应当前请求。
 * @param path - 含筛选参数的轨迹 API 路径。
 * @param resource - 去除查询参数的资源路径。
 * @param data - 未验证的响应正文。
 * @param method - HTTP 方法。
 * @throws 轨迹身份、节点或分页格式不符时抛出中文异常。
 */
export function validateTraceResponse(
  path: string,
  resource: string,
  data: JsonRecord,
  method: string,
): void {
  validateItems(
    data,
    method === 'GET' && (resource === '/admin/traces' || resource === '/admin/traces/owners'),
  )
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
}
