import type { Task, TaskResult } from './types'

const answerLabels: Record<string, string> = {
  internal_evidence: '内部资料答复',
  business_snapshot: '实时业务快照',
  source_excerpt: '授权原文摘录',
  public_reference: '官方公开参考',
  insufficient_evidence: '依据不足',
}

/**
 * 仅呈现已发布结果或当前审核人有权复核的答复。
 * @param task - 当前选中的服务端任务快照。
 * @returns 当前可读结果，无权限或尚未发布时为空。
 */
export function visibleTaskResult(task: Task | null): TaskResult | null {
  if (!task || task.restricted) return null
  return task.state === 'completed' || (task.state === 'review' && task.can_review)
    ? task.result || null
    : null
}

/**
 * 将问答结果与链路节点共用的依据代码翻译为业务名称。
 * @param value - 服务端记录的答复依据。
 * @returns 已知的依据名称，未记录时为空。
 */
export function answerBasisLabel(value: unknown): string {
  return typeof value === 'string' && Object.hasOwn(answerLabels, value) ? answerLabels[value] : ''
}

/**
 * 区分正文实际引用与检索候选，保留原数组的引用编号。
 * @param result - 含文档片段的问答结果。
 * @returns 非空的证据分组。
 */
export function citationGroups(result: TaskResult) {
  const entries = (result.citations || []).map((citation, index) => ({
    citation,
    number: index + 1,
  }))
  return [
    { label: '答复引用', entries: entries.filter(({ citation }) => citation.cited === true) },
    { label: '相关资料', entries: entries.filter(({ citation }) => citation.cited !== true) },
  ].filter((group) => group.entries.length)
}

/**
 * 校验公开资料链接，仅展示可安全打开的 HTTPS 官方来源。
 * @param url - 来源原始地址。
 * @returns 可打开的地址，非法地址返回空串。
 */
export function publicSourceUrl(url: string): string {
  try {
    const parsed = new URL(url)
    return parsed.protocol === 'https:' && !parsed.username && !parsed.password ? parsed.href : ''
  } catch {
    return ''
  }
}
