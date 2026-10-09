<script setup lang="ts">
import { ref, reactive, computed, onMounted, onUnmounted } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { requestApi } from './api'
import type {
  User,
  WorkspaceInfo,
  Organization,
  OrganizationTree,
  DocumentRecord,
  DocumentListItem,
  DocumentVersion,
  Task,
  Approval,
  AuditEvent,
  Grant,
  Dashboard,
  RuntimeStatus,
  Items,
  CreatedResource,
  LoginResponse,
  Capabilities,
} from './types'
const user = ref<User | null>(null),
  info = ref<Partial<WorkspaceInfo>>({}),
  page = ref('工作台'),
  loading = ref(false),
  busy = ref(false)
const orgs = ref<Organization[]>([]),
  docs = ref<DocumentListItem[]>([]),
  jobs = ref<Task[]>([]),
  people = ref<User[]>([]),
  requests = ref<Approval[]>([]),
  audits = ref<AuditEvent[]>([]),
  stats = ref<Partial<Dashboard>>({})
const detail = ref<DocumentRecord | null>(null),
  activeTask = ref<Task | null>(null),
  history = ref<DocumentVersion[]>([])
const docOpen = ref(false),
  taskOpen = ref(false),
  historyOpen = ref(false),
  docEdit = ref(false),
  policyOpen = ref(false),
  grantOpen = ref(false),
  accountOpen = ref(false),
  memberOpen = ref(false),
  orgOpen = ref(false),
  accessOpen = ref(false)
const loginForm = reactive({ username: '', password: '' }),
  passwordForm = reactive({ current_password: '', new_password: '' })
const askForm = reactive({
  question: '',
  depth: 'standard',
  input_level: 1,
  require_review: false,
  allow_external: true,
  sku_id: '',
  order_id: '',
})
const docForm = reactive({
  id: '',
  title: '',
  body: '',
  scope: 'org',
  node_id: '',
  level: 2,
  version: 1,
})
const policyForm = reactive({
  scope: 'org',
  node_id: '',
  level: 2,
  ai_allowed: false,
  download_allowed: false,
})
const grantForm = reactive({
  user_id: '',
  effect: 'allow',
  can_download: false,
  expires_at: '',
  revoke: false,
})
const accountForm = reactive({
  username: '',
  password: '',
  display_name: '',
  role: 'employee',
  node_id: '',
})
const memberForm = reactive({ user_id: '', node_id: '', role: 'employee', remove: false })
const orgForm = reactive({
  id: '',
  name: '',
  kind: 'department',
  parent_id: '' as string | null,
  active: true,
})
const runtimeStatus = ref<Partial<RuntimeStatus>>({}),
  currentGrants = ref<Grant[]>([]),
  permissionIdOpen = ref(false),
  permissionId = ref('')
const accessId = ref(''),
  searchTerm = ref(''),
  selectedFile = ref<File | null>(null),
  fileInput = ref<HTMLInputElement | null>(null)

/**
 * 清除会话关联的资料、答案、弹窗及密码；401、退出和改密均调用，避免换人后残留。
 */
function wipe() {
  user.value = null
  detail.value = null
  activeTask.value = null
  docs.value = []
  jobs.value = []
  people.value = []
  requests.value = []
  audits.value = []
  orgs.value = []
  history.value = []
  currentGrants.value = []
  stats.value = {}
  runtimeStatus.value = {}
  docOpen.value = false
  taskOpen.value = false
  historyOpen.value = false
  docEdit.value = false
  policyOpen.value = false
  grantOpen.value = false
  accountOpen.value = false
  memberOpen.value = false
  orgOpen.value = false
  accessOpen.value = false
  permissionIdOpen.value = false
  Object.assign(docForm, { id: '', title: '', body: '' })
  askForm.question = ''
  askForm.sku_id = ''
  askForm.order_id = ''
  accountForm.password = ''
  passwordForm.current_password = ''
  passwordForm.new_password = ''
  accessId.value = ''
  permissionId.value = ''
  selectedFile.value = null
}
/**
 * 复用同源会话请求与 CSRF 校验；响应契约由调用方显式指定，后端拒绝仍生效。
 * @param path - 业务接口路径。
 * @param method - HTTP 方法；写入需要 CSRF。
 * @param payload - JSON 输入或附件表单。
 * @returns 通过基础校验的接口结果；网络或业务拒绝抛出中文错误。
 */
async function api<T = Record<string, unknown>>(
  path: string,
  method = 'GET',
  payload?: unknown,
): Promise<T> {
  return requestApi<T>(path, info.value.mode || '', wipe, method, payload)
}
/**
 * 执行页面操作并显示中文错误；无论成功或失败都释放忙碌状态。
 * @param work - 需要执行的异步操作。
 * @returns 操作完成结果；失败统一显示中文提示。
 */
async function action(work: () => Promise<void>) {
  busy.value = true
  try {
    await work()
  } catch (e: unknown) {
    const message = e instanceof Error ? e.message : ''
    ElMessage.error(
      message && /[\u4e00-\u9fff]/.test(message) ? message : '操作未完成，请检查输入或稍后重试',
    )
  } finally {
    busy.value = false
  }
}
const caps = computed<Partial<Capabilities>>(() => user.value?.capabilities || {})
const root = computed(() => orgs.value.find((n) => n.kind === 'company')?.id || '')
const menus = computed(() => [
  '工作台',
  '智能问答',
  '任务中心',
  '文档库',
  ...(caps.value.review ? ['团队工作台'] : []),
  ...(caps.value.permissions ? ['审核与授权'] : []),
  ...(caps.value.system ? ['组织与人员', '运行状态'] : []),
  ...(caps.value.review || caps.value.system ? ['审计记录'] : []),
  '个人设置',
])
const title = computed(
  () =>
    (
      ({
        工作台: '把知识与协作，放在同一个工作台',
        智能问答: '让每个答案都有可靠依据',
        任务中心: '从提问到发布，全程可追溯',
        文档库: '让合适的人，看见合适的资料',
        团队工作台: '管理团队任务与业务审核',
        审核与授权: '每一次开放，都有明确依据',
        组织与人员: '组织清晰，职责明确',
        审计记录: '查看权限与业务操作记录',
        运行状态: '查看企业服务运行状态',
        个人设置: '管理你的账号与登录信息',
      }) as Record<string, string>
    )[page.value],
)
const subtitles = computed(
  () =>
    (
      ({
        工作台: '按当前身份展示获准的资料、任务和团队范围。',
        智能问答: '系统仅使用你有权访问的资料；涉及机密内容时请设置问题密级。',
        任务中心: '查看阶段进度、核验结果与引用。来源撤权后，历史答案也会重新校验。',
        文档库: '资料按组织范围与密级管理。新资料先审核，再发布。',
        团队工作台: '审核负责范围内的任务，跨组和机密资料另行授权。',
        审核与授权: '任职、单文档授权、密级调整和发布审核在这里处理。',
        组织与人员: '管理员维护系统；业务负责人批准访问范围和角色变化。',
        审计记录: '管理员查看运行审计，业务负责人查看获准范围内的业务审计。',
        运行状态: '查看服务连通性、任务进程和本企业队列状态，不展示业务正文。',
        个人设置: '密码修改后，旧会话会失效，需要重新登录。',
      }) as Record<string, string>
    )[page.value],
)
const NAV_GLYPHS: Record<string, string> = {
  工作台: '概',
  智能问答: '问',
  任务中心: '任',
  文档库: '文',
  团队工作台: '协',
  审核与授权: '审',
  组织与人员: '组',
  运行状态: '态',
  审计记录: '迹',
  个人设置: '我',
}
const filteredDocs = computed(() => docs.value.filter((d) => d.title.includes(searchTerm.value)))
const tree = computed(() => {
  const map = new Map(
    orgs.value.map((n) => [n.id, { ...n, label: n.name, children: [] } as OrganizationTree]),
  )
  const roots: OrganizationTree[] = []
  map.forEach((n) => {
    if (n.parent_id && map.has(n.parent_id)) map.get(n.parent_id)!.children.push(n)
    else roots.push(n)
  })
  return roots
})
/**
 * 将资料密级转为中文；未知等级显示待确认。
 * @param n - 资料密级。
 * @returns 中文显示文本。
 */
