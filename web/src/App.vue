<template>
  <LoginPortal
    v-if="!user"
    v-model:username="loginForm.username"
    v-model:password="loginForm.password"
    :info="workspaceInfo"
    :busy="busy"
    @sign-in="signIn"
    @pick-account="pickAccount"
  />

  <div v-else class="app-shell">
    <aside class="sidebar">
      <div class="sidebar-brand">
        <div class="sidebar-logo-wrap">
          <img :src="robotUrl" alt="平台标识" class="brand-logo-img" />
        </div>
        <strong class="brand-title">淘智企业工作台</strong>
      </div>

      <div class="tenant-badge-box">
        <span class="tenant-name-text" :title="workspaceInfo.name">
          {{ workspaceInfo.name || '企业工作空间' }}
        </span>
        <span class="tenant-code">
          {{ workspaceInfo.tenant_code || 'default' }}
        </span>
      </div>

      <nav class="sidebar-nav" aria-label="主导航">
        <template v-for="group in navigationGroups" :key="group.title">
          <div class="nav-group-label" :class="{ 'nav-group-directory': group.nested }">
            {{ group.title }}
          </div>
          <button
            v-for="item in group.items"
            :key="item.page"
            type="button"
            class="nav-item-btn"
            :class="{ active: currentPage === item.page, 'nav-item-child': group.nested }"
            :aria-current="currentPage === item.page ? 'page' : undefined"
            @click="navigate(item.page)"
          >
            <svg
              class="nav-svg-icon"
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              stroke-width="1.8"
              stroke-linecap="round"
              stroke-linejoin="round"
            >
              <path v-for="path in item.iconPaths" :key="path" :d="path" />
            </svg>
            <span class="nav-label">{{ item.page }}</span>
            <span v-if="item.badge" class="nav-badge-count">{{ item.badge }}</span>
          </button>
        </template>
      </nav>

      <div class="sidebar-footer">
        <div class="sidebar-security-chip">
          <svg
            class="nav-svg-icon"
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            stroke-width="1.8"
          >
            <path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z" />
          </svg>
          <span>数据权限：{{ clearanceShortText }}</span>
        </div>
      </div>
    </aside>

    <div class="main-shell">
      <header class="topbar">
        <div class="topbar-breadcrumb">
          <span class="bc-platform">{{ workspaceInfo.name || '企业工作台' }}</span>
          <span class="bc-slash">/</span>
          <span class="bc-current">{{ breadcrumbText }}</span>
        </div>

        <select
          class="mobile-navigation"
          aria-label="工作台导航"
          :value="currentPage"
          @change="navigateFromSelect"
        >
          <optgroup v-for="group in navigationGroups" :key="group.title" :label="group.title">
            <option v-for="item in group.items" :key="item.page" :value="item.page">
              {{ item.page }}
            </option>
          </optgroup>
        </select>
        <div class="topbar-right">
          <button
            v-if="capabilities.permissions && pendingApprovalCount > 0"
            type="button"
            class="topbar-alert-btn"
            @click="navigate('权限审批')"
          >
            <span>待审批 {{ pendingApprovalCount }}</span>
          </button>

          <el-dropdown trigger="click" @command="handleUserCommand">
            <button type="button" class="user-dropdown-trigger">
              <span class="topbar-avatar" :class="userToneClass">
                {{ userInitial }}
              </span>
              <span class="user-name-col">
                <strong class="user-realname">{{ user.display_name }}</strong>
                <small class="user-roles-text">{{ displayRoles(user) }}</small>
              </span>
              <svg
                class="chevron-svg"
                viewBox="0 0 24 24"
                fill="none"
                stroke="currentColor"
                stroke-width="2"
              >
                <polyline points="6 9 12 15 18 9" />
              </svg>
            </button>
            <template #dropdown>
              <el-dropdown-menu>
                <div class="dropdown-user-header">
                  <strong>{{ user.display_name }}</strong>
                  <small>账号：{{ user.username }} · {{ primaryOrgName }}</small>
                  <span class="dropdown-clearance-tag">数据权限：{{ clearanceText }}</span>
                </div>
                <el-dropdown-item command="account">个人中心</el-dropdown-item>
                <el-dropdown-item command="tasks">我的任务</el-dropdown-item>
                <el-dropdown-item divided command="signout">退出登录</el-dropdown-item>
              </el-dropdown-menu>
            </template>
          </el-dropdown>
        </div>
      </header>

      <main class="content">
        <div class="page-heading">
          <div>
            <h1>{{ currentPage }}</h1>
            <p>{{ currentPageDefinition.description }}</p>
          </div>
        </div>

        <el-alert
          v-if="user.must_change"
          type="warning"
          show-icon
          :closable="false"
          title="安全提醒：当前账号使用初始密码或重置后的临时密码登录，请先在个人中心修改登录密码。"
        />

        <WorkspaceOverview
          v-if="currentPage === '工作概览'"
          :user="user"
          :dashboard="dashboard"
          :documents="documents"
          :primary-org-name="primaryOrgName"
          :roles="displayRoles(user)"
          :clearance-text="clearanceText"
          @open-document="documentManager?.openDocument($event)"
          @navigate="navigate"
        />

        <ResearchChatView v-else-if="currentPage === '选品调研'" :api="api" @inspect="openTrace" />

        <BiView v-else-if="currentPage === '数据中心'" :api="api" />

        <TraceCenter
          v-else-if="currentPage === '轨迹中台' && !user.must_change"
          :key="`${user.id}:${user.role_level}`"
          :api="api"
          :is-admin="isAdmin(user)"
          @inspect="openTrace"
        />

        <template v-else-if="isEvaluationWorkspacePage && !user.must_change">
          <section class="evaluation-kind-switch" aria-label="选择评测体系">
            <span class="evaluation-kind-switch-label">评测体系</span>
            <el-checkbox-group v-model="evaluationKinds">
              <el-checkbox
                value="agent"
                :disabled="evaluationKinds.length === 1 && evaluationKinds.includes('agent')"
              >
                Agent
              </el-checkbox>
              <el-checkbox
                value="rag"
                :disabled="evaluationKinds.length === 1 && evaluationKinds.includes('rag')"
              >
                RAG
              </el-checkbox>
            </el-checkbox-group>
            <span class="evaluation-kind-switch-hint">至少保留一个评测体系</span>
          </section>
          <div class="evaluation-panel-stack">
            <EvaluationCenter
              v-for="kind in evaluationKinds"
              :key="`${user.id}:${user.role_level}:${currentPage}:${kind}`"
              :api="api"
              :is-admin="isAdmin(user)"
              :can-write-eval="isAdmin(user) || Boolean(user.is_boss)"
              :view="evaluationView"
              :kind="kind"
              :collapsed="collapsedEvaluationKinds.includes(kind)"
              @inspect="openTrace"
              @expand="expandEvaluationKind(kind)"
              @launch="navigate('测评启动')"
            />
          </div>
        </template>

        <KnowledgeWorkspace
          v-else-if="currentPage === '知识问答' && !user.must_change"
          :key="user.id"
          :user="user"
          :organizations="organizations"
          :input-level="currentUserLevel"
          :history-icon-paths="workspacePages.知识问答.iconPaths"
          :tasks="tasks"
          :task="inlineQuestion ? activeTask : null"
          :loading-answer="inlineQuestion && !activeTask"
          :draft="inlineQuestion ? taskDraft : ''"
          :busy="busy"
          @submit="submitQuestion"
          @select="selectQuestion"
          @reset="newQuestion"
          @browse="navigate('知识文档')"
          @inspect="openTrace"
          @open-doc="documentManager?.openDocument($event)"
          @cancel="cancelTask"
          @retry="retryTask"
          @review="reviewTask"
        />

        <TaskCenter
          v-else-if="currentPage === '我的任务' || currentPage === '团队审核'"
          :tasks="visibleTasks"
          :is-review-page="currentPage === '团队审核'"
          @open="openTask"
          @navigate="navigate"
          @reload="load"
        />

        <AdministrationWorkspace
          v-else-if="isAdministrationPage(currentPage)"
          :key="currentPage"
          :page="currentPage"
          :api="api"
          :action="action"
          :refresh="load"
          :busy="busy"
          :approvals="approvals"
          :organizations="organizations"
          :users="users"
          :root-organization-id="rootOrganizationId"
        />

        <AccountCenter
          v-else-if="currentPage === '个人中心'"
          :user="user"
          :tenant-name="workspaceInfo.name || '企业工作空间'"
          :busy="busy"
          @change-password="changePassword"
          @sign-out="signOut"
        />
        <DocumentManager
          v-if="!user.must_change"
          :key="user.id"
          ref="documentManager"
          :library-visible="currentPage === '知识文档'"
          :api="api"
          :refresh-workspace="load"
          :documents="documents"
          :organizations="organizations"
          :root-organization-id="rootOrganizationId"
          :users="users"
          :user="user"
        />
      </main>
    </div>
  </div>

  <TaskTimeline
    v-model="taskOpen"
    :task="activeTask"
    :draft="taskDraft"
    :can-inspect="
      Boolean(
        user &&
        !user.must_change &&
        activeTask &&
        ['ask', 'research'].includes(activeTask.kind) &&
        (isAdmin(user) || activeTask.owner_id === user.id),
      )
    "
    @inspect="openTrace"
    @open-doc="documentManager?.openDocument($event)"
    @review="reviewTask"
    @cancel="cancelTask"
    @retry="retryTask"
    @steer="steerTask"
  />

  <TraceInspector
    v-if="inspectedTrace && user && !user.must_change"
    :key="inspectedTrace.taskId"
    :task-id="inspectedTrace.taskId"
    :initial-trace-id="inspectedTrace.traceId"
    :api="api"
    @close="inspectedTrace = null"
  />
