<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { levelText, stateText } from '../../shared/labels'
import type { ApiClient, CreatedResource, Items } from '../../shared/apiTypes'
import type {
  AdministrationPage,
  Approval,
  AuditEvent,
  ManagedUser,
  Organization,
  OrganizationTree,
  RuntimeStatus,
} from './types'
import PolicyMatrix from './PolicyMatrix.vue'

const props = defineProps<{
  page: AdministrationPage
  api: ApiClient
  action: (operation: () => Promise<void>) => Promise<void>
  refresh: () => Promise<void>
  busy: boolean
  organizations: Organization[]
  approvals: Approval[]
  users: ManagedUser[]
  rootOrganizationId: string
}>()

const audits = ref<AuditEvent[]>([])
const runtimeStatus = ref<RuntimeStatus | null>(null)
const accountOpen = ref(false)
const accountForm = reactive({
  username: '',
  password: '',
  display_name: '',
  role: 'employee',
  node_id: '',
})

const memberOpen = ref(false)
const memberForm = reactive({ user_id: '', node_id: '', role: 'employee', remove: false })

const resetOpen = ref(false)
const resetForm = reactive({ user_id: '', username: '', display_name: '', new_password: '' })

const orgOpen = ref(false)
const orgForm = reactive({ id: '', name: '', kind: 'department', parent_id: '', active: true })

/**
 * 将组织平铺列表构造成前端树形控件结构。
 * @param parentId - 父级组织 ID。
 * @returns 递归嵌套组织树节点数组。
 */
function buildTree(parentId: string | null): OrganizationTree[] {
  const result: OrganizationTree[] = []
  for (const organization of props.organizations) {
    if (organization.parent_id !== parentId) continue
    result.push({ ...organization, label: organization.name, children: buildTree(organization.id) })
  }
  return result
}
const tree = computed(
  /**
   * 生成根节点起点的企业组织架构树。
   * @returns 组织树。
   */
  () => buildTree(null),
)

/**
 * 将组织节点编号解析为可读部门名称。
 * @param nodeId 组织节点编号。
 * @returns 部门名称，未命中时回退编号或占位符。
 */
function orgName(nodeId?: string | null): string {
  if (!nodeId) return '—'
  return props.organizations.find((organization) => organization.id === nodeId)?.name || nodeId
}

/**
 * 审批或驳回待处理申请。
 * @param row - 审批记录。
 * @param approve - 是否通过。
 */
async function decideRequest(row: Approval, approve: boolean): Promise<void> {
  await props.action(async () => {
    await props.api('/approvals/' + row.id + '/decision', 'POST', { approve, comment: '' })
    ElMessage.success(approve ? '已审批通过' : '已驳回申请')
    await props.refresh()
  })
}

/**
 * 初始化新建成员账号表单。
 */
function newAccount(): void {
  Object.assign(accountForm, {
    username: '',
    password: '',
    display_name: '',
    role: 'employee',
    node_id: props.rootOrganizationId,
  })
  accountOpen.value = true
}

/**
 * 新建企业成员账号并提交初始部门/角色审批。
 */
async function saveAccount(): Promise<void> {
  await props.action(async () => {
    const r = await props.api<CreatedResource>('/users', 'POST', {
      username: accountForm.username,
      password: accountForm.password,
      display_name: accountForm.display_name,
    })
    if (accountForm.role === 'admin') {
      await props.api('/users/' + r.id, 'PATCH', { system_admin: true })
    } else {
      await props.api('/users/' + r.id + '/memberships', 'POST', {
        node_id: accountForm.role === 'boss' ? props.rootOrganizationId : accountForm.node_id,
        role: accountForm.role,
        remove: false,
      })
    }
    accountOpen.value = false
    accountForm.password = ''
    ElMessage.success('成员账号已创建，部门分配待审批')
    await props.refresh()
  })
}

/**
 * 打开成员部门调整弹窗。
 * @param row - 目标成员对象。
 */
