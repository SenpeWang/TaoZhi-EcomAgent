import type { ActivityCategory, SpanKind, SpanStatus, TraceRun, TraceSpan } from './types'

export const spanStatusLabels: Record<SpanStatus, string> = {
  running: '执行中',
  ok: '成功',
  error: '失败',
  interrupted: '已中断',
}

export const spanKindLabels: Record<SpanKind, string> = {
  LLM: '模型调用',
  TOOL: '工具调用',
  RETRIEVER: '知识检索',
  EMBEDDING: '向量化',
  RERANKER: '重排',
  AGENT: '智能体',
  CHAIN: '链路节点',
  INTERNAL: '内部操作',
}

export const activityCategoryLabels: Record<ActivityCategory, string> = {
  TOOL: spanKindLabels.TOOL,
  CONTEXT: '上下文处理',
  LLM: spanKindLabels.LLM,
  AGENT: spanKindLabels.AGENT,
  CHAIN: '流程编排',
  RETRIEVER: spanKindLabels.RETRIEVER,
  EMBEDDING: spanKindLabels.EMBEDDING,
  RERANKER: spanKindLabels.RERANKER,
  GUARDRAIL: '权限与校验',
  INTERNAL: spanKindLabels.INTERNAL,
}

const identityAttributes: Partial<Record<SpanKind, string>> = {
  LLM: 'llm.model_name',
  TOOL: 'tool.name',
  AGENT: 'agent.name',
  EMBEDDING: 'embedding.model_name',
  RERANKER: 'reranker.model_name',
}

const kindFields: Record<SpanKind, Record<string, string>> = {
  LLM: {
    'llm.model_name': '模型名称',
    'llm.endpoint_id': '调用端点',
    'llm.token_count.prompt': '输入 Token',
    'llm.token_count.completion': '输出 Token',
    'llm.token_count.total': '总 Token',
  },
  TOOL: {
    'tool.name': '工具名称',
    'tool.dispatched': '已派发至工具',
    'tool.status': '工具结果状态',
    'tool.result_count': '返回条数',
  },
  RETRIEVER: { 'retrieval.result_count': '检索命中数' },
  EMBEDDING: { 'embedding.model_name': '向量模型' },
  RERANKER: { 'reranker.model_name': '重排模型' },
  AGENT: {},
  CHAIN: {},
  INTERNAL: {},
}

const executionFields: Record<string, string> = {
  'agent.name': '智能体名称',
  subagent_id: '子智能体执行 ID',
  'state.selected_agents': '选中智能体',
  'state.evidence': '证据条数',
  'state.findings': '发现条数',
  'state.missing': '待补充项',
  'state.business': '业务建议数',
  'state.answer_chars': '回答字符数',
  'state.model_calls': '流程记录的模型调用数',
  'state.hard_block': '触发阻断',
  'rag.retrieval_decision': '检索路由代码',
  'rag.retrieval_search_count': '已执行的文档检索次数',
  'context.evidence_count': '上下文证据条数',
  'context.observation_count': '工具观测条数',
  'context.tool_count': '可用工具数',
  'context.input_chars': '处理前字符数',
  'context.output_chars': '处理后字符数',
  'otel.span.kind': 'OTel SpanKind',
}

/**
 * 为节点名称补充尚未显示的模型、工具或智能体标识。
 * @param span - 当前调用节点。
 * @returns 与节点名称不同的已采集标识。
 */
export function spanSecondaryName(span: TraceSpan): string {
  const key = identityAttributes[span.kind]
  const value = key && span.attributes[key]
  return typeof value === 'string' && value !== span.display_name ? value : ''
}

/**
 * 按节点语义呈现真实采集到的属性，保留数值为零的用量与结果数。
 * @param span - 当前选中的调用节点。
 * @returns 以展示顺序排列的节点详情。
 */
export function spanFields(span: TraceSpan) {
  const fields = []
  for (const [key, label] of Object.entries({ ...kindFields[span.kind], ...executionFields })) {
    if (!Object.hasOwn(span.attributes, key)) continue
    const value = span.attributes[key]
    fields.push({ key, label, value: typeof value === 'boolean' ? (value ? '是' : '否') : value })
  }
  return fields
}

/** 节点在父子树中的位置及相对于整次执行的时间区间。 */
export interface WaterfallRow {
  span: TraceSpan
  depth: number
  hasChildren: boolean
  offset: number
  width: number
  duration: number | null
}

export type SpanView = 'tree' | 'latency' | 'issues'

/**
 * 以服务端的采集时间计算运行中执行的耗时；中断且无结束记录时保留未知。
 * @param run - 当前执行版本。
 * @param observedAt - 服务端最近一次观测的时间戳（毫秒）。
 * @returns 已记录的耗时或空值。
 */
export function runDuration(run: TraceRun, observedAt: number): number | null {
  const startedAt = Date.parse(run.started_at)
  const endedAt = run.ended_at ? Date.parse(run.ended_at) : observedAt
  if (!run.ended_at && (run.status !== 'running' || observedAt < startedAt)) return null
  return Math.max(0, endedAt - startedAt)
}