</template>

<script setup lang="ts">
import { computed, onMounted, reactive, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import { requestApi } from './app/workspaceApi'
import { useWorkspaceAction } from './shared/useWorkspaceAction'
import { levelText } from './shared/labels'
import robotUrl from './assets/robot.svg'
import {
  isEvaluationPage,
  workspaceNavigation,
  workspacePages,
  type WorkspacePage,
} from './app/workspaceNavigation'
import { isAdmin, isAdministrationPage, type ManagedUser } from './features/administration/types'
import AccountCenter from './features/administration/AccountCenter.vue'
import BiView from './features/bi/BiView.vue'
import DocumentManager from './features/documents/DocumentManager.vue'
import LoginPortal from './app/LoginPortal.vue'
import KnowledgeWorkspace from './features/tasks/KnowledgeWorkspace.vue'
import AdministrationWorkspace from './features/administration/AdministrationWorkspace.vue'
import WorkspaceOverview from './app/WorkspaceOverview.vue'
import { useTaskWorkspace } from './features/tasks/useTaskWorkspace'
import ResearchChatView from './features/tasks/ResearchChatView.vue'
import TaskCenter from './features/tasks/TaskCenter.vue'
import TaskTimeline from './features/tasks/TaskTimeline.vue'
import EvaluationCenter from './features/observability/EvaluationCenter.vue'
import TraceCenter from './features/observability/TraceCenter.vue'
import TraceInspector from './features/observability/TraceInspector.vue'
import type { Approval, Capabilities, Organization, User } from './features/administration/types'
import type { Items } from './shared/apiTypes'
import type { Dashboard, LoginResponse, QuickAccount, WorkspaceInfo } from './app/workspaceTypes'
import type { DocumentListItem } from './features/documents/types'
import type { Task } from './features/tasks/types'
import type { EvaluationKind } from './features/observability/evaluationTypes'

const workspaceInfo = ref<WorkspaceInfo>({
  name: '',
  mode: '',
  tenant_code: '',
  quick_accounts: [],
})
const user = ref<User | null>(null)
const currentPage = ref<WorkspacePage>('工作概览')
const evaluationKinds = ref<EvaluationKind[]>(['agent'])
const collapsedEvaluationKinds = ref<EvaluationKind[]>([])
const { busy, action } = useWorkspaceAction()
const documentManager = ref<InstanceType<typeof DocumentManager> | null>(null)
const organizations = ref<Organization[]>([])
const documents = ref<DocumentListItem[]>([])
const tasks = ref<Task[]>([])
const approvals = ref<Approval[]>([])
const users = ref<ManagedUser[]>([])
const emptyDashboard: Dashboard = {
  documents: 0,
  tasks: 0,
  review: 0,
  completed: 0,
  teams: [],
  model_ready: false,
  business_ready: false,
}
const dashboard = ref<Dashboard>(emptyDashboard)
let session = new AbortController()
let workspaceRequest = 0

const loginForm = reactive({ username: '', password: '' })

const inspectedTrace = ref<{ taskId: string; traceId: string | null } | null>(null)
/**
 * 判断审批记录是否处于待处理状态。
 * @param row - 审批记录。
 * @returns 是否为待审批状态。
 */
function isPendingApproval(row: Approval): boolean {
  return row.state === 'pending'
}

/**
 * 读取当前登录身份的功能能力集合。
 * @returns 权限能力对象。
 */
function resolveCapabilities(): Capabilities {
  return (
    user.value?.capabilities || {
      system: false,
      write: false,
      review: false,
      permissions: false,
    }
  )
}
const capabilities = computed(resolveCapabilities)

/**
 * 计算组织根节点编号。
 * @returns 公司根节点 ID。
 */
function resolveRootOrganizationId(): string {
  for (const organization of organizations.value) {
    if (organization.kind === 'company') return organization.id
  }
  return organizations.value[0]?.id || ''
}
const rootOrganizationId = computed(resolveRootOrganizationId)

const pendingApprovalCount = computed(() => approvals.value.filter(isPendingApproval).length)
const navigationGroups = computed(() =>
  workspaceNavigation(
    user.value,
    capabilities.value,
    pendingApprovalCount.value,
    dashboard.value.review,
  ),
)
const currentPageDefinition = computed(() => workspacePages[currentPage.value])
const navigationItems = computed(() => navigationGroups.value.flatMap((group) => group.items))
const isEvaluationWorkspacePage = computed(() => isEvaluationPage(currentPage.value))
const evaluationView = computed<'launch' | 'results'>(() =>
  currentPage.value === '测评启动' ? 'launch' : 'results',
)
const breadcrumbText = computed(() => {
  if (user.value?.must_change) return '账号安全'
  const group = currentPageDefinition.value.group
  return isEvaluationWorkspacePage.value ? `${group} / ${currentPage.value}` : group
})

/**
 * 展开被折叠的评测体系面板，避免同时铺开两套完整评测页。
 * @param kind - 要展开的评测体系。
 */
function expandEvaluationKind(kind: EvaluationKind): void {
  collapsedEvaluationKinds.value = collapsedEvaluationKinds.value.filter((item) => item !== kind)
}

watch(evaluationKinds, (kinds, previous) => {
  if (kinds.length < 2) {
    collapsedEvaluationKinds.value = []
    return
  }
  if ((previous?.length ?? 0) < 2) collapsedEvaluationKinds.value = [kinds[1]]
})

/**
 * 处理移动端导航下拉框选择。
 * @param event - 下拉框变更事件。
 */
function navigateFromSelect(event: Event): void {
  const target = event.target
  if (!(target instanceof HTMLSelectElement)) return
  void navigate(target.value)
}

/**
 * 获取当前登录用户的首字缩写。
 * @returns 大写头像缩写。
 */
function computeUserInitial(): string {
  const raw = (user.value?.display_name || user.value?.username || 'U').trim()
  return raw ? raw.slice(0, 1).toUpperCase() : 'U'
}
const userInitial = computed(computeUserInitial)

/**
 * 计算当前用户的角色视觉配色类名。
 * @returns 样式类名。
 */
function computeUserTone(): string {
  if (user.value?.is_boss) return 'tone-exec'
  if (user.value?.is_leader) return 'tone-lead'
  if (isAdmin(user.value)) return 'tone-admin'
  return 'tone-member'
}
const userToneClass = computed(computeUserTone)

/**
 * 获取当前用户主归属部门名称。
 * @returns 部门名称。
 */
function computePrimaryOrg(): string {
  const membership = user.value?.memberships?.[0]
  return membership?.name || orgName(membership?.node_id) || '暂未分配部门'
}
const primaryOrgName = computed(computePrimaryOrg)

/**
 * 将组织节点编号解析为可读部门名称。
 * @param nodeId 组织节点编号。
 * @returns 部门名称，未命中时回退编号或占位符。
 */
function orgName(nodeId?: string | null): string {
  if (!nodeId) return '—'
  return organizations.value.find((organization) => organization.id === nodeId)?.name || nodeId
}

/**
 * 推导当前账户的数据访问密级数值（L1/L2/L3）。
 * @returns 密级数字。
 */
function computeUserLevel(): number {
  if (user.value?.is_boss) return 3
  if (user.value?.is_leader) return 2
  return 1
}
const currentUserLevel = computed(computeUserLevel)

const clearanceText = computed(
  /**
   * 输出完整的数据权限等级文本。
   * @returns 密级名称。
   */
  () => levelText(currentUserLevel.value),
)

const clearanceShortText = computed(
  /**
   * 输出侧栏底部精简密级标识。
   * @returns 精简密级标签。
   */
  () =>
    `${currentUserLevel.value === 3 ? '机密 (L3)' : currentUserLevel.value === 2 ? '内部 (L2)' : '公开 (L1)'}`,
)

/**
 * 格式化用户信息条展示的岗位角色摘要。
 * @param row - 账号对象。
 * @returns 角色摘要文案。
 */
function displayRoles(row: User): string {
  const items: string[] = []
  if (row.is_boss) items.push('企业负责人')
  else if (row.is_leader) items.push('部门主管')
  else items.push('普通员工')
  if (isAdmin(row)) items.push('系统管理员')
  return items.join(' · ')
}

/**
 * 清空客户端所有业务状态，确保退出或改密后不残留会话信息。
 */
function clearSession(): void {
  session.abort()
  session = new AbortController()
  workspaceRequest++
  user.value = null
  inspectedTrace.value = null
  organizations.value = []
  documents.value = []
  tasks.value = []
  approvals.value = []
  users.value = []
  dashboard.value = emptyDashboard
  currentPage.value = '工作概览'
  evaluationKinds.value = ['agent']
  collapsedEvaluationKinds.value = []
  loginForm.password = ''
  clearTaskWorkspace()
}

/**
 * 调用同源企业服务端 API，统一注入运行环境 CSRF 与未认证会话清理回调。
 * @param path - 同源请求路径。
 * @param method - HTTP 方法，默认 GET。
 * @param payload - JSON 对象或 FormData 表单载体。
 * @returns 经过结构校验的服务端响应数据。
 */
async function api<T>(path: string, method = 'GET', payload?: unknown): Promise<T> {
  return requestApi<T>(
    path,
    workspaceInfo.value.mode,
    () => {
      if (user.value) clearSession()
    },
    method,
    payload,
    session.signal,
  )
}

/**
 * 并行刷新当前账号权限范围内的数据快照。
 */
async function load(): Promise<void> {
  if (!user.value) return
  if (user.value.must_change) {
    currentPage.value = '个人中心'
    return
  }
  const request = ++workspaceRequest
  const [overview, organizationPage, documentPage, taskPage, approvalPage, userPage] =
    await Promise.all([
      api<Dashboard>('/dashboard'),
      api<Items<Organization>>('/organizations'),
      api<Items<DocumentListItem>>('/documents'),
      api<Items<Task>>('/tasks'),
      capabilities.value.permissions
        ? api<Items<Approval>>('/approvals')
        : Promise.resolve({ items: [] as Approval[] }),
      capabilities.value.system || capabilities.value.permissions
        ? api<Items<ManagedUser>>('/users')
        : Promise.resolve({ items: [] as ManagedUser[] }),
    ])
  if (request !== workspaceRequest) return
  dashboard.value = overview
  organizations.value = organizationPage.items
  documents.value = documentPage.items
  tasks.value = taskPage.items
  approvals.value = approvalPage.items
  users.value = userPage.items
}

/**
 * 使用企业账号登录并设置默认密级与外部分析开关；登录成功后立即清空密码输入。
 */
async function signIn(): Promise<void> {
  await action(async () => {
    const result = await api<LoginResponse>('/auth/login', 'POST', {
      ...loginForm,
      tenant_code: workspaceInfo.value.tenant_code,
    })
    clearSession()
    const signedUser = result.user
    user.value = signedUser
    loginForm.password = ''
    currentPage.value = signedUser.must_change ? '个人中心' : '工作概览'
    evaluationKinds.value = ['agent']
    collapsedEvaluationKinds.value = []
    await load()
  })
}

/**
 * 退出当前账号并清空客户端状态。
 */
async function signOut(): Promise<void> {
  await action(async () => {
    await api('/auth/logout', 'POST')
    clearSession()
    loginForm.username = ''
    ElMessage.success('已退出登录')
  })
}

/**
 * 快捷填充测试账号凭据。
 * @param acc - 服务端返回的测试账号元数据。
 */
function pickAccount(acc: QuickAccount): void {
  loginForm.username = acc.username
  if (acc.password) loginForm.password = acc.password
}

/**
 * 处理右上角用户菜单指令。
 * @param cmd - 菜单命令标识。
 */
function handleUserCommand(cmd: string): void {
  if (cmd === 'account') void navigate('个人中心')
  else if (cmd === 'tasks') void navigate('我的任务')
  else if (cmd === 'signout') void signOut()
}

/**
 * 切换菜单并加载对应页面数据。
 * @param name - 菜单名称。
 */
async function navigate(name: string): Promise<void> {
  const destination = navigationItems.value.find((item) => item.page === name)
  if (!destination) return
  if (currentPage.value === '知识问答' && destination.page !== currentPage.value) newQuestion()
  currentPage.value = destination.page
  if (!isLazyPage(destination.page)) await action(load)
}

/**
 * 判断页面是否自行装载数据，无需工作台整体刷新。
 * @param page - 工作台页面名称。
 * @returns 是否为延迟装载页面。
 */
function isLazyPage(page: WorkspacePage): boolean {
  return isEvaluationPage(page) || page === '轨迹中台' || page === '系统状态' || page === '操作日志'
}

/**
 * 从任务或调研对话打开该任务的执行轨迹。按 Trace ID 搜索时保留命中的执行。
 * @param taskId - 已持久化的任务编号。
 * @param traceId - 可选的精确链路编号。
 */
function openTrace(taskId: string, traceId: string | null = null): void {
  inspectedTrace.value = { taskId, traceId }
}

/**
 * 个人修改登录密码并强制重新登录。
 * @param payload - 包含当前密码与新密码的变更载荷。
 * @param payload.current_password - 当前登录密码。
 * @param payload.new_password - 新登录密码。
 */
async function changePassword(payload: {
  current_password: string
  new_password: string
}): Promise<void> {
  await action(async () => {
    await api('/auth/password', 'POST', payload)
    clearSession()
    ElMessage.success('密码已修改，请重新登录')
  })
}

const knowledgePage = computed(() => currentPage.value === '知识问答')
const reviewPage = computed(() => currentPage.value === '团队审核')
const {
  taskOpen,
  activeTask,
  taskDraft,
  inlineQuestion,
  visibleTasks,
  newQuestion,
  selectQuestion,
  submitQuestion,
  openTask,
  reviewTask,
  cancelTask,
  retryTask,
  steerTask,
  clearTaskWorkspace,
} = useTaskWorkspace({ api, user, tasks, knowledgePage, reviewPage, busy, action, load })

onMounted(async () => {
  try {
    workspaceInfo.value = await api<WorkspaceInfo>('/info')
  } catch (e: unknown) {
    console.error('获取工作空间信息失败:', e)
  }
  try {
    const me = await api<User>('/auth/me')
    user.value = me
    await load()
  } catch {}
})
</script>

<style scoped>
.nav-group-directory {
  padding-top: 18px;
  color: #94a3b8;
  font-weight: 700;
}

.nav-item-btn.nav-item-child {
  height: 34px;
  padding-left: 32px;
  font-size: 12.5px;
}

.nav-item-btn.nav-item-child .nav-svg-icon {
  width: 14px;
  height: 14px;
  opacity: 0.7;
}

.nav-item-btn.nav-item-child.active .nav-svg-icon {
  opacity: 1;
}

.evaluation-kind-switch {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 16px;
  margin-bottom: 16px;
  padding: 10px 16px;
  background: var(--tz-surface);
  border: 1px solid var(--tz-border);
  border-radius: 10px;
}

.evaluation-kind-switch-label {
  color: var(--tz-ink-secondary);
  font-size: 13px;
  font-weight: 600;
}

.evaluation-kind-switch-hint {
  color: var(--tz-ink-muted);
  font-size: 12px;
}

.evaluation-panel-stack {
  display: flex;
  flex-direction: column;
  gap: 18px;
}
</style>
