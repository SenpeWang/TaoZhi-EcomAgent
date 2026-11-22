/** 从未知网络响应读取数据；角色判断仍由服务端统一执行。 */
export type JsonRecord = Record<string, unknown>

/**
 * 判断响应是否为对象，拒绝空值和数组作为业务对象。
 * @param value - 尚未验证的 JSON 值。
 * @returns 可安全读取字段的对象判断结果。
 */
export function isRecord(value: unknown): value is JsonRecord {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

/**
 * 检查集合响应只包含对象记录。
 * @param data - 当前响应正文。
 * @param required - 请求是否要求返回集合。
 * @throws 缺少记录集合或包含畸形记录时抛出中文异常。
 */
export function validateItems(data: JsonRecord, required = false): void {
  if (
    (required || 'items' in data) &&
    (!Array.isArray(data.items) || !data.items.every(isRecord))
  ) {
    throw new Error('资料列表响应异常，请刷新后重试')
  }
}