/**
 * 从已加载的节点定位耗时较长的子调用或执行异常。耗时包含该节点的子调用。
 * @param rows - 所选执行的完整瀑布图行（包括收起的分支）。
 * @param view - 管理员选择的分析视图。
 * @returns 保留相同时间轴位置的节点行。
 */
export function analysisRows(
  rows: WaterfallRow[],
  view: Exclude<SpanView, 'tree'>,
): WaterfallRow[] {
  const matchingRows: WaterfallRow[] = []
  for (const row of rows) {
    const { span } = row
    if (view === 'issues') {
      if (span.status === 'error' || span.status === 'interrupted') matchingRows.push(row)
    } else if (span.parent_span_id !== null && span.duration_ms !== null) {
      matchingRows.push(row)
    }
  }
  return view === 'latency' ? matchingRows.sort(compareDurationDescending) : matchingRows
}

/**
 * 将记录了完整时长的节点按耗时降序排列。
 * @param first - 当前节点。
 * @param second - 待比较节点。
 * @returns 两个节点耗时的逆序差值。
 */
function compareDurationDescending(first: WaterfallRow, second: WaterfallRow): number {
  return (second.span.duration_ms ?? 0) - (first.span.duration_ms ?? 0)
}

/**
 * 按工具调用的时间尺度显示延迟。
 * @param ms - 毫秒耗时，中断且没有结束记录时为空。
 * @returns 格式化后的耗时。
 */
export function formatDuration(ms: number | null): string {
  if (ms === null) return '未知'
  if (ms < 1) return ms === 0 ? '0 ms' : '<1 ms'
  if (ms < 1000) return Math.round(ms) + ' ms'
  if (ms < 60000) return Number((ms / 1000).toFixed(2)) + ' s'
  return Math.floor(ms / 60000) + ' 分 ' + Math.floor((ms % 60000) / 1000) + ' 秒'
}

/**
 * 将当前已加载的节点按父子关系排序，并以同一时钟轴定位并行区间。
 * @param run - 当前选择的执行。
 * @param spans - 该执行已加载的节点。
 * @param collapsed - 已收起的节点编号。
 * @param now - 本次刷新时刻，用于显示已观测到的运行时长。
 * @param flat - 筛选单一类型时按时间排列，保留节点上真实的父编号。
 * @returns 树形行与横轴的时间跨度。
 */
export function buildWaterfall(
  run: TraceRun,
  spans: TraceSpan[],
  collapsed: Set<string>,
  now: number,
  flat = false,
) {
  const start = Date.parse(run.started_at)
  let end = run.ended_at
    ? Date.parse(run.ended_at)
    : run.status === 'running'
      ? Math.max(start, now)
      : start
  const children = new Map<string | null, TraceSpan[]>()
  const ids = new Set<string>()
  for (const span of spans) ids.add(span.span_id)
  for (const span of spans) {
    const parent =
      !flat && span.parent_span_id && ids.has(span.parent_span_id) ? span.parent_span_id : null
    const branch = children.get(parent) ?? []
    branch.push(span)
    children.set(parent, branch)
    const observedEnd = span.ended_at ? Date.parse(span.ended_at) : Date.parse(span.started_at)
    end = Math.max(end, observedEnd, span.status === 'running' ? now : start)
  }
  for (const branch of children.values()) {
    branch.sort(compareStart)
  }
  const total = Math.max(0, end - start)
  const rows: WaterfallRow[] = []
  const stack: { span: TraceSpan; depth: number }[] = []
  const roots = children.get(null) ?? []
  for (let i = roots.length - 1; i >= 0; i--) stack.push({ span: roots[i], depth: 0 })
  while (stack.length) {
    const { span, depth } = stack.pop()!
    const descendants = children.get(span.span_id) ?? []
    const began = Date.parse(span.started_at)
    const elapsed =
      span.duration_ms ?? (span.status === 'running' ? Math.max(0, now - began) : null)
    const offset = Math.max(0, Math.min(100, ((began - start) / (total || 1)) * 100))
    const width = Math.max(0, Math.min(100 - offset, ((elapsed ?? 0) / (total || 1)) * 100))
    rows.push({
      span,
      depth,
      hasChildren: descendants.length > 0,
      offset,
      width,
      duration: elapsed,
    })
    if (!collapsed.has(span.span_id)) {
      for (let i = descendants.length - 1; i >= 0; i--) {
        stack.push({ span: descendants[i], depth: depth + 1 })
      }
    }
  }
  return { rows, total }
}

/**
 * 对同级节点按实际开始时间排序。
 * @param first - 第一个节点。
 * @param second - 第二个节点。
 * @returns 开始时间的差值。
 */
function compareStart(first: TraceSpan, second: TraceSpan): number {
  return Date.parse(first.started_at) - Date.parse(second.started_at)
}
