/**
 * 当前成员拥有的业务权限标记，由服务端按角色下发。
 * system 为系统管理、write 为文档写入、review 为团队审核、permissions 为权限审批。
 */
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

/**
 * 成员在组织节点上的任职关系。
 * node_id 为所属部门节点；role 为岗位角色；name 为可选的节点名称。
 */
export interface Membership {
  node_id: string
  role: string
  name?: string
}

/**
 * 登录成员的完整身份信息。
 * 含账号资料与角色层级，is_boss、is_leader 标识老板与组长身份，
 * must_change 表示首次登录必须改密，memberships 为部门任职，capabilities 为权限。
 */
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

/**
 * 成员管理列表中的人员摘要。
 * system_admin、active、must_change 为管理员、启用与改密状态；
 * version 用于并发更新校验；其余字段为可选的资料与任职信息。
 */
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

/**
 * 组织架构节点。
 * kind 区分企业、部门等节点类型；parent_id 指向上级节点，为 null 时是根节点；
 * active 表示该节点是否启用。
 */
export interface Organization {
  id: string
  name: string
  kind: string
  parent_id: string | null
  active: boolean
}

/**
 * 带层级结构的组织节点，在 Organization 基础上增加展示标签 label 与子节点 children。
 */
export interface OrganizationTree extends Organization {
  label: string
  children: OrganizationTree[]
}

/**
 * 审批单中随类型变化的具体参数。
 * 覆盖成员角色调整、文档授权、密级与有效期、账号重置等字段；
 * 未使用的字段按审批类型忽略。
 */
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

/**
 * 待办审批单。
 * kind 为审批类型，state 为处理状态，target_id 为作用对象编号；
 * payload 为随类型变化的申请参数；can_decide 表示当前用户能否处理。
 */
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

/**
 * 操作审计记录。
 * action 为动作编码；actor_id 为操作人；node_id 为作用节点，level 为密级；
 * system 表示该动作由系统自动执行。
 */
export interface AuditEvent {
  id: number
  created_at: string
  action: string
  actor_id: string
  node_id: string
  level: number
  system: boolean
}

/**
 * 系统运行状态。
 * database 与 worker_available 表示数据库和后台任务服务是否可用；
 * counts 为任务状态计数；model_configured 与 business_configured 表示模型与业务数据源是否配置。
 */
export interface RuntimeStatus {
  database: boolean
  worker_available: boolean
  counts: { state: string; count: number }[]
  model_configured: boolean
  business_configured: boolean
}

const administrationPages = ['权限审批', '组织与成员', '系统状态', '操作日志'] as const

/**
 * 企业管理工作台页面名称的联合类型，取值来自 administrationPages。
 */
export type AdministrationPage = (typeof administrationPages)[number]

/**
 * 判断当前导航是否进入组织、权限或系统维护页面。
 * @param page - 当前工作台页面名称。
 * @returns 属于企业管理工作区时返回 true。
 */
export function isAdministrationPage(page: string): page is AdministrationPage {
  return administrationPages.some((candidate) => candidate === page)
}