function membership(row: ManagedUser): void {
  Object.assign(memberForm, {
    user_id: row.id,
    node_id: row.memberships?.[0]?.node_id || props.rootOrganizationId,
    role: row.memberships?.[0]?.role || 'employee',
    remove: false,
  })
  memberOpen.value = true
}

/**
 * 提交成员部门调整或移出申请。
 */
async function saveMembership(): Promise<void> {
  await props.action(async () => {
    await props.api('/users/' + memberForm.user_id + '/memberships', 'POST', {
      node_id: memberForm.role === 'boss' ? props.rootOrganizationId : memberForm.node_id,
      role: memberForm.role,
      remove: memberForm.remove,
    })
    memberOpen.value = false
    ElMessage.success('部门调整申请已提交')
    await props.refresh()
  })
}

/**
 * 确认后申请停用或启用成员账号。
 * @param row - 目标成员对象。
 */
async function disableUser(row: ManagedUser): Promise<void> {
  await props.action(async () => {
    const targetAction = row.active ? '停用' : '启用'
    await ElMessageBox.confirm(
      `确定申请${targetAction}成员「${row.display_name}」(${row.username}) 吗？账号停用后将自动注销其当前登录会话。`,
      `${targetAction}账号`,
      { confirmButtonText: `确定${targetAction}`, cancelButtonText: '取消', type: 'warning' },
    )
    await props.api('/users/' + row.id, 'PATCH', { active: !row.active })
    ElMessage.success(`账号${targetAction}申请已提交审批`)
    await props.refresh()
  })
}

/**
 * 打开重置成员密码弹窗。
 * @param row - 目标成员对象。
 */
function openResetDialog(row: ManagedUser): void {
  Object.assign(resetForm, {
    user_id: row.id,
    username: row.username,
    display_name: row.display_name,
    new_password: '',
  })
  resetOpen.value = true
}

/**
 * 提交重置临时密码审批请求。
 */
async function submitResetPassword(): Promise<void> {
  if (!resetForm.new_password) {
    ElMessage.warning('请输入新的临时密码')
    return
  }
  await props.action(async () => {
    await props.api('/users/' + resetForm.user_id, 'PATCH', {
      new_password: resetForm.new_password,
    })
    resetForm.new_password = ''
    resetOpen.value = false
    ElMessage.success('密码重置申请已提交，等待审批')
    await props.refresh()
  })
}

/**
 * 初始化新建部门表单。
 */
function newOrg(): void {
  Object.assign(orgForm, {
    id: '',
    name: '',
    kind: 'department',
    parent_id: props.rootOrganizationId,
    active: true,
  })
  orgOpen.value = true
}

/**
 * 载入现有部门信息准备编辑。
 * @param row - 目标部门对象。
 */
