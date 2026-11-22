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

const preciseClock = new Intl.DateTimeFormat('zh-CN', {
  year: 'numeric',
  month: '2-digit',
  day: '2-digit',
  hour: '2-digit',
  minute: '2-digit',
  second: '2-digit',
  fractionalSecondDigits: 3,
  hour12: false,
})

/**
 * 显示毫秒级本地时间以定位同一步骤中的多次失败。
 * @param timestamp - 已通过接口边界校验的时间戳。
 * @returns 包含日期的诊断时间。
 */
export function preciseTraceTime(timestamp: string): string {
  return preciseClock.format(new Date(timestamp))
}

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

/**
 * 清洗阶段文本中的机器前缀，仅保留正式可读的执行轨迹说明。
 * @param raw - 原始阶段描述。
 * @returns 格式化后的轨迹说明。
 */
export function formatStage(raw?: string | null): string {
  const txt = (raw || '').trim()
  if (!txt) return '等待处理'
  return txt.replace(/^(\[[a-zA-Z0-9_]+\]\s*)+/, '').trim() || txt
}

const roleLabels: Record<string, string> = {
  employee: '普通员工',
  leader: '部门主管',
  boss: '企业负责人',
  admin: '系统管理员',
}

/**
 * 显示企业岗位的中文称谓。
 * @param role - 岗位代码。
 * @returns 对应的中文岗位名称。
 */
export function roleName(role: string): string {
  return roleLabels[role] ?? role
}
