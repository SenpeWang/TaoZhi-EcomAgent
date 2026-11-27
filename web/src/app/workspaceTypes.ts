import type { User } from '../features/administration/types'

/**
 * 快捷登录账号，用于本地演示环境在登录页直接选择身份。
 * username、display_name、role 为账号资料；password 仅在开放快捷登录时由服务端下发。
 */
export interface QuickAccount {
  username: string
  display_name: string
  role: string
  password?: string
}

/**
 * 工作台环境信息，由服务端在登录前返回。
 * name 为企业名称；mode 为运行环境（production / test）；
 * tenant_code 为租户编码；quick_accounts 为可选的快捷登录账号列表。
 */
export interface WorkspaceInfo {
  name: string
  mode: string
  tenant_code: string
  quick_accounts?: QuickAccount[]
}

/**
 * 工作台首页的统计概览。
 * documents、tasks、review、completed 分别为文档、任务、待审核与已完成数量；
 * teams 为可见团队名称；model_ready 与 business_ready 表示模型与业务数据是否就绪。
 */
export interface Dashboard {
  documents: number
  tasks: number
  review: number
  completed: number
  teams: string[]
  model_ready: boolean
  business_ready: boolean
}

/**
 * 登录成功后的响应，user 为当前成员的身份、角色与授权信息。
 */
export interface LoginResponse {
  user: User
}