function editOrg(row: Organization): void {
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
 * 提交部门新建或调整申请。
 */
async function saveOrg(): Promise<void> {
  await props.action(async () => {
    if (orgForm.id) {
      await props.api('/organizations/' + orgForm.id, 'PATCH', {
        name: orgForm.name,
        parent_id: orgForm.parent_id,
        active: orgForm.active,
      })
    } else {
      await props.api('/organizations', 'POST', {
        name: orgForm.name,
        kind: orgForm.kind,
        parent_id: orgForm.parent_id,
      })
    }
    orgOpen.value = false
    ElMessage.success(orgForm.id ? '部门调整申请已提交' : '部门已创建')
    await props.refresh()
  })
}

onMounted(async () => {
  if (props.page === '系统状态') {
    await props.action(async () => {
      runtimeStatus.value = await props.api<RuntimeStatus>('/system/status')
    })
  } else if (props.page === '操作日志') {
    await props.action(async () => {
      audits.value = (await props.api<Items<AuditEvent>>('/audit')).items
    })
  }
})
</script>

<template>
  <PolicyMatrix
    v-if="page === '权限审批'"
    mode="approvals"
    :approvals="approvals"
    :tree="tree"
    :users="users"
    @decide="decideRequest"
  />

  <PolicyMatrix
    v-else-if="page === '组织与成员'"
    mode="org"
    :approvals="approvals"
    :tree="tree"
    :users="users"
    @new-org="newOrg"
    @edit-org="editOrg"
    @new-account="newAccount"
    @membership="membership"
    @reset-password="openResetDialog"
    @disable-user="disableUser"
  />

  <template v-else-if="page === '操作日志'">
    <section class="panel">
      <div class="section-head">
        <div>
          <h3>操作审计日志</h3>
          <p class="section-sub">记录系统登录、文档访问、权限调整及审批决策历史。</p>
        </div>
      </div>
      <el-table :data="audits" empty-text="暂无操作日志">
        <el-table-column prop="created_at" label="时间" width="195" />
        <el-table-column prop="action" label="操作行为" min-width="200" />
        <el-table-column prop="actor_id" label="操作账号" min-width="150" />
        <el-table-column label="关联部门" min-width="140">
          <template #default="{ row }">
            {{ orgName(row.node_id) }}
          </template>
        </el-table-column>
        <el-table-column label="密级" width="110">
          <template #default="{ row }">
            <span class="level-pill" :class="`level-${row.level}`">
              {{ levelText(row.level) }}
            </span>
          </template>
        </el-table-column>
      </el-table>
    </section>
  </template>

  <template v-else-if="page === '系统状态'">
    <section class="runtime-grid">
      <div class="panel">
        <h3>服务连接状态</h3>
        <div class="scope-card">
          <span>主数据库与向量检索</span>
          <span
            class="status-pill"
            :class="runtimeStatus?.database ? 'status-active' : 'status-disabled'"
          >
            <span class="status-dot" />
            {{ runtimeStatus?.database ? '正常' : '异常' }}
          </span>
        </div>
        <div class="scope-card">
          <span>后台任务执行服务</span>
          <span
            class="status-pill"
            :class="runtimeStatus?.worker_available ? 'status-active' : 'status-disabled'"
          >
            <span class="status-dot" />
            {{ runtimeStatus?.worker_available ? '在线' : '离线' }}
          </span>
        </div>
        <div class="scope-card">
          <span>AI 分析模型配置</span>
          <span
            class="status-pill"
            :class="runtimeStatus?.model_configured ? 'status-active' : 'status-disabled'"
          >
            <span class="status-dot" />
            {{ runtimeStatus?.model_configured ? '已配置' : '未配置' }}
          </span>
        </div>
        <div class="scope-card">
          <span>经营分析数据源</span>
          <span
            class="status-pill"
            :class="runtimeStatus?.business_configured ? 'status-active' : 'status-disabled'"
          >
            <span class="status-dot" />
            {{ runtimeStatus?.business_configured ? '已连接' : '未配置' }}
          </span>
        </div>
      </div>

      <div class="panel">
        <h3>任务状态统计</h3>
        <div v-for="c in runtimeStatus?.counts || []" :key="c.state" class="scope-card">
          <span>{{ stateText(c.state) }}</span>
          <strong>{{ c.count }} 项</strong>
        </div>
      </div>
    </section>
  </template>

  <el-dialog v-model="accountOpen" title="新建成员账号" width="540px">
    <div class="dialog-lead">创建成员账号后，系统将同步提交初始部门分配或管理员角色审批申请。</div>
    <el-form label-position="top">
      <div class="form-columns">
        <el-form-item label="姓名">
          <el-input v-model="accountForm.display_name" placeholder="请输入成员姓名" />
        </el-form-item>
        <el-form-item label="登录账号">
          <el-input v-model="accountForm.username" placeholder="建议使用英文名或工号" />
        </el-form-item>
      </div>
      <el-form-item label="初始密码（建议不少于 12 位）">
        <el-input
          v-model="accountForm.password"
          type="password"
          show-password
          placeholder="请输入初始密码"
        />
      </el-form-item>
      <div class="form-columns">
        <el-form-item label="岗位角色">
          <el-select v-model="accountForm.role">
            <el-option label="普通员工 (L1)" value="employee" />
            <el-option label="部门主管 (L2)" value="leader" />
            <el-option label="企业负责人 (L3)" value="boss" />
            <el-option label="系统管理员" value="admin" />
          </el-select>
        </el-form-item>
        <el-form-item
          v-if="accountForm.role !== 'boss' && accountForm.role !== 'admin'"
          label="所属部门"
        >
          <el-select v-model="accountForm.node_id">
            <el-option
              v-for="organization in organizations"
              :key="organization.id"
              :label="organization.name"
              :value="organization.id"
            />
          </el-select>
        </el-form-item>
      </div>
    </el-form>
    <template #footer>
      <el-button @click="accountOpen = false">取消</el-button>
      <el-button type="primary" :loading="busy" @click="saveAccount">确定新建</el-button>
    </template>
  </el-dialog>

  <el-dialog v-model="memberOpen" title="调整成员所属部门" width="480px">
    <div class="dialog-lead">
      成员所属部门及岗位角色变更后，其数据访问范围将随之调整（提交后需审批生效）。
    </div>
    <el-form label-position="top">
      <el-form-item label="岗位角色">
        <el-select v-model="memberForm.role">
          <el-option label="普通员工 (L1)" value="employee" />
          <el-option label="部门主管 (L2)" value="leader" />
          <el-option label="企业负责人 (L3)" value="boss" />
        </el-select>
      </el-form-item>
      <el-form-item v-if="memberForm.role !== 'boss'" label="所属部门">
        <el-select v-model="memberForm.node_id">
          <el-option
            v-for="organization in organizations"
            :key="organization.id"
            :label="organization.name"
            :value="organization.id"
          />
        </el-select>
      </el-form-item>
      <el-checkbox v-model="memberForm.remove">从所选部门中移除该成员</el-checkbox>
    </el-form>
    <template #footer>
      <el-button @click="memberOpen = false">取消</el-button>
      <el-button type="primary" :loading="busy" @click="saveMembership">提交申请</el-button>
    </template>
  </el-dialog>

  <el-dialog v-model="resetOpen" title="重置成员登录密码" width="460px">
    <div class="dialog-lead">
      正在为 <strong>{{ resetForm.display_name }}</strong
      >（{{ resetForm.username }}）申请重置密码。审批生效后该账号现有登录会话将立即失效。
    </div>
    <el-form label-position="top" @submit.prevent="submitResetPassword">
      <el-form-item label="新临时密码（建议不少于 12 位）">
        <el-input
          v-model="resetForm.new_password"
          type="password"
          show-password
          placeholder="请输入新的临时登录密码"
        />
      </el-form-item>
    </el-form>
    <template #footer>
      <el-button @click="resetOpen = false">取消</el-button>
      <el-button type="primary" :loading="busy" @click="submitResetPassword">提交申请</el-button>
    </template>
  </el-dialog>

  <el-dialog v-model="orgOpen" :title="orgForm.id ? '编辑部门' : '新建部门'" width="460px">
    <el-form label-position="top">
      <el-form-item label="部门名称">
        <el-input v-model="orgForm.name" placeholder="例如：华东电商运营部" />
      </el-form-item>
      <el-form-item v-if="!orgForm.id" label="组织类型">
        <el-select v-model="orgForm.kind">
          <el-option label="部门" value="department" />
          <el-option label="业务小组" value="team" />
        </el-select>
      </el-form-item>
      <el-form-item label="上级部门">
        <el-select v-model="orgForm.parent_id">
          <el-option
            v-for="organization in organizations.filter((item) => item.id !== orgForm.id)"
            :key="organization.id"
            :label="organization.name"
            :value="organization.id"
          />
        </el-select>
      </el-form-item>
      <el-checkbox v-if="orgForm.id" v-model="orgForm.active">启用该部门</el-checkbox>
    </el-form>
    <template #footer>
      <el-button @click="orgOpen = false">取消</el-button>
      <el-button type="primary" :loading="busy" @click="saveOrg">保存</el-button>
    </template>
  </el-dialog>
</template>
