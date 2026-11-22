import { isRecord, type JsonRecord } from '../shared/response.ts'

/**
 * 检查服务端返回的登录身份，拒绝不完整的账号及能力信息。
 * @param path - 已请求的会话接口完整路径。
 * @param data - 未验证的会话响应。
 * @throws 账号或能力记录不完整时抛出中文异常。
 */
export function validateSessionResponse(path: string, data: JsonRecord): void {
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
      !['is_boss', 'is_leader', 'must_change'].every((key) => typeof identity[key] === 'boolean') ||
      typeof identity.role_level !== 'number' ||
      !isRecord(capabilities) ||
      !['system', 'write', 'review', 'permissions'].every(
        (key) => typeof capabilities[key] === 'boolean',
      )
    ) {
      throw new Error('登录身份响应异常，请重新登录')
    }
  }
}
