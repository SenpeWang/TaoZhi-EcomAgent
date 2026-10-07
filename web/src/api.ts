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

/**
 * 校验集合及身份响应的基础结构，畸形响应不得被当作当前身份。
 * @param path - 同源 API 路径。
 * @param data - 未知 JSON 对象；不会用于决定后端授权。
 * @param method - HTTP 方法；只有集合 GET 要求 items，创建结果保留自己的契约。
 * @throws 响应缺少集合或身份字段时抛出中文异常。
 */
function validateResponse(path: string, data: JsonRecord, method: string): void {
  const collection =
    method === 'GET' &&
    (['/organizations', '/documents', '/tasks', '/users', '/approvals', '/audit'].includes(path) ||
      /^\/documents\/[^/]+\/(versions|grants)$/.test(path))
  if (
    (collection || 'items' in data) &&
    (!Array.isArray(data.items) || !data.items.every(isRecord))
  ) {
    throw new Error('资料列表响应异常，请刷新后重试')
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
      !['is_boss', 'is_leader', 'system_admin', 'must_change'].every(
        (key) => typeof identity[key] === 'boolean',
      ) ||
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
 * @param mode - 服务端返回的演示、测试或正式环境名。
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
 * 调用同源 /api/v2，并以未知值解析响应；写请求带 CSRF，401 清除页面资料。
 * @param path - 业务路径，身份、企业及权限由后端会话决定。
 * @param mode - Cookie 所属环境。
 * @param onUnauthorized - 旧会话失效时清除内存中的答案、资料和表单。
 * @param method - HTTP 方法；默认只读 GET。
 * @param payload - JSON 可序列化输入或上传 FormData，不允许携带私有模型凭据。
 * @returns 通过基础结构校验的契约响应；字段类型来自 types.ts。
 * @throws 网络、JSON、身份或业务拒绝均转为中文错误，不输出服务端堆栈。
 */
export async function requestApi<T>(
  path: string,
  mode: string,
  onUnauthorized: () => void,
  method = 'GET',
  payload?: unknown,
): Promise<T> {
  const headers: Record<string, string> = {}
  if (method !== 'GET') headers['X-CSRF-Token'] = readCsrf(mode)
  if (payload && !(payload instanceof FormData)) headers['Content-Type'] = 'application/json'
  let response: Response
  try {
    response = await fetch('/api/v2' + path, {
      method,
      credentials: 'same-origin',
      headers,
      body: payload ? (payload instanceof FormData ? payload : JSON.stringify(payload)) : undefined,
    })
  } catch {
    throw new Error('暂时无法连接工作台，请稍后重试')
  }
  if (response.status === 401) onUnauthorized()
  let data: unknown
  try {
    data = await response.json()
  } catch {
    throw new Error('服务响应异常，请稍后重试')
  }
  if (!isRecord(data)) throw new Error('服务响应异常，请稍后重试')
  if (!response.ok) {
    throw new Error(typeof data.message === 'string' ? data.message : '操作未完成，请稍后重试')
  }
  validateResponse(path, data, method)
  return data as T
}
