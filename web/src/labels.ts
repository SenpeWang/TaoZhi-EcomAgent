/**
 * 将数字密级转为企业标准化中文标签。
 * @param level - 密级编号（1 公开 / 2 内部 / 3 机密）。
 * @returns 企业密级文案。
 */
export function levelText(level?: number): string {
  if (level === 1) return '公开 (L1)'
  if (level === 2) return '内部 (L2)'
  if (level === 3) return '机密 (L3)'
  return '待定'
}

/**
 * 将英文状态编码转为企业规范中文状态名称。
 * @param state - 服务端状态编码。
 * @returns 中文状态文本。
 */
export function stateText(state?: string): string {
  if (!state) return '未知'
  const labels: Record<string, string> = {
    draft: '草稿',
    parsing: '解析中',
    review: '待审核',
    published: '已发布',
    queued: '排队中',
    running: '进行中',
    completed: '已完成',
    failed: '失败',
    cancelled: '已取消',
    rejected: '已驳回',
    legacy: '历史归档',
    approved: '已通过',
    pending: '待审批',
    partial_or_failed: '执行异常',
  }
  return labels[state] || state
}

/**
 * 映射任务或文档状态的 Element Plus 标签色彩。
 * @param state - 服务端状态编码。
 * @returns 标签样式类型。
 */
export function stateTag(state: string): 'primary' | 'success' | 'warning' | 'danger' | 'info' {
  if (['published', 'completed', 'approved'].includes(state)) return 'success'
  if (['review', 'pending', 'running'].includes(state)) return 'warning'
  if (['failed', 'rejected', 'partial_or_failed'].includes(state)) return 'danger'
  return 'info'
}

const traceClock = new Intl.DateTimeFormat('zh-CN', {
  dateStyle: 'short',
  timeStyle: 'medium',
  hour12: false,
})

/**
 * 将执行记录的时间戳显示为浏览器本地时间。
 * @param value - 服务端时间戳。
 * @returns 时间文本或空值占位。
 */
export function traceTime(value?: string | null): string {
  if (!value) return '—'
  const time = new Date(value)
  return Number.isNaN(time.getTime()) ? '—' : traceClock.format(time)
}

/**
 * 根据任务类别显示入口名称，问答流程同时承载知识与经营提问。
 * @param kind - 任务类别。
 * @returns 入口中文名称。
 */
export function traceKind(kind: string): string {
  return kind === 'research' ? '选品调研' : '知识与经营问答'
}
