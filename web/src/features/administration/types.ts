export interface Capabilities {
  system: boolean
  write: boolean
  review: boolean
  permissions: boolean
}

/** 系统管理员起始角色层级。 */
export const ADMIN_ROLE_LEVEL = 4

/**
 * 判断用户是否具备系统管理员层级。
 * @param user - 含角色层级的用户对象。
 * @returns 达到管理员层级时返回 true。
 */
export function isAdmin(user: { role_level?: number } | null | undefined): boolean {
  return (user?.role_level ?? 0) >= ADMIN_ROLE_LEVEL
}

export interface Membership {
  node_id: string
  role: string
  name?: string
}

export interface User {
  id: string
  username: string
  display_name: string
  role_level: number
  is_boss: boolean
  is_leader: boolean
  roles: string[]
  must_change: boolean
  memberships: Membership[]
  capabilities: Capabilities
  active?: boolean
}

export interface ManagedUser {
  id: string
  username: string
  display_name: string
  system_admin?: boolean
  active?: boolean
  must_change?: boolean
  version?: number
  is_boss?: boolean
  is_leader?: boolean
  roles?: string[]
  memberships?: Membership[]
}

export interface Organization {
  id: string
  name: string
  kind: string
  parent_id: string | null
  active: boolean
}

export interface OrganizationTree extends Organization {
  label: string
  children: OrganizationTree[]
}

export interface ApprovalPayload {
  user_id?: string
  role?: string
  remove?: boolean
  node_id?: string
  scope?: string
  level?: number
  ai_allowed?: boolean
  download_allowed?: boolean
  can_download?: boolean
  can_write?: boolean
  effect?: string
  expires_at?: string | null
  revoke?: boolean
  reset_password?: boolean
  active?: boolean
  system_admin?: boolean
  name?: string
  expected_version?: number
  expected_acl?: number
}

export interface Approval {
  id: string
  kind: string
  label: string
  title?: string
  state: string
  target_id: string
  requested_by: string
  required_role: string
  payload: ApprovalPayload
  can_decide: boolean
}

export interface AuditEvent {
  id: number
  created_at: string
  action: string
  actor_id: string
  node_id: string
  level: number
  system: boolean
}

export interface RuntimeStatus {
  database: boolean
  worker_available: boolean
  counts: { state: string; count: number }[]
  model_configured: boolean
  business_configured: boolean
}

const administrationPages = ['权限审批', '组织与成员', '系统状态', '操作日志'] as const

export type AdministrationPage = (typeof administrationPages)[number]

/**
 * 判断当前导航是否进入组织、权限或系统维护页面。
 * @param page - 当前工作台页面名称。
 * @returns 属于企业管理工作区时返回 true。
 */
export function isAdministrationPage(page: string): page is AdministrationPage {
  return administrationPages.some((candidate) => candidate === page)
}