const level = (n: number) =>
  (({ 1: '员工级', 2: '组长级', 3: '老板级' }) as Record<number, string>)[n] || '待确认'
/**
 * 将任务、资料与审批状态转为中文；未知状态不冒充成功。
 * @param s - 后端状态码。
 * @returns 中文显示文本。
 */
const state = (s: string) =>
  (
    ({
      queued: '等待处理',
      running: '处理中',
      review: '等待审核',
      completed: '已完成',
      cancelled: '已取消',
      failed: '处理失败',
      rejected: '审核未通过',
      legacy: '待重新核验',
      draft: '草稿',
      ingesting: '正在解析',
      published: '已发布',
      deleted: '已删除',
      pending: '待审批',
      approved: '已批准',
      stale: '已失效',
    }) as Record<string, string>
  )[s] || '待确认'
/**
 * 将组织、任职和授权类型转为中文。
 * @param s - 后端类型值。
 * @returns 中文显示文本。
 */
const kind = (s: string) =>
  (
    ({
      company: '公司',
      department: '部门',
      team: '小组',
      employee: '员工',
      leader: '组长',
      boss: '老板',
      allow: '允许',
      deny: '禁止',
    }) as Record<string, string>
  )[s] || s
/**
 * 格式化中文时间；缺少时间显示占位符。
 * @param v - ISO 时间、日期对象或空值。
 * @returns 中文显示文本。
 */
const date = (v: string | number | Date | null | undefined) =>
  v ? new Date(v).toLocaleString('zh-CN', { hour12: false }) : '—'
/**
 * 仅在当前可见组织中查找名称。
 * @param id - 组织编号。
 * @returns 中文显示文本。
 */
const nodeName = (id: string) => orgs.value.find((n) => n.id === id)?.name || '待分配'
/**
 * 仅在当前可见人员中查找姓名；未知人员不暴露身份。
 * @param id - 人员编号。
 * @returns 中文显示文本。
 */
const personName = (id: string) =>
  people.value.find((n) => n.id === id)?.display_name ||
  (id === user.value?.id ? user.value?.display_name : '企业成员')
/**
 * 并行刷新当前身份的获准资料与任务；首次改密账号仅进入个人设置。
 */
async function load() {
  if (!user.value) return
  if (user.value.must_change) {
    page.value = '个人设置'
    return
  }
  loading.value = true
  try {
    const [o, d, t, s, p, r] = await Promise.all([
      api<Items<Organization>>('/organizations'),
      api<Items<DocumentListItem>>('/documents'),
      api<Items<Task>>('/tasks'),
      api<Dashboard>('/dashboard'),
      api<Items<User>>('/users'),
      api<Items<Approval>>('/approvals'),
    ])
    orgs.value = o.items
    docs.value = d.items
    jobs.value = t.items
    stats.value = s
    people.value = p.items
    requests.value = r.items
  } finally {
    loading.value = false
  }
}
/**
 * 使用企业账号登录并设置问题默认密级；密码随即清空，老板默认禁用外部 AI。
 */
async function signIn() {
  await action(async () => {
    const result = await api<LoginResponse>('/auth/login', 'POST', {
      ...loginForm,
      tenant_code: info.value.tenant_code,
    })
    user.value = result.user
    loginForm.password = ''
    page.value = user.value!.must_change ? '个人设置' : '工作台'
    askForm.input_level = user.value!.is_boss ? 3 : user.value!.is_leader ? 2 : 1
    askForm.allow_external = !user.value!.is_boss
    await load()
  })
}
/**
 * 撤销当前会话并清空业务内存；网络失败时保持当前界面以便重试。
 */
async function signOut() {
  await action(async () => {
    await api('/auth/logout', 'POST')
    wipe()
    loginForm.username = ''
  })
}
/**
 * 填入隔离演示账号；正式环境不展示此入口。
 * @param name - 演示账号名。
 */
function chooseRole(name: string) {
  loginForm.username = name
  loginForm.password = '123456'
  ElMessage.info(`已快捷填入 ${name} 演示账号与默认密码 123456`)
}
/**
 * 切换中文菜单并按当前身份获取审计或运行状态；菜单可见不代替后端授权。
 * @param name - 中文菜单名称。
 */
async function navigate(name: string) {
  page.value = name
  if (name === '运行状态')
    await action(async () => {
      runtimeStatus.value = await api<RuntimeStatus>('/system/status')
    })
  else if (name === '审计记录')
    await action(async () => {
      audits.value = (await api<Items<AuditEvent>>('/audit')).items
    })
  else await action(load)
}
/**
 * 提交问答任务并打开获准进度；空问题不提交，审核前不自行显示答案。
 * @returns 操作完成；空问题时返回中文提示，不创建任务。
 */
async function submitQuestion() {
  if (!askForm.question.trim()) return ElMessage.warning('请先填写你的问题')
  await action(async () => {
    const r = await api<CreatedResource>('/tasks', 'POST', askForm)
    activeTask.value = await api<Task>('/tasks/' + r.id)
    taskOpen.value = true
    streamTask(r.id)
    ElMessage.success('任务已提交')
    await load()
  })
}
let taskStream: EventSource | null = null
/**
 * 订阅任务事件流（SSE）：阶段与进度实时推送，终态一次性携带完整结果。
 * 核验完成前服务端只发阶段状态；连接失败时静默降级为既有轮询。
 * @param id - 任务编号；权限由服务端在事件流中持续核验。
 */
function streamTask(id: string) {
  taskStream?.close()
  taskStream = new EventSource('/api/tasks/' + id + '/events')
  /**
   * 接收进度事件；只更新当前任务，忽略其他任务的事件。
   * @param ev - 服务端推送的进度事件，载荷为阶段与进度字段。
   */
  taskStream.onmessage = (ev) => {
    try {
      const row = JSON.parse(ev.data) as { stage?: string; progress?: number }
      if (!activeTask.value || activeTask.value.id !== id) return
      if (row.stage) activeTask.value.stage = row.stage
      if (typeof row.progress === 'number') activeTask.value.progress = row.progress
    } catch {}
  }
  taskStream.addEventListener('done', (ev) => {
    taskStream?.close()
    taskStream = null
    try {
      const done = JSON.parse((ev as MessageEvent).data) as { id: string }
      if (activeTask.value?.id === done.id)
        api<Task>('/tasks/' + done.id).then((t) => {
          if (activeTask.value?.id === done.id) activeTask.value = t
        })
    } catch {}
  })
  /** 连接失败时静默关闭事件流，既有轮询继续兜底刷新进度。 */
  taskStream.onerror = () => {
    taskStream?.close()
    taskStream = null
  }
}
/**
 * 重新读取指定任务；权限或来源失效由服务端拒绝，不沿用历史答案。
 * @param id - 任务编号，读取时重新核验权限。
 */
async function openTask(id: string) {
  await action(async () => {
    activeTask.value = await api<Task>('/tasks/' + id)
    taskOpen.value = true
  })
}
/**
 * 提交审核决定并重新获取答案；批准不会把审核人的权限转给提问人。
 * @param approve - 批准或拒绝当前任务。
 */
async function reviewTask(approve: boolean) {
  if (!activeTask.value) return
  await action(async () => {
    await api('/tasks/' + activeTask.value!.id + '/review', 'POST', { approve, comment: '' })
    ElMessage.success(approve ? '答案已发布' : '答案未通过审核')
    activeTask.value = await api<Task>('/tasks/' + activeTask.value!.id)
    await load()
  })
}
/**
 * 请求取消当前任务；执行与取消权限由服务端再次核验。
 */
async function cancelTask() {
  await action(async () => {
    await api('/tasks/' + activeTask.value!.id + '/cancel', 'POST')
    ElMessage.success('已请求取消')
    await load()
  })
}
/**
 * 打开授权文档；不根据猜测编号展示未获准标题或正文。
 * @param id - 资料编号，读取时重新核验范围和密级。
 */
async function openDoc(id: string) {
  await action(async () => {
    detail.value = await api<DocumentRecord>('/documents/' + id)
    docOpen.value = true
  })
}
/**
 * 创建资料草稿表单；管理员默认公司员工级资料，发布仍需审核。
 */
