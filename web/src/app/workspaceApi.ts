import { requestJson } from '../shared/http.ts'
import { validateItems, type JsonRecord } from '../shared/response.ts'
import { validateEvaluationResponse } from '../features/observability/evaluationResponse.ts'
import { validateTraceResponse } from '../features/observability/traceResponse.ts'
import { validateSessionResponse } from './sessionResponse.ts'

/**
 * 为工作台请求选择负责检查响应的业务模块。
 * @param path - 含查询参数的同源 API 路径。
 * @param data - 未验证的 JSON 对象。
 * @param method - HTTP 方法。
 */
function validateWorkspaceResponse(path: string, data: JsonRecord, method: string): void {
  const resource = path.split('?', 1)[0]
  if (
    method === 'GET' &&
    (resource === '/evaluations/runs' ||
      resource.startsWith('/evaluations/runs/') ||
      resource.startsWith('/evaluations/metrics/') ||
      resource === '/evaluations/datasets' ||
      resource.startsWith('/evaluations/datasets/'))
  ) {
    validateEvaluationResponse(resource, data)
    return
  }
  if (resource === '/admin/traces' || resource.startsWith('/admin/traces/')) {
    validateTraceResponse(path, resource, data, method)
    return
  }
  const collection =
    method === 'GET' &&
    (['/organizations', '/documents', '/tasks', '/users', '/approvals', '/audit'].includes(
      resource,
    ) ||
      /^\/documents\/[^/]+\/(versions|grants)$/.test(path))
  validateItems(data, collection)
  validateSessionResponse(path, data)
}

/**
 * 通过当前会话调用工作台 API，并按所属业务校验响应。
 * @param path - 同源 API 路径。
 * @param mode - CSRF Cookie 所属环境。
 * @param onUnauthorized - 清除失效会话的回调。
 * @param method - HTTP 方法，默认读取。
 * @param payload - JSON 正文或上传表单。
 * @param signal - 随登录会话失效的取消信号。
 * @returns 通过业务契约检查的响应。
 */
export function requestApi<T>(
  path: string,
  mode: string,
  onUnauthorized: () => void,
  method = 'GET',
  payload?: unknown,
  signal?: AbortSignal,
): Promise<T> {
  return requestJson<T>(path, {
    mode,
    onUnauthorized,
    method,
    payload,
    signal,
    validate: validateWorkspaceResponse,
  })
}
