import type { Capabilities, User } from '../features/administration/types'

type PageAccess = 'member' | 'reviewer' | 'permissionManager' | 'systemManager' | 'auditor'

interface WorkspacePageDefinition {
  group: string
  description: string
  iconPaths: string[]
  access: PageAccess
}

const shieldIconPaths = ['M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z']

const workspacePages = {
  工作概览: {
    group: '业务工作台',
    description: '查看授权文档、分析任务和待审核事项。',
    iconPaths: ['M3 3h7v9H3zM14 3h7v5h-7zM14 12h7v9h-7zM3 16h7v5H3z'],
    access: 'member',
  },
  选品调研: {
    group: '业务工作台',
    description: '对比货源报价与渠道价格，形成选品分析报告。',
    iconPaths: ['M12 2a4 4 0 0 1 4 4v2a4 4 0 0 1-8 0V6a4 4 0 0 1 4-4z', 'M5 20a7 7 0 0 1 14 0'],
    access: 'member',
  },
  数据中心: {
    group: '业务工作台',
    description: '查看授权范围内的经营指标，查询业务数据。',
    iconPaths: ['M3 3v18h18', 'M18 17V9', 'M13 17V5', 'M8 17v-3'],
    access: 'member',
  },
  知识问答: {
    group: '业务工作台',
    description: '检索企业制度及业务资料，核对回答引用的原文。',
    iconPaths: ['M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z'],
    access: 'member',
  },
  知识文档: {
    group: '文档与任务',
    description: '按部门和密级管理企业知识文档。',
    iconPaths: ['M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z', 'M14 2v6h6'],
    access: 'member',
  },
  我的任务: {
    group: '文档与任务',
    description: '跟踪已提交的分析任务，查阅历史结论。',
    iconPaths: ['M9 11l3 3L22 4', 'M21 12v7a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11'],
    access: 'member',
  },
  团队审核: {
    group: '文档与任务',
    description: '复核知识问答的内容和来源，决定是否发布。',
    iconPaths: ['M17 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2', 'M9 7a4 4 0 1 0 0-8 4 4 0 0 0 0 8z'],
    access: 'reviewer',
  },
  轨迹中台: {
    group: '运行分析',
    description: '按提问查看每次执行中智能体的行动、工具调用和模型用量。',
    iconPaths: ['M4 5h9M8 12h12M4 19h10', 'M4 3v4M8 10v4M4 17v4'],
    access: 'member',
  },
  系统状态: {
    group: '运行分析',
    description: '查看数据库、后台任务服务与数据源的连接状态。',
    iconPaths: ['M22 12h-4l-3 9L9 3l-3 9H2'],
    access: 'systemManager',
  },
  测评启动: {
    group: '测评中心',
    description: '登记算法版本，选择数据集与评测口径发起真实调研，并跟踪任务进度。',
    iconPaths: ['M8 5H5v15h14V5h-3', 'M9 3h6v4H9z', 'M8 12l1 1 2-2M13 12h3M8 16h8'],
    access: 'member',
  },
  测评结果: {
    group: '测评中心',
    description: '查看评测概览与趋势、数据集原始内容、批次与基线，管理员可查审计留痕。',
    iconPaths: ['M3 3v18h18', 'M7 15l3-3 3 2 4-5'],
    access: 'member',
  },
  权限审批: {
    group: '组织与权限',
    description: '处理文档授权、密级调整及成员权限申请。',
    iconPaths: shieldIconPaths,
    access: 'permissionManager',
  },
  组织与成员: {
    group: '组织与权限',
    description: '维护部门架构、成员账号及岗位角色。',
    iconPaths: [
      'M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2',
      'M9 7a4 4 0 1 0 0-8 4 4 0 0 0 0 8z',
      'M22 21v-2a4 4 0 0 0-3-3.87',
    ],
    access: 'systemManager',
  },
  操作日志: {
    group: '组织与权限',
    description: '查阅登录、文档读写和权限审批记录。',
    iconPaths: ['M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z', 'M9 13h6', 'M9 17h6'],
    access: 'auditor',
  },
  个人中心: {
    group: '账号设置',
    description: '查看所属部门和权限范围，管理登录密码。',
    iconPaths: ['M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2', 'M12 3a4 4 0 1 0 0 8 4 4 0 0 0 0-8z'],
    access: 'member',
  },
} satisfies Record<string, WorkspacePageDefinition>

export type WorkspacePage = keyof typeof workspacePages
export { workspacePages }

export interface NavigationGroup {
  title: string
  nested?: boolean
  items: { page: WorkspacePage; iconPaths: string[]; badge?: number }[]
}

const nestedNavigationGroups = new Set(['测评中心'])

/**
 * 判断页面是否属于测评中心。
 * @param page - 工作台页面名称。
 * @returns 是否为测评启动或测评结果页面。
 */
export function isEvaluationPage(page: WorkspacePage): boolean {
  return workspacePages[page].group === '测评中心'
}

/**
 * 按实际身份组织菜单，并为可办理的事项附上数量；测评中心渲染为带二级按钮的目录。
 * @param user - 当前登录成员。
 * @param capabilities - 服务端授予的业务权限。
 * @param pendingApprovals - 待处理的权限申请数。
 * @param pendingReviews - 待审核的分析任务数。
 * @returns 保留展示次序的可访问菜单分组。
 */
export function workspaceNavigation(
  user: User | null,
  capabilities: Capabilities,
  pendingApprovals: number,
  pendingReviews: number,
): NavigationGroup[] {
  if (!user) return []
  if (user.must_change) {
    return [{ title: '账号安全', items: [{ page: '个人中心', iconPaths: shieldIconPaths }] }]
  }
  const allowed: Record<PageAccess, boolean> = {
    member: true,
    reviewer: capabilities.review,
    permissionManager: capabilities.permissions,
    systemManager: capabilities.system,
    auditor: capabilities.system || capabilities.permissions,
  }
  const badges: Partial<Record<WorkspacePage, number>> = {
    权限审批: pendingApprovals,
    团队审核: pendingReviews,
  }
  const groups = new Map<string, NavigationGroup>()
  for (const page of Object.keys(workspacePages) as WorkspacePage[]) {
    const { access, group, iconPaths } = workspacePages[page]
    if (!allowed[access]) continue
    const section: NavigationGroup = groups.get(group) ?? {
      title: group,
      nested: nestedNavigationGroups.has(group),
      items: [],
    }
    section.items.push({ page, iconPaths, badge: badges[page] })
    groups.set(group, section)
  }
  return Array.from(groups.values())
}