function newDoc() {
  Object.assign(docForm, {
    id: '',
    title: '',
    body: '',
    scope: user.value?.system_admin && !user.value?.is_boss ? 'company' : 'org',
    node_id:
      user.value?.system_admin && !user.value?.is_boss
        ? root.value
        : user.value?.memberships[0]?.node_id || root.value,
    level: user.value?.system_admin && !user.value?.is_boss ? 1 : 2,
    version: 1,
  })
  selectedFile.value = null
  docEdit.value = true
}
/**
 * 读取当前资料版本到编辑表单；保存时携带版本，避免覆盖并发修改。
 */
function editDoc() {
  if (!detail.value) return
  Object.assign(docForm, {
    id: detail.value.id,
    title: detail.value.title,
    body: detail.value.body,
    scope: detail.value.scope,
    node_id: detail.value.node_id,
    level: detail.value.level,
    version: detail.value.current_version,
  })
  selectedFile.value = null
  docEdit.value = true
}
/**
 * 保存草稿、附件或新版本；指定范围和密级，解析后仍等待发布审核。
 */
async function saveDoc() {
  await action(async () => {
    if (docForm.id)
      await api('/documents/' + docForm.id, 'PUT', {
        title: docForm.title,
        body: docForm.body,
        version: docForm.version,
      })
    else if (selectedFile.value) {
      const form = new FormData()
      form.append('file', selectedFile.value)
      for (const k of ['title', 'scope', 'node_id', 'level'] as const)
        form.append(k, String(docForm[k]))
      await api('/documents/upload', 'POST', form)
    } else
      await api('/documents', 'POST', {
        title: docForm.title,
        body: docForm.body,
        node_id: docForm.scope === 'company' ? root.value : docForm.node_id,
        scope: docForm.scope,
        level: docForm.level,
      })
    docEdit.value = false
    docOpen.value = false
    ElMessage.success('资料已保存，解析后等待发布审核')
    await load()
  })
}
/**
 * 读取浏览器选择的首个文件；格式、体积和权限仍由上传 API 校验。
 * @param e - 浏览器文件选择事件。
 */
function picked(e: Event) {
  selectedFile.value = (e.target as HTMLInputElement).files?.[0] || null
}
/**
 * 确认后删除获准资料；来源变化会使依赖该资料的历史答案不可读取。
 */
async function deleteDoc() {
  if (!detail.value) return
  await action(async () => {
    await ElMessageBox.confirm('删除后，引用这份资料的历史答案将不可读取。是否删除？', '删除资料', {
      confirmButtonText: '删除',
      cancelButtonText: '取消',
      type: 'warning',
    })
    await api('/documents/' + detail.value!.id, 'DELETE')
    docOpen.value = false
    ElMessage.success('资料已删除')
    await load()
  })
}
/**
 * 填充当前授权配置；按编号查询返回的配置不能当作正文阅读许可。
 */
function showPolicy() {
  if (!detail.value) return
  Object.assign(policyForm, {
    scope: detail.value.scope,
    node_id: detail.value.node_id,
    level: detail.value.level,
    ai_allowed: detail.value.ai_allowed,
    download_allowed: detail.value.download_allowed,
  })
  policyOpen.value = true
}
/**
 * 提交资料密级、组织与 AI 许可申请；审批完成前不会自行扩大访问范围。
 */
async function savePolicy() {
  await action(async () => {
    await api('/documents/' + detail.value!.id + '/policy', 'POST', {
      ...policyForm,
      node_id: policyForm.scope === 'company' ? root.value : policyForm.node_id,
    })
    policyOpen.value = false
    ElMessage.success('权限调整已提交，等待老板审批')
    await load()
  })
}
/**
 * 读取当前单文档授权并准备申请；不提升人员角色或默认编辑权限。
 */
async function showGrant() {
  currentGrants.value = []
  await action(async () => {
    currentGrants.value = (
      await api<Items<Grant>>('/documents/' + detail.value!.id + '/grants')
    ).items
  })
  Object.assign(grantForm, {
    user_id: '',
    effect: 'allow',
    can_download: false,
    expires_at: '',
    revoke: false,
  })
  grantOpen.value = true
}
/**
 * 提交指定人员的读取与下载授权申请；到期时间转换为明确的 UTC 时间。
 */
async function saveGrant() {
  await action(async () => {
    await api('/documents/' + detail.value!.id + '/grants', 'POST', {
      ...grantForm,
      expires_at: grantForm.expires_at ? new Date(grantForm.expires_at).toISOString() : null,
    })
    grantOpen.value = false
    ElMessage.success('文档授权已提交，等待老板审批')
    await load()
  })
}
/**
 * 读取当前身份获准的历史资料版本；不可读版本由后端过滤。
 */
async function showVersions() {
  await action(async () => {
    history.value = (
      await api<Items<DocumentVersion>>('/documents/' + detail.value!.id + '/versions')
    ).items
    historyOpen.value = true
  })
}
/**
 * 审批获准申请；版本或人员变更导致旧申请失效时由服务端拒绝。
 * @param row - 可见申请。
 * @param approve - 批准或拒绝申请。
 */
async function decideRequest(row: Approval, approve: boolean) {
  await action(async () => {
    await api('/approvals/' + row.id + '/decision', 'POST', { approve, comment: '' })
    ElMessage.success(approve ? '申请已批准' : '申请已拒绝')
    await load()
  })
}
/**
 * 准备新账号表单；简单口令只用于演示，正式环境须遵守密码校验。
 */
function newAccount() {
  Object.assign(accountForm, {
    username: '',
    password: info.value.demo ? '123456' : '',
    display_name: '',
    role: 'employee',
    node_id: root.value,
  })
  accountOpen.value = true
}
/**
 * 创建账号并另行申请任职或管理员身份；账号创建不自动取得业务机密。
 */
async function saveAccount() {
  await action(async () => {
    const r = await api<CreatedResource>('/users', 'POST', {
      username: accountForm.username,
      password: accountForm.password,
      display_name: accountForm.display_name,
    })
    if (accountForm.role === 'admin') await api('/users/' + r.id, 'PATCH', { system_admin: true })
    else
      await api('/users/' + r.id + '/memberships', 'POST', {
        node_id: accountForm.role === 'boss' ? root.value : accountForm.node_id,
        role: accountForm.role,
        remove: false,
      })
    accountOpen.value = false
    accountForm.password = ''
    ElMessage.success('账号已创建，任职申请等待业务负责人审批')
    await load()
  })
}
/**
 * 读取人员现有任职到调整表单；多组任职按具体组织单独处理。
 * @param row - 当前可管理人员。
 */
function membership(row: User) {
  Object.assign(memberForm, {
    user_id: row.id,
    node_id: row.memberships?.[0]?.node_id || root.value,
    role: row.memberships?.[0]?.role || 'employee',
    remove: false,
  })
  memberOpen.value = true
}
/**
 * 提交组织任职的新增或移除申请；老板身份限定公司且受审批保护。
 */
async function saveMembership() {
  await action(async () => {
    await api('/users/' + memberForm.user_id + '/memberships', 'POST', {
      node_id: memberForm.role === 'boss' ? root.value : memberForm.node_id,
      role: memberForm.role,
      remove: memberForm.remove,
    })
    memberOpen.value = false
    ElMessage.success('任职调整已提交，等待审批')
    await load()
  })
}
/**
 * 申请切换账号状态；停用后的会话与任务授权由服务端撤销。
 * @param row - 拟调整状态的人员。
 */
async function disableUser(row: User) {
  await action(async () => {
    await api('/users/' + row.id, 'PATCH', { active: !row.active })
    ElMessage.success('账号状态变更已提交，等待老板审批')
    await load()
  })
}
/**
 * 收集临时密码并申请重置；受保护身份必须经老板批准，密码不写日志。
 * @param row - 拟申请密码重置的人员。
 */
async function resetPassword(row: User) {
  await action(async () => {
    const r = await ElMessageBox.prompt(
      '输入新的临时密码。正式环境须至少 12 位；批准后旧会话将失效。',
      '申请重置密码',
      { confirmButtonText: '提交申请', cancelButtonText: '取消', inputType: 'password' },
    )
    await api('/users/' + row.id, 'PATCH', { new_password: r.value })
    ElMessage.success('重置申请已提交，等待老板审批')
    await load()
  })
}
/**
 * 准备部门或小组表单；上级组织按公司结构限定。
 */
