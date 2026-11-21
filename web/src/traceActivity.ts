import type { DiagnosticCategory, TraceActivity, TraceDiagnostic } from './types'

export const diagnosticCategoryLabels: Record<DiagnosticCategory, string> = {
  model_api: '模型 API',
  tool: '工具',
  retrieval: '检索',
  context: '上下文',
  permission: '权限',
  storage: '存储',
  workflow: '工作流',
}

export const diagnosticOutcomeLabels: Record<TraceDiagnostic['outcome'], string> = {
  failed: '失败',
  retry: '重试',
  fallback: '降级处理',
  skipped: '跳过',
}

const toolActions: Record<string, string> = {
  get_current_time: '读取当前时间',
  search_web: '搜索公开网页',
  fetch_web_page: '读取网页正文',
  search_1688_wholesale: '查询 1688 批发报价',
  search_amazon: '查询亚马逊竞品',
  stock: '查询商品库存',
  orders: '查询订单',
}
const agentActions: Record<string, string> = {
  'research.orchestrator': '协调本轮调研',
  'research.subagent_worker': '执行调研子任务',
  'research.synthesizer': '汇总证据并撰写报告',
  'rag.specialist': '核验问答资料',
}
const toolOutcomes: Record<string, string> = {
  error: '工具执行失败',
  empty: '无匹配结果',
  timeout: '工具超时',
  cancelled: '工具已取消',
}
const retrievalDecisions: Record<string, string> = {
  retry_weak_evidence: '首轮证据不足，进入补查',
  retry_low_confidence: '重排置信度偏低，进入补查',
  sufficient_evidence: '进入授权上下文汇集',
  run_specialists: '转入专家核验',
  no_authorized_evidence: '缺少可用授权证据，跳过专家核验',
  external_processing_blocked: '未获外发许可，跳过专家核验',
  no_finance_evidence: '缺少授权财务依据，跳过专家核验',
}

/**
 * 根据实际执行的节点与工具名称生成动作描述，并保留采集到的技术标识。
 * @param entry - 一次真实执行的活动。
 * @returns 在时间线上直接展示的动作及目标。
 */
export function activityAction(entry: TraceActivity): { title: string; target?: string } {
  const attrs = entry.attributes
  switch (entry.kind) {
    case 'AGENT':
      return {
        title: Object.hasOwn(agentActions, entry.name)
          ? agentActions[entry.name]
          : '执行智能体任务',
      }
    case 'TOOL': {
      const tool = attrs['tool.name']
      if (typeof tool !== 'string') return { title: '调用工具' }
      return {
        title: Object.hasOwn(toolActions, tool) ? toolActions[tool] : '调用工具',
        target: tool,
      }
    }
    case 'LLM':
      return { title: '调用模型', target: String(attrs['llm.model_name'] ?? '') }
    case 'EMBEDDING':
      return { title: entry.display_name, target: String(attrs['embedding.model_name'] ?? '') }
    case 'RERANKER':
      return { title: entry.display_name, target: String(attrs['reranker.model_name'] ?? '') }
    default:
      return { title: entry.display_name }
  }
}

/**
 * 整理本次行动已采集的结果，区分未命中、失败、路由决定及实际 Token 用量。
 * @param entry - 时间线的一行活动。
 * @returns 用于主视图的结果文本。
 */
export function activityResult(entry: TraceActivity): string {
  const attrs = entry.attributes
  const details: string[] = []
  const toolStatus = attrs['tool.status']
  if (entry.kind === 'TOOL') {
    const dispatched = attrs['tool.dispatched']
    if (dispatched === false) {
      if (toolStatus === 'timeout') return '未派发 · 派发前已超时'
      if (toolStatus === 'cancelled') return '未派发 · 派发前已取消'
      return entry.error_type ? '未派发 · ' + entry.error_type : '未派发'
    }
    if (dispatched === true) details.push('已派发')
    const outcome =
      typeof toolStatus === 'string' && Object.hasOwn(toolOutcomes, toolStatus)
        ? toolOutcomes[toolStatus]
        : undefined
    if (outcome) details.push(outcome)
    const count = attrs['tool.result_count']
    if (typeof count === 'number' && (entry.status === 'ok' || count > 0)) {
      details.push('返回 ' + count + ' 条')
    }
  }
  if (entry.category === 'CONTEXT') {
    for (const [key, label] of [
      ['context.evidence_count', '份证据'],
      ['context.observation_count', '条观测'],
      ['context.tool_count', '项可用工具'],
    ]) {
      const count = attrs[key]
      if (typeof count === 'number') details.push(count + ' ' + label)
    }
    const input = attrs['context.input_chars']
    const output = attrs['context.output_chars']
    if (typeof output === 'number')
      details.push(typeof input === 'number' ? input + ' → ' + output + ' 字符' : output + ' 字符')
  }
  if (entry.kind === 'LLM') {
    const prompt = attrs['llm.token_count.prompt']
    const completion = attrs['llm.token_count.completion']
    if (typeof prompt === 'number') details.push('输入 ' + prompt + ' Token')
    if (typeof completion === 'number') details.push('输出 ' + completion + ' Token')
    const total = attrs['llm.token_count.total']
    if (typeof total === 'number' && !details.length) details.push('共 ' + total + ' Token')
  }
  if (entry.kind === 'RETRIEVER' && typeof attrs['retrieval.result_count'] === 'number') {
    details.push('检索到 ' + attrs['retrieval.result_count'] + ' 条')
  }
  if (entry.name === 'pipeline.retrieval_retry') {
    const searches = attrs['rag.retrieval_search_count']
    if (searches === 1) details.push('未执行第二次检索')
    if (searches === 2) details.push('已执行第二次检索')
  }
  const decision = attrs['rag.retrieval_decision']
  if (typeof decision === 'string' && Object.hasOwn(retrievalDecisions, decision)) {
    details.push(retrievalDecisions[decision])
  }
  if (entry.name === 'research.orchestrator') {
    switch (attrs['research.next_action']) {
      case 'dispatch_subagents': {
        const count = attrs['research.dispatched_count']
        details.push(typeof count === 'number' ? '已派发 ' + count + ' 项子任务' : '已派发子任务')
        break
      }
      case 'synthesizer':
        details.push('转入报告撰写')
        break
      case 'final_node':
        details.push('进入归档')
        break
    }
  }
  if (
    entry.error_type &&
    !(
      entry.kind === 'TOOL' &&
      toolStatus === entry.error_type &&
      Object.hasOwn(toolOutcomes, toolStatus)
    )
  ) {
    details.push(entry.error_type)
  }
  return details.join(' · ')
}

/**
 * 找到出现在列表中的同名执行者，用真实的 Span ID 区分并行实例。
 * @param items - 当前已载入的行动记录。
 * @returns 同名智能体的 Span ID 及其可区分的编号前缀。
 */
export function actorInstanceLabels(items: TraceActivity[]): Map<string, string> {
  const byName = new Map<string, Set<string>>()
  for (const { actor } of items) {
    if (actor?.kind !== 'agent') continue
    const ids = byName.get(actor.display_name) ?? new Set<string>()
    ids.add(actor.span_id)
    byName.set(actor.display_name, ids)
  }
  const labels = new Map<string, string>()
  for (const ids of byName.values()) {
    if (ids.size < 2) continue
    let length = 6
    while (length < 16 && new Set(Array.from(ids, (id) => id.slice(0, length))).size !== ids.size)
      length += 2
    for (const id of ids) labels.set(id, id.slice(0, length))
  }
  return labels
}
