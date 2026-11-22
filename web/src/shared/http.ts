import { isRecord, type JsonRecord } from './response.ts'

interface RequestOptions {
  mode: string
  onUnauthorized: () => void
  method: string
  payload?: unknown
  signal?: AbortSignal
  validate: (path: string, data: JsonRecord, method: string) => void
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
 * @param options - 会话、请求方法及业务校验配置。
 * @returns 通过基础结构校验的契约响应；由调用方检查业务契约。
 * @throws 网络、JSON、身份或业务拒绝均转为中文错误，不输出服务端堆栈。
 */
export async function requestJson<T>(path: string, options: RequestOptions): Promise<T> {
  const { mode, onUnauthorized, method, payload, signal, validate } = options
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
  validate(path, data, method)
  return data as T
}