function newOrg() {
  Object.assign(orgForm, {
    id: '',
    name: '',
    kind: 'department',
    parent_id: root.value,
    active: true,
  })
  orgOpen.value = true
}
/**
 * 读取现有组织以申请调整；公司根节点由界面和后端限制修改。
 * @param row - 已有组织。
 */
function editOrg(row: Organization) {
  Object.assign(orgForm, {
    id: row.id,
    name: row.name,
    kind: row.kind,
    parent_id: row.parent_id,
    active: row.active,
  })
  orgOpen.value = true
}
/**
 * 创建组织或申请调整；组织变更后的业务访问范围需要负责人审批。
 */
async function saveOrg() {
  await action(async () => {
    if (orgForm.id)
      await api('/organizations/' + orgForm.id, 'PATCH', {
        name: orgForm.name,
        parent_id: orgForm.parent_id,
        active: orgForm.active,
      })
    else
      await api('/organizations', 'POST', {
        name: orgForm.name,
        kind: orgForm.kind,
        parent_id: orgForm.parent_id,
      })
    orgOpen.value = false
    ElMessage.success(orgForm.id ? '组织调整已提交，等待老板审批' : '组织已创建')
    await load()
  })
}
/**
 * 按负责人提供的编号申请阅读；响应不披露未授权资料是否存在。
 */
async function requestAccess() {
  await action(async () => {
    await api('/documents/' + encodeURIComponent(accessId.value) + '/request-access', 'POST')
    accessOpen.value = false
    ElMessage.success('访问申请已提交')
  })
}
/**
 * 管理员按编号读取授权配置；结果不含机密标题和正文。
 */
async function manageById() {
  await action(async () => {
    detail.value = await api<DocumentRecord>(
      '/documents/' + encodeURIComponent(permissionId.value) + '/permission-status',
    )
    permissionIdOpen.value = false
    showPolicy()
  })
}
/**
 * 将公开的审批字段转为中文摘要；不展开机密正文或凭据。
 * @param row - 后端按权限筛选的申请。
 * @returns 仅包含获准字段的中文摘要。
 */
function applicationSummary(row: Approval) {
  const p = row.payload || {},
    parts: string[] = []
  if (p.user_id) parts.push('人员：' + personName(p.user_id))
  if (p.role) parts.push((p.remove ? '移除任职：' : '任职：') + kind(p.role))
  if (p.node_id) parts.push('范围：' + nodeName(p.node_id))
  if (p.scope)
    parts.push(
      '共享方式：' +
        ({ company: '公司共享', org: '所属组织', selected: '指定人员' } as Record<string, string>)[
          p.scope
        ],
    )
  if (p.level) parts.push('密级：' + level(p.level))
  if ('ai_allowed' in p) parts.push('外部 AI 处理：' + (p.ai_allowed ? '允许' : '禁止'))
  if ('download_allowed' in p) parts.push('常规下载：' + (p.download_allowed ? '允许' : '禁止'))
  if ('can_download' in p) parts.push('单独下载：' + (p.can_download ? '允许' : '禁止'))
  if ('can_write' in p) parts.push('单独编辑：' + (p.can_write ? '允许' : '禁止'))
  if (p.effect) parts.push('访问：' + kind(p.effect))
  if (p.expires_at) parts.push('到期：' + date(p.expires_at))
  if (p.revoke) parts.push('撤销单独授权')
  if (p.reset_password) parts.push('重置临时密码')
  if ('active' in p) parts.push('状态：' + (p.active ? '启用' : '停用'))
  if ('system_admin' in p) parts.push('管理员身份：' + (p.system_admin ? '授予' : '移除'))
  if (p.name) parts.push('组织名称：' + p.name)
  return parts.join('；') || '确认当前资料版本并发布'
}
/**
 * 修改本人密码后清空状态并重新登录；旧会话立即撤销。
 */
async function changePassword() {
  await action(async () => {
    await api('/auth/password', 'POST', passwordForm)
    passwordForm.current_password = ''
    passwordForm.new_password = ''
    wipe()
    ElMessage.success('密码已修改，请重新登录')
  })
}
let timer: ReturnType<typeof setInterval>
onMounted(async () => {
  try {
    info.value = await api<WorkspaceInfo>('/info')
  } catch (e: unknown) {
    console.error('获取工作空间信息失败:', e)
  }
  try {
    user.value = await api<User>('/auth/me')
    askForm.input_level = user.value!.is_boss ? 3 : user.value!.is_leader ? 2 : 1
    askForm.allow_external = !user.value!.is_boss
    await load()
  } catch {}
  timer = setInterval(async () => {
    if (!user.value || user.value.must_change) return
    try {
      if (taskOpen.value && activeTask.value)
        activeTask.value = await api<Task>('/tasks/' + activeTask.value.id)
      if (docOpen.value && detail.value) {
        try {
          detail.value = await api<DocumentRecord>('/documents/' + detail.value.id)
        } catch {
          docOpen.value = false
          detail.value = null
        }
      }
    } catch {}
  }, 2500)
})
onUnmounted(() => {
  clearInterval(timer)
  taskStream?.close()
})
</script>

<template>
  <div v-if="!user" class="login-shell">
    <aside class="login-story">
      <div class="brand">
        <span class="brand-mark">协</span>
        <div>配件智协<small>企业智能协作工作台</small></div>
      </div>
      <div class="story-content">
        <span class="eyebrow">知识 · 业务 · 协作</span>
        <h1>每一份知识，<br />都有合适的边界。</h1>
        <p>从商品资料到经营决策，让团队在清晰的权限范围内协作，让智能体用可靠依据回答。</p>
        <div class="story-grid">
          <div>分级资料<span>员工 · 组长 · 老板</span></div>
          <div>组织协作<span>公司 · 部门 · 小组</span></div>
          <div>可靠回答<span>来源可查 · 权限可控</span></div>
          <div>操作留痕<span>授权申请 · 审核记录</span></div>
        </div>
      </div>
      <span class="story-foot">手机配件电商企业工作台</span>
    </aside>
    <main class="login-main">
      <div class="login-card">
        <el-tag v-if="info.demo" type="warning" effect="plain">独立演示环境</el-tag>
        <h2>登录工作台</h2>
        <p class="muted">使用企业账号继续</p>
        <el-form label-position="top" @submit.prevent="signIn">
          <el-form-item>
            <template #label>
              <div class="login-label-row">
                <span>账号</span>
                <span v-if="info.demo" class="quick-roles-inline">
                  快捷选择：
                  <a href="javascript:void(0)" @click.prevent="chooseRole('admin')">admin</a>
                  <span class="sep">/</span>
                  <a href="javascript:void(0)" @click.prevent="chooseRole('leader')">leader</a>
                  <span class="sep">/</span>
                  <a href="javascript:void(0)" @click.prevent="chooseRole('staff')">staff</a>
                  <span class="sep">/</span>
                  <a href="javascript:void(0)" @click.prevent="chooseRole('boss')">boss</a>
                </span>
              </div>
            </template>
            <el-input
              v-model="loginForm.username"
              placeholder="请输入账号 (如 admin / leader / staff / boss)"
              autocomplete="username"
              size="large"
            />
          </el-form-item>
          <el-form-item label="密码">
            <el-input
              v-model="loginForm.password"
              type="password"
              show-password
              placeholder="请输入密码 (演示环境统一 123456)"
              autocomplete="current-password"
              size="large"
              @keyup.enter="signIn"
            />
          </el-form-item>
          <el-button class="full" size="large" type="primary" :loading="busy" @click="signIn">
            登录
          </el-button>
        </el-form>
        <div v-if="info.demo" class="demo-box">
          <div class="demo-box-head">
            <strong>快捷填入演示账号</strong>
            <el-tag size="small" type="success" effect="light">密码统一为 123456</el-tag>
          </div>
          <p>点击下方身份一键填入账号与密码：</p>
          <div class="role-buttons">
            <el-button
              v-for="r in [
                { n: 'admin', l: '管理员 (admin)' },
                { n: 'leader', l: '组长 (leader)' },
                { n: 'staff', l: '员工 (staff)' },
                { n: 'boss', l: '老板 (boss)' },
              ]"
              :key="r.n"
              :type="loginForm.username === r.n ? 'primary' : 'default'"
              @click="chooseRole(r.n)"
              >{{ r.l }}</el-button
            >
          </div>
          <small>管理员维护系统与组织，业务机密需单独授权。</small>
        </div>
        <p class="login-foot">资料与答案均按当前身份授权访问</p>
      </div>
    </main>
  </div>
  <div v-else class="app-shell">
    <aside class="sidebar">
      <div class="brand">
        <span class="brand-mark">协</span>
        <div>配件智协<small>企业智能协作工作台</small></div>
      </div>
      <div class="workspace-label">企业空间</div>
      <div class="tenant-label">{{ info.name }}</div>
      <nav>
        <button
          v-for="name in menus"
          :key="name"
          :class="{ active: page === name }"
          @click="navigate(name)"
        >
          <span class="nav-glyph">{{ NAV_GLYPHS[name] }}</span
          >{{ name }}
        </button>
      </nav>
      <div class="sidebar-bottom">
        <span class="status-dot"></span>权限实时校验<small v-if="info.demo"
          >演示资料与正式数据隔离</small
        >
      </div>
    </aside>
    <div class="main-shell">
      <header class="topbar">
        <span>{{ page }}</span>
        <div class="topbar-right">
          <el-tag v-if="info.demo" type="warning" effect="plain">演示环境</el-tag
          ><span class="avatar">{{ user.display_name?.slice(0, 1) }}</span>
          <div class="user-info">
            {{ user.display_name }}<small>{{ user.roles.join(' / ') }}</small>
          </div>
          <el-button text @click="signOut">退出登录</el-button>
        </div>
      </header>
      <main v-loading="loading" class="content">
        <div class="page-heading">
          <div>
            <span class="eyebrow">企业智能协作</span>
            <h1>{{ title }}</h1>
            <p>{{ subtitles }}</p>
          </div>
          <el-button v-if="page === '文档库' && caps.write" type="primary" @click="newDoc"
            >新增资料</el-button
          ><el-button v-else @click="action(load)">刷新</el-button>
        </div>
        <template v-if="page === '工作台'">
          <div class="stat-grid">
            <div
              v-for="s in [
                { l: '可读资料', v: stats.documents, u: '份' },
                { l: '可见任务', v: stats.tasks, u: '个' },
                { l: '待业务审核', v: stats.review, u: '个' },
                { l: '已完成任务', v: stats.completed, u: '个' },
              ]"
              :key="s.l"
              class="stat-card"
            >
              <span>{{ s.l }}</span
              ><strong
                >{{ s.v || 0 }}<small>{{ s.u }}</small></strong
              >
              <div class="stat-line"></div>
            </div>
          </div>
          <div class="dashboard-grid">
            <section class="panel ask-promo">
              <el-tag effect="plain">基于授权资料回答</el-tag>
              <h2>今天，需要核对什么？</h2>
              <p>
                商品规格、手机适配、售后条件，或团队资料。智能体按问题协作，答案保留可访问的引用。
              </p>
              <el-input
                v-model="askForm.question"
                type="textarea"
                :rows="3"
                placeholder="例如：MC-500 的切幅是多少？"
              />
              <div class="panel-actions">
                <small>仅使用你有权查看的资料</small
                ><el-button type="primary" @click="page = '智能问答'">开始提问</el-button>
              </div>
            </section>
            <section class="panel">
              <h3>你的协作范围</h3>
              <div class="scope-card">
                <span>当前身份</span><strong>{{ user.roles.join(' / ') }}</strong>
              </div>
              <div class="scope-card">
                <span>所属团队</span
                ><strong>{{ stats.teams?.join('、') || '公司共享范围' }}</strong>
              </div>
              <div class="scope-card">
                <span>企业模型</span><strong>{{ stats.model_ready ? '已配置' : '待配置' }}</strong>
              </div>
              <div class="scope-card">
                <span>真实业务系统</span
                ><strong>{{ stats.business_ready ? '已配置' : '未接入' }}</strong>
              </div>
            </section>
          </div>
          <section class="panel">
            <div class="section-heading">
              <h3>近期资料</h3>
              <el-button text type="primary" @click="page = '文档库'">查看文档库</el-button>
            </div>
            <div class="document-cards">
              <button
                v-for="d in docs.slice(0, 4)"
                :key="d.id"
                class="document-card"
                @click="openDoc(d.id)"
              >
                <span class="doc-mark">文</span><strong>{{ d.title }}</strong
                ><span>{{ level(d.level) }} · {{ d.node_name }}</span
                ><small>{{ state(d.state) }}</small>
              </button>
            </div>
            <el-empty v-if="!docs.length" description="当前没有可访问资料" />
          </section>
        </template>
        <template v-else-if="page === '智能问答'"
          ><div class="question-grid">
            <section class="panel">
              <h3>描述你的问题</h3>
              <el-form label-position="top"
                ><el-form-item label="问题"
                  ><el-input
                    v-model="askForm.question"
                    type="textarea"
                    :rows="7"
                    maxlength="4000"
                    show-word-limit
                    placeholder="请说明商品型号、适配机型或需要核对的业务事项"
                /></el-form-item>
                <div class="form-columns">
                  <el-form-item label="核验深度"
                    ><el-select v-model="askForm.depth"
                      ><el-option label="快速查询" value="quick" /><el-option
                        label="标准核验"
                        value="standard" /><el-option
                        label="深入核验与人工审核"
                        value="deep" /></el-select></el-form-item
                  ><el-form-item label="问题内容密级"
                    ><el-select
                      v-model="askForm.input_level"
                      @change="askForm.allow_external = askForm.input_level < 3"
                      ><el-option
                        v-for="l in [1, 2, 3]"
                        :key="l"
                        :label="level(l)"
                        :value="l" /></el-select
                  ></el-form-item>
                </div>
                <div class="form-columns">
                  <el-form-item label="商品编号（选填）"
                    ><el-input
                      v-model="askForm.sku_id"
                      placeholder="接入库存系统后可查询" /></el-form-item
                  ><el-form-item label="订单编号（选填）"
                    ><el-input v-model="askForm.order_id" placeholder="接入订单系统后可查询"
                  /></el-form-item>
                </div>
                <el-checkbox v-model="askForm.require_review">请求业务负责人审核</el-checkbox
                ><el-checkbox v-model="askForm.allow_external"
                  >允许问题使用外部 AI（资料仍需获准）</el-checkbox
                ><el-alert
                  v-if="askForm.input_level === 3"
                  title="老板级资料默认禁止发送外部 AI；文档负责人须另行批准资料的 AI 处理权限。"
                  type="info"
                  :closable="false"
                  show-icon
                /><el-button
                  class="submit-question"
                  type="primary"
                  size="large"
                  :loading="busy"
                  @click="submitQuestion"
                  >提交问题</el-button
                ></el-form
              >
            </section>
            <section class="panel pipeline-panel">
              <h3>智能体如何协作</h3>
              <div
                v-for="(step, i) in [
                  '校验身份与组织范围',
                  '制定问题核验计划',
                  '检索授权资料和业务数据',
                  '调度专业智能体',
                  '检查事实、数字与引用',
                  '审核并发布可读答案',
                ]"
                :key="step"
                class="pipeline-step"
              >
                <span>{{ i + 1 }}</span>
                <div>
                  {{ step
                  }}<small>{{
                    [
                      '以服务端身份为准',
                      '只调度问题所需专家',
                      '缺少接口时明确说明',
                      '商品、适配、售后、运营等',
                      '不发布无依据的事实',
                      '发布前再次验证权限',
                    ][i]
                  }}</small>
                </div>
              </div>
            </section>
          </div></template
        >
        <template v-else-if="page === '任务中心' || page === '团队工作台'"
          ><section class="panel">
            <div class="section-heading">
              <h3>{{ page === '团队工作台' ? '团队任务与审核' : '我的可见任务' }}</h3>
              <el-button type="primary" @click="page = '智能问答'">提出新问题</el-button>
            </div>
            <el-table :data="jobs"
              ><el-table-column label="问题" min-width="290"
                ><template #default="{ row }"
                  ><el-button
                    text
                    type="primary"
                    class="table-question"
                    @click="openTask(row.id)"
                    >{{ row.question }}</el-button
                  ></template
                ></el-table-column
              ><el-table-column label="状态" width="130"
                ><template #default="{ row }"
                  ><el-tag
                    :type="
                      row.state === 'completed'
                        ? 'success'
                        : row.state === 'failed'
                          ? 'danger'
                          : 'info'
                    "
                    >{{ state(row.state) }}</el-tag
                  ></template
                ></el-table-column
              ><el-table-column label="当前阶段" prop="stage" min-width="150" /><el-table-column
                label="范围"
                width="140"
                ><template #default="{ row }">{{
                  nodeName(row.node_id)
                }}</template></el-table-column
              ><el-table-column label="密级" width="100"
                ><template #default="{ row }">{{
                  level(row.input_level)
                }}</template></el-table-column
              ><el-table-column label="提交时间" width="190"
                ><template #default="{ row }">{{ date(row.created_at) }}</template></el-table-column
              ></el-table
            ><el-empty v-if="!jobs.length" description="暂时没有可见任务" /></section
        ></template>
        <template v-else-if="page === '文档库'"
          ><section class="panel">
            <div class="section-heading">
              <el-input
                v-model="searchTerm"
                class="search-box"
                placeholder="搜索可访问资料"
                clearable
              />
              <div>
                <el-button v-if="caps.system" @click="permissionIdOpen = true"
                  >按编号配置权限</el-button
                ><el-button @click="accessOpen = true">申请资料访问</el-button>
              </div>
            </div>
            <el-table :data="filteredDocs"
              ><el-table-column label="资料名称" min-width="300"
                ><template #default="{ row }"
                  ><el-button text type="primary" class="table-question" @click="openDoc(row.id)">{{
                    row.title
                  }}</el-button></template
                ></el-table-column
              ><el-table-column label="组织范围" prop="node_name" min-width="150" /><el-table-column
                label="密级"
                width="110"
                ><template #default="{ row }"
                  ><el-tag
                    :type="row.level === 3 ? 'danger' : row.level === 2 ? 'warning' : 'info'"
                    >{{ level(row.level) }}</el-tag
                  ></template
                ></el-table-column
              ><el-table-column label="版本" width="80"
                ><template #default="{ row }"
                  >第 {{ row.current_version }} 版</template
                ></el-table-column
              ><el-table-column label="状态" width="120"
                ><template #default="{ row }">{{ state(row.state) }}</template></el-table-column
              ><el-table-column label="操作" width="160"
                ><template #default="{ row }"
                  ><el-button text @click="openDoc(row.id)">查看</el-button
                  ><el-button
                    v-if="row.can_download"
                    text
                    tag="a"
                    :href="'/api/documents/' + row.id + '/download'"
                    >下载</el-button
                  ></template
                ></el-table-column
              ></el-table
            ><el-empty v-if="!filteredDocs.length" description="当前没有匹配的授权资料" /></section
        ></template>
        <template v-else-if="page === '审核与授权'"
          ><section class="panel">
            <el-alert
              title="批准申请不会改变其他人的权限。资料版本或人员状态变化后，旧申请须重新提交。"
              type="info"
              :closable="false"
            /><el-table :data="requests"
              ><el-table-column type="expand"
                ><template #default="{ row }"
                  ><p class="application-summary">{{ applicationSummary(row) }}</p></template
                ></el-table-column
              ><el-table-column label="申请类型" prop="label" width="140" /><el-table-column
                label="相关资料或人员"
                min-width="240"
                ><template #default="{ row }"
                  >{{ row.title || personName(row.payload?.user_id || row.target_id)
                  }}<small class="cell-small">{{
                    row.title
                      ? ''
                      : row.kind === 'membership'
                        ? nodeName(row.payload.node_id) + ' · ' + kind(row.payload.role)
                        : '受限详情按当前权限展示'
                  }}</small></template
                ></el-table-column
              ><el-table-column label="申请人" width="130"
                ><template #default="{ row }">{{
                  personName(row.requested_by)
                }}</template></el-table-column
              ><el-table-column label="审批身份" width="110"
                ><template #default="{ row }">{{
                  row.required_role === 'boss' ? '老板' : '团队负责人'
                }}</template></el-table-column
              ><el-table-column label="状态" width="100"
                ><template #default="{ row }">{{ state(row.state) }}</template></el-table-column
              ><el-table-column label="操作" width="170"
                ><template #default="{ row }"
                  ><template v-if="row.can_decide && row.state === 'pending'"
                    ><el-button text type="primary" @click="decideRequest(row, true)"
                      >批准</el-button
                    ><el-button text type="danger" @click="decideRequest(row, false)"
                      >拒绝</el-button
                    ></template
                  ><span v-else class="muted">等待或已处理</span></template
                ></el-table-column
              ></el-table
            ><el-empty v-if="!requests.length" description="暂时没有申请" /></section
        ></template>
        <template v-else-if="page === '组织与人员'"
          ><div class="organization-grid">
            <section class="panel">
              <div class="section-heading">
                <h3>公司组织架构</h3>
                <el-button text type="primary" @click="newOrg">新增组织</el-button>
              </div>
              <el-tree :data="tree" node-key="id" default-expand-all :expand-on-click-node="false"
                ><template #default="{ data }"
                  ><div class="org-tree-row">
                    <span>{{ data.name }}</span
                    ><el-tag size="small" type="info">{{ kind(data.kind) }}</el-tag
                    ><el-button
                      v-if="data.kind !== 'company'"
                      text
                      size="small"
                      @click="editOrg(data)"
                      >调整</el-button
                    >
                  </div></template
                ></el-tree
              >
            </section>
            <section class="panel">
              <div class="section-heading">
                <h3>人员与任职</h3>
                <el-button type="primary" @click="newAccount">新增账号</el-button>
              </div>
              <el-table :data="people"
                ><el-table-column label="姓名 / 账号" min-width="160"
                  ><template #default="{ row }"
                    >{{ row.display_name
                    }}<small class="cell-small">{{ row.username }}</small></template
                  ></el-table-column
                ><el-table-column label="任职与范围" min-width="210"
                  ><template #default="{ row }"
                    ><el-tag v-if="row.system_admin" size="small">管理员</el-tag>
                    <div v-for="m in row.memberships" :key="m.node_id + m.role">
                      {{ nodeName(m.node_id) }} · {{ kind(m.role) }}
                    </div>
                    <span v-if="!row.memberships?.length && !row.system_admin"
                      >待分配</span
                    ></template
                  ></el-table-column
                ><el-table-column label="状态" width="80"
                  ><template #default="{ row }">{{
                    row.active ? '正常' : '已停用'
                  }}</template></el-table-column
                ><el-table-column label="管理" width="220"
                  ><template #default="{ row }"
                    ><el-button text @click="membership(row)">任职</el-button
                    ><el-button text @click="resetPassword(row)">重置</el-button
                    ><el-button
                      text
                      :type="row.active ? 'danger' : 'primary'"
                      @click="disableUser(row)"
                      >{{ row.active ? '停用' : '启用' }}</el-button
                    ></template
                  ></el-table-column
                ></el-table
              >
            </section>
          </div></template
        >
        <template v-else-if="page === '审计记录'"
          ><section class="panel">
            <el-table :data="audits"
              ><el-table-column label="时间" width="190"
                ><template #default="{ row }">{{ date(row.created_at) }}</template></el-table-column
              ><el-table-column label="操作" prop="action" min-width="220" /><el-table-column
                label="操作者"
                min-width="140"
                ><template #default="{ row }">{{
                  personName(row.actor_id)
                }}</template></el-table-column
              ><el-table-column label="审计范围" width="130"
                ><template #default="{ row }">{{
                  row.system ? '系统运行' : '业务操作'
                }}</template></el-table-column
              ><el-table-column label="密级" width="110"
                ><template #default="{ row }">{{ level(row.level) }}</template></el-table-column
              ><el-table-column label="组织" min-width="160"
                ><template #default="{ row }">{{
                  nodeName(row.node_id)
                }}</template></el-table-column
              ></el-table
            ><el-empty v-if="!audits.length" description="当前没有可见审计记录" /></section
        ></template>
        <template v-else-if="page === '运行状态'"
          ><section class="panel">
            <h3>服务状态</h3>
            <div class="stat-grid">
              <div
                v-for="s in [
                  { l: '企业数据库', v: runtimeStatus.database ? '已连接' : '待连接' },
                  { l: '任务进程', v: runtimeStatus.worker_available ? '运行中' : '待恢复' },
                  { l: '企业模型', v: runtimeStatus.model_configured ? '已配置' : '待配置' },
                  { l: '真实业务系统', v: runtimeStatus.business_configured ? '已配置' : '未接入' },
                ]"
                :key="s.l"
                class="stat-card"
              >
                <span>{{ s.l }}</span
                ><strong style="font-size: 22px">{{ s.v }}</strong>
              </div>
            </div>
            <el-table :data="runtimeStatus.counts || []"
              ><el-table-column label="任务状态"
                ><template #default="{ row }">{{ state(row.state) }}</template></el-table-column
              ><el-table-column label="本企业数量" prop="count"
            /></el-table></section
        ></template>
        <template v-else-if="page === '个人设置'"
          ><section class="panel profile-panel">
            <h3>{{ user.must_change ? '首次登录，请修改初始密码' : '个人账号' }}</h3>
            <div class="profile-details">
              <span>姓名</span><strong>{{ user.display_name }}</strong
              ><span>账号</span><strong>{{ user.username }}</strong
              ><span>身份</span><strong>{{ user.roles.join(' / ') }}</strong>
            </div>
            <el-divider /><el-form label-position="top"
              ><el-form-item label="原密码"
                ><el-input
                  v-model="passwordForm.current_password"
                  type="password"
                  show-password /></el-form-item
              ><el-form-item label="新密码"
                ><el-input
                  v-model="passwordForm.new_password"
                  type="password"
                  show-password
                  :placeholder="
                    info.demo ? '至少 6 位' : '至少 12 位，避免常见密码'
                  " /></el-form-item
              ><el-button type="primary" :loading="busy" @click="changePassword"
                >修改密码并重新登录</el-button
              ></el-form
            >
          </section></template
        >
        <footer class="content-footer">资料范围由服务端校验 · 所有业务答案保留来源</footer>
      </main>
    </div>
    <el-dialog v-model="docOpen" :title="detail?.title || '资料详情'" width="760px"
      ><template v-if="detail"
        ><div class="detail-meta">
          <el-tag>{{ level(detail.level) }}</el-tag
          ><span>{{ nodeName(detail.node_id) }}</span
          ><span>第 {{ detail.current_version }} 版</span><span>{{ state(detail.state) }}</span>
        </div>
        <el-alert v-if="detail.error" :title="detail.error" type="error" :closable="false" />
        <p class="muted" style="font-size: 11px">资料编号：{{ detail.id }}</p>
        <pre class="document-body">{{ detail.body }}</pre>
        <div class="dialog-actions">
          <el-button v-if="detail.can_write" @click="editDoc">编辑资料</el-button
          ><el-button @click="showVersions">版本记录</el-button
          ><el-button v-if="caps.permissions" @click="showPolicy">权限设置</el-button
          ><el-button v-if="caps.permissions" @click="showGrant">单文档授权</el-button
          ><el-button
            v-if="detail.can_download"
            tag="a"
            :href="'/api/documents/' + detail.id + '/download'"
            >下载</el-button
          ><el-button v-if="detail.can_write" type="danger" plain @click="deleteDoc"
            >删除</el-button
          >
        </div></template
      ></el-dialog
    >
    <el-dialog v-model="taskOpen" title="任务详情" width="860px"
      ><template v-if="activeTask"
        ><h3>{{ activeTask.question }}</h3>
        <div class="detail-meta">
          <el-tag>{{ state(activeTask.state) }}</el-tag
          ><span>{{ activeTask.stage }}</span
          ><span>{{ level(activeTask.input_level) }}</span>
        </div>
        <el-progress
          :percentage="activeTask.progress"
          :status="activeTask.state === 'completed' ? 'success' : undefined"
        /><el-alert
          v-if="activeTask.error"
          :title="activeTask.error"
          type="error"
          :closable="false"
        /><el-alert
          v-if="activeTask.restricted"
          title="来源权限或版本已变化，历史答案不可读取，请重新查询。"
          type="warning"
          :closable="false"
        /><template v-if="activeTask.result"
          ><h3>多智能体协同核验结果</h3>
          <div class="detail-meta">
            <el-tag type="primary" effect="dark">🎯 Main 调度</el-tag>
            <el-tag v-for="s in activeTask.result.specialists || []" :key="s.name" effect="plain"
              >{{ s.name }} ·
              {{ s.count ? '已核验 ' + s.count + ' 条原文' : '暂无支持结论' }}</el-tag
            >
            <el-tag type="success" effect="dark">🔍 Verifier 终审</el-tag>
          </div>
          <pre class="answer-body">{{ activeTask.result.answer }}</pre>
          <div v-if="activeTask.result.missing?.length" class="missing-box">
            <strong>尚需确认</strong>
            <p v-for="m in activeTask.result.missing" :key="m">{{ m }}</p>
          </div>
          <h3 v-if="activeTask.result.citations?.length">引用来源</h3>
          <el-collapse
            ><el-collapse-item
              v-for="(c, i) in activeTask.result.citations"
              :key="c.chunk_id"
              :title="'[' + (Number(i) + 1) + '] ' + c.title + ' · 第 ' + c.version + ' 版'"
            >
              <pre class="quote-body">{{ c.quote }}</pre>
              <el-button text type="primary" @click="openDoc(c.document_id)"
                >查看授权原文</el-button
              ></el-collapse-item
            ></el-collapse
          ></template
        ><el-empty v-else-if="activeTask.state === 'review'" description="答案等待业务负责人审核" />
        <div class="dialog-actions">
          <template v-if="activeTask.state === 'review' && activeTask.can_review"
            ><el-button type="primary" @click="reviewTask(true)">批准发布</el-button
            ><el-button type="danger" plain @click="reviewTask(false)"
              >拒绝发布</el-button
            ></template
          ><el-button
            v-if="
              ['queued', 'running', 'review'].includes(activeTask.state) && activeTask.can_cancel
            "
            @click="cancelTask"
            >取消任务</el-button
          >
        </div></template
      ></el-dialog
    >
    <el-dialog
      v-model="docEdit"
      :title="docForm.id ? '编辑资料新版本' : '新增资料草稿'"
      width="700px"
      ><el-form label-position="top"
        ><el-form-item label="资料名称"
          ><el-input v-model="docForm.title" maxlength="160"
        /></el-form-item>
        <div v-if="!docForm.id" class="form-columns">
          <el-form-item label="密级"
            ><el-select v-model="docForm.level"
              ><el-option
                v-for="l in [1, 2, 3]"
                :key="l"
                :label="level(l)"
                :value="l" /></el-select></el-form-item
          ><el-form-item label="可见范围"
            ><el-select
              v-model="docForm.scope"
              @change="docForm.scope === 'company' && (docForm.node_id = root)"
              ><el-option label="所属组织" value="org" /><el-option
                label="公司共享"
                value="company" /><el-option label="仅指定人员" value="selected" /></el-select
          ></el-form-item>
        </div>
        <el-form-item v-if="!docForm.id" label="归属组织"
          ><el-select v-model="docForm.node_id" class="full"
            ><el-option v-for="n in orgs" :key="n.id" :label="n.name" :value="n.id" /></el-select
        ></el-form-item>
        <div v-if="!docForm.id" class="upload-row">
          <input
            ref="fileInput"
            type="file"
            accept=".pdf,.docx,.txt,.md"
            hidden
            @change="picked"
          /><el-button @click="fileInput?.click()">选择附件</el-button
          ><span>{{ selectedFile?.name || '支持 PDF、Word、文本；最大 20 MB' }}</span>
        </div>
        <el-form-item v-if="!selectedFile" label="正文"
          ><el-input
            v-model="docForm.body"
            type="textarea"
            :rows="10"
            placeholder="粘贴经过确认的企业资料" /></el-form-item
        ><el-alert
          title="新资料为受限草稿。解析、确认密级和发布审核完成前，不对员工开放。"
          type="info"
          :closable="false" /></el-form
      ><template #footer
        ><el-button @click="docEdit = false">取消</el-button
        ><el-button type="primary" :loading="busy" @click="saveDoc"
          >保存并提交解析</el-button
        ></template
      ></el-dialog
    >
    <el-dialog v-model="policyOpen" title="资料权限设置" width="560px"
      ><el-form label-position="top"
        ><el-form-item label="密级"
          ><el-select
            v-model="policyForm.level"
            @change="policyForm.level === 3 && (policyForm.ai_allowed = false)"
            ><el-option
              v-for="l in [1, 2, 3]"
              :key="l"
              :label="level(l)"
              :value="l" /></el-select></el-form-item
        ><el-form-item label="范围"
          ><el-select v-model="policyForm.scope"
            ><el-option label="所属组织" value="org" /><el-option
              label="公司共享"
              value="company" /><el-option
              label="仅指定人员"
              value="selected" /></el-select></el-form-item
        ><el-form-item label="归属组织"
          ><el-select v-model="policyForm.node_id"
            ><el-option
              v-for="n in orgs"
              :key="n.id"
              :label="n.name"
              :value="n.id" /></el-select></el-form-item
        ><el-checkbox v-model="policyForm.ai_allowed">允许授权资料发送外部 AI 处理</el-checkbox
        ><el-checkbox v-model="policyForm.download_allowed"
          >允许符合常规阅读权限的人员下载</el-checkbox
        ><el-alert
          title="密级调整、扩大可见范围和外部 AI 许可须老板审批。"
          type="warning"
          :closable="false" /></el-form
      ><template #footer
        ><el-button @click="policyOpen = false">取消</el-button
        ><el-button type="primary" @click="savePolicy">提交审批</el-button></template
      ></el-dialog
    >
    <el-dialog v-model="grantOpen" title="单文档授权申请" width="560px"
      ><div v-if="currentGrants.length" class="missing-box">
        <strong>当前单独授权</strong>
        <p v-for="g in currentGrants" :key="g.user_id">
          {{ g.display_name }} · {{ kind(g.effect) }} ·
          {{ g.can_download ? '可下载' : '不可下载' }} · 只读 ·
          {{ g.expires_at ? date(g.expires_at) : '长期' }}
        </p>
      </div>
      <el-form label-position="top"
        ><el-form-item label="授权人员"
          ><el-select v-model="grantForm.user_id"
            ><el-option
              v-for="u in people"
              :key="u.id"
              :label="u.display_name + '（' + u.username + '）'"
              :value="u.id" /></el-select></el-form-item
        ><el-form-item label="授权方式"
          ><el-select v-model="grantForm.effect"
            ><el-option label="允许读取" value="allow" /><el-option
              label="明确禁止访问"
              value="deny" /></el-select></el-form-item
        ><el-form-item label="有效期（留空表示长期）"
          ><el-date-picker
            v-model="grantForm.expires_at"
            type="datetime"
            placeholder="选择到期时间" /></el-form-item
        ><el-checkbox v-model="grantForm.can_download">允许下载</el-checkbox
        ><el-checkbox v-model="grantForm.revoke">撤销该人员的单独授权</el-checkbox
        ><el-alert
          title="默认仅授予此份资料的阅读权限，不提升人员角色或其他资料权限。"
          type="info"
          :closable="false" /></el-form
      ><template #footer
        ><el-button @click="grantOpen = false">取消</el-button
        ><el-button type="primary" @click="saveGrant">提交审批</el-button></template
      ></el-dialog
    >
    <el-dialog v-model="historyOpen" title="资料版本记录" width="760px"
      ><el-collapse
        ><el-collapse-item
          v-for="v in history"
          :key="v.version"
          :title="'第 ' + v.version + ' 版 · ' + date(v.created_at)"
        >
          <pre class="document-body">{{ v.body }}</pre>
        </el-collapse-item></el-collapse
      ></el-dialog
    >
    <el-dialog v-model="accountOpen" title="新增企业账号" width="560px"
      ><el-form label-position="top"
        ><el-form-item label="姓名"><el-input v-model="accountForm.display_name" /></el-form-item
        ><el-form-item label="账号"><el-input v-model="accountForm.username" /></el-form-item
        ><el-form-item label="初始密码"
          ><el-input v-model="accountForm.password" type="password" show-password /></el-form-item
        ><el-form-item label="申请身份"
          ><el-select v-model="accountForm.role"
            ><el-option
              v-for="r in ['employee', 'leader', 'boss', 'admin']"
              :key="r"
              :label="r === 'admin' ? '管理员' : kind(r)"
              :value="r" /></el-select></el-form-item
        ><el-form-item label="任职组织"
          ><el-select v-model="accountForm.node_id"
            ><el-option
              v-for="n in orgs"
              :key="n.id"
              :label="n.name"
              :value="n.id" /></el-select></el-form-item
        ><el-alert
          title="账号创建后，任职或管理员身份须完成审批；不能通过创建账号自动取得机密权限。"
          type="info"
          :closable="false" /></el-form
      ><template #footer
        ><el-button @click="accountOpen = false">取消</el-button
        ><el-button type="primary" @click="saveAccount">创建并申请任职</el-button></template
      ></el-dialog
    >
    <el-dialog v-model="memberOpen" title="组织任职调整" width="560px"
      ><el-form label-position="top"
        ><el-form-item label="人员"
          ><el-input :model-value="personName(memberForm.user_id)" disabled /></el-form-item
        ><el-form-item label="组织"
          ><el-select v-model="memberForm.node_id"
            ><el-option
              v-for="n in orgs"
              :key="n.id"
              :label="n.name"
              :value="n.id" /></el-select></el-form-item
        ><el-form-item label="身份"
          ><el-select v-model="memberForm.role"
            ><el-option
              v-for="r in ['employee', 'leader', 'boss']"
              :key="r"
              :label="kind(r)"
              :value="r" /></el-select></el-form-item
        ><el-checkbox v-model="memberForm.remove">移除此项任职</el-checkbox></el-form
      ><template #footer
        ><el-button @click="memberOpen = false">取消</el-button
        ><el-button type="primary" @click="saveMembership">提交审批</el-button></template
      ></el-dialog
    >
    <el-dialog v-model="orgOpen" :title="orgForm.id ? '调整组织' : '新增组织'" width="560px"
      ><el-form label-position="top"
        ><el-form-item label="名称"><el-input v-model="orgForm.name" /></el-form-item
        ><el-form-item label="组织类型"
          ><el-select v-model="orgForm.kind" :disabled="!!orgForm.id"
            ><el-option label="部门" value="department" /><el-option
              label="小组"
              value="team" /></el-select></el-form-item
        ><el-form-item label="上级组织"
          ><el-select v-model="orgForm.parent_id"
            ><el-option
              v-for="n in orgs.filter((x) =>
                orgForm.kind === 'department' ? x.kind === 'company' : x.kind === 'department',
              )"
              :key="n.id"
              :label="n.name"
              :value="n.id" /></el-select></el-form-item
        ><el-checkbox v-if="orgForm.id" v-model="orgForm.active">启用组织</el-checkbox></el-form
      ><template #footer
        ><el-button @click="orgOpen = false">取消</el-button
        ><el-button type="primary" @click="saveOrg">{{
          orgForm.id ? '提交调整审批' : '创建组织'
        }}</el-button></template
      ></el-dialog
    >
    <el-dialog v-model="permissionIdOpen" title="按资料编号配置权限" width="500px"
      ><p class="muted">请输入老板提供的资料编号。这里仅查询授权配置，不开放机密正文或标题。</p>
      <el-input v-model="permissionId" placeholder="资料编号" /><template #footer
        ><el-button @click="permissionIdOpen = false">取消</el-button
        ><el-button type="primary" @click="manageById">查询权限配置</el-button></template
      ></el-dialog
    >
    <el-dialog v-model="accessOpen" title="申请资料访问" width="500px"
      ><p class="muted">请输入资料负责人提供的资料编号。系统不会公开未授权文档的名称和内容。</p>
      <el-input v-model="accessId" placeholder="资料编号" /><template #footer
        ><el-button @click="accessOpen = false">取消</el-button
        ><el-button type="primary" @click="requestAccess">提交申请</el-button></template
      ></el-dialog
    >
  </div>
</template>
