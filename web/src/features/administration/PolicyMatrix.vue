<template>
  <section v-if="mode === 'approvals'" class="iam-stack">
    <div class="iam-kpi-strip compact">
      <div class="iam-kpi-card">
        <span class="iam-kpi-label">全部审批</span>
        <strong class="iam-kpi-value">{{ approvals.length }}</strong>
      </div>
      <div class="iam-kpi-card">
        <span class="iam-kpi-label">待审批</span>
        <strong class="iam-kpi-value accent">{{ pendingApprovalCount }}</strong>
      </div>
      <div class="iam-kpi-card">
        <span class="iam-kpi-label">已处理</span>
        <strong class="iam-kpi-value">{{ approvals.length - pendingApprovalCount }}</strong>
      </div>
    </div>

    <div class="panel">
      <div class="section-head">
        <div>
          <h3>权限审批</h3>
          <p class="section-sub">
            文档密级调整、单文档阅读授权、组织架构变更及成员账号安全变更需经审批后生效。
          </p>
        </div>
        <div class="filter-pills">
          <button
            v-for="tab in approvalTabs"
            :key="tab.value"
            type="button"
            class="filter-pill-btn"
            :class="{ active: approvalFilter === tab.value }"
            @click="approvalFilter = tab.value"
          >
            {{ tab.label }}
          </button>
        </div>
      </div>

      <el-table :data="filteredApprovals" empty-text="暂无审批记录">
        <el-table-column label="申请类型" min-width="220">
          <template #default="{ row }">
            <div class="approval-title-cell">
              <strong>{{ row.label || row.kind }}</strong>
              <small v-if="row.title" class="muted-line">关联对象：{{ row.title }}</small>
            </div>
          </template>
        </el-table-column>
        <el-table-column label="申请详情" min-width="240">
          <template #default="{ row }">
            <span class="summary-text">{{ summarize(row) }}</span>
          </template>
        </el-table-column>
        <el-table-column label="审批角色" width="130">
          <template #default="{ row }">
            <el-tag
              size="small"
              :type="row.required_role === 'boss' ? 'primary' : 'success'"
              effect="light"
            >
              {{ row.required_role === 'boss' ? '企业负责人' : '部门主管' }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column label="状态" width="110">
          <template #default="{ row }">
            <span class="status-pill" :class="approvalStateClass(row.state)">
              <span class="status-dot" />
              {{ approvalStateText(row.state) }}
            </span>
          </template>
        </el-table-column>
        <el-table-column label="操作" width="165" align="right">
          <template #default="{ row }">
            <div v-if="row.state === 'pending' && row.can_decide" class="row-actions-end">
              <el-button size="small" type="primary" @click="emit('decide', row, true)">
                通过
              </el-button>
              <el-button size="small" type="danger" plain @click="emit('decide', row, false)">
                驳回
              </el-button>
            </div>
            <span v-else class="muted-note">已处理</span>
          </template>
        </el-table-column>
      </el-table>
    </div>
  </section>

  <section v-else class="iam-stack">
    <div class="iam-kpi-strip">
      <div class="iam-kpi-card">
        <span class="iam-kpi-label">企业成员数</span>
        <strong class="iam-kpi-value">{{ users.length }}</strong>
      </div>
      <div class="iam-kpi-card">
        <span class="iam-kpi-label">正常账号</span>
        <strong class="iam-kpi-value success">{{ activeUserCount }}</strong>
      </div>
      <div class="iam-kpi-card">
        <span class="iam-kpi-label">已停用</span>
        <strong class="iam-kpi-value muted">{{ users.length - activeUserCount }}</strong>
      </div>
      <div class="iam-kpi-card">
        <span class="iam-kpi-label">部门数量</span>
        <strong class="iam-kpi-value accent">{{ orgNodeCount }}</strong>
      </div>
    </div>

    <div class="iam-workspace-grid">
      <div class="panel iam-org-panel">
        <div class="section-head">
          <div>
            <h3>组织架构</h3>
            <p class="section-sub">管理企业部门与业务小组</p>
          </div>
          <el-button type="primary" size="small" @click="emit('newOrg')">新建部门</el-button>
        </div>

        <el-tree :data="tree" node-key="id" default-expand-all class="enterprise-org-tree">
          <template #default="{ data }">
            <div class="org-tree-row">
              <div class="org-tree-main">
                <span class="org-kind-badge">{{ orgKindLabel(data.kind) }}</span>
                <span class="org-node-name">{{ data.name }}</span>
                <span v-if="!data.active" class="status-pill status-disabled mini">已停用</span>
              </div>
              <el-button
                v-if="data.kind !== 'company'"
                size="small"
                text
                type="primary"
                @click.stop="emit('editOrg', data)"
              >
                编辑
              </el-button>
            </div>
          </template>
        </el-tree>
      </div>

      <div class="panel iam-users-panel">
        <div class="section-head iam-users-head">
          <div>
            <h3>成员列表</h3>
            <p class="section-sub">管理企业成员账号、所属部门及密码重置</p>
          </div>
          <el-button type="primary" @click="emit('newAccount')">新建成员</el-button>
        </div>

        <div class="directory-toolbar">
          <el-input
            v-model="searchQuery"
            placeholder="搜索姓名、账号或部门..."
            clearable
            class="directory-search"
          />
          <div class="filter-pills">
            <button
              v-for="item in userFilterTabs"
              :key="item.value"
              type="button"
              class="filter-pill-btn"
              :class="{ active: userStatusFilter === item.value }"
              @click="userStatusFilter = item.value"
            >
              {{ item.label }}
            </button>
          </div>
        </div>

        <el-table :data="filteredUsers" empty-text="暂无符合条件的成员">
          <el-table-column label="成员" min-width="180">
            <template #default="{ row }">
              <div class="member-cell">
                <span class="member-avatar" :class="memberTone(row)">
                  {{ memberInitial(row) }}
                </span>
                <div class="member-identity">
                  <strong>{{ row.display_name }}</strong>
                  <small>{{ row.username }}</small>
                </div>
              </div>
            </template>
          </el-table-column>

          <el-table-column label="角色" min-width="140">
            <template #default="{ row }">
              <div class="role-badge-stack">
                <el-tag
                  v-for="r in displayRoles(row)"
                  :key="r.text"
                  size="small"
                  :type="r.type"
                  effect="light"
                >
                  {{ r.text }}
                </el-tag>
              </div>
            </template>
          </el-table-column>

          <el-table-column label="所属部门" min-width="170">
            <template #default="{ row }">
              <div v-if="row.memberships.length" class="membership-tags">
                <span v-for="m in row.memberships" :key="`${m.node_id}-${m.role}`" class="org-chip">
                  {{ m.name || m.node_id }} · {{ roleName(m.role) }}
                </span>
              </div>
              <span v-else class="muted-note">暂未分配部门</span>
            </template>
          </el-table-column>

          <el-table-column label="状态" width="98">
            <template #default="{ row }">
              <span class="status-pill" :class="row.active ? 'status-active' : 'status-disabled'">
                <span class="status-dot" />
                {{ row.active ? '正常' : '已停用' }}
              </span>
            </template>
          </el-table-column>

          <el-table-column label="操作" width="215" align="right">
            <template #default="{ row }">
              <div class="row-actions-end">
                <el-button size="small" plain @click="emit('membership', row)">
                  调整部门
                </el-button>
                <el-button size="small" plain @click="emit('resetPassword', row)">
                  重置密码
                </el-button>
                <el-button
                  size="small"
                  :type="row.active ? 'danger' : 'success'"
                  text
                  @click="emit('disableUser', row)"
                >
                  {{ row.active ? '停用' : '启用' }}
                </el-button>
              </div>
            </template>
          </el-table-column>
        </el-table>
      </div>
    </div>
  </section>
</template>

<script setup lang="ts">
import { computed, ref } from 'vue'
import { roleName } from '../../shared/labels'
import type { ManagedUser, Approval, Organization, OrganizationTree } from './types'

const props = defineProps<{
  mode: 'approvals' | 'org'
  approvals: Approval[]
  tree: OrganizationTree[]
  users: ManagedUser[]
}>()

const emit = defineEmits<{
  decide: [row: Approval, approve: boolean]
  newOrg: []
  editOrg: [row: Organization]
  newAccount: []
  membership: [row: ManagedUser]
  resetPassword: [row: ManagedUser]
  disableUser: [row: ManagedUser]
}>()

const searchQuery = ref('')
const userStatusFilter = ref<'all' | 'active' | 'disabled'>('all')
const approvalFilter = ref<'all' | 'pending' | 'done'>('all')

const userFilterTabs: { value: 'all' | 'active' | 'disabled'; label: string }[] = [
  { value: 'all', label: '全部' },
  { value: 'active', label: '正常' },
  { value: 'disabled', label: '已停用' },
]

const approvalTabs: { value: 'all' | 'pending' | 'done'; label: string }[] = [
  { value: 'all', label: '全部' },
  { value: 'pending', label: '待审批' },
  { value: 'done', label: '已处理' },
]

/**
 * 统计待处理审批事项数量。
 * @returns 待审批数量。
 */
function calcPendingApprovals(): number {
  let count = 0
  for (const row of props.approvals) {
    if (row.state === 'pending') count += 1
  }
  return count
}
const pendingApprovalCount = computed(calcPendingApprovals)

/**
 * 按状态过滤审批队列。
 * @returns 过滤后的审批数组。
 */
function filterApprovals(): Approval[] {
  if (approvalFilter.value === 'all') return props.approvals
  const out: Approval[] = []
  for (const row of props.approvals) {
    if (approvalFilter.value === 'pending' && row.state === 'pending') out.push(row)
    else if (approvalFilter.value === 'done' && row.state !== 'pending') out.push(row)
  }
  return out
}
const filteredApprovals = computed(filterApprovals)

/**
 * 统计在岗启用的企业账号数量。
 * @returns 启用账号数。
 */
function calcActiveUsers(): number {
  let count = 0
  for (const u of props.users) {
    if (u.active) count += 1
  }
  return count
}
const activeUserCount = computed(calcActiveUsers)

/**
 * 递归统计组织树中的节点总数。
 * @param nodes - 组织树节点列表。
 * @returns 节点数量。
 */
function countNodes(nodes: OrganizationTree[]): number {
  let total = 0
  for (const n of nodes) {
    total += 1
    if (n.children?.length) total += countNodes(n.children)
  }
  return total
}
const orgNodeCount = computed(
  /**
   * 计算当前组织架构总节点数。
   * @returns 组织节点总计。
   */
  () => countNodes(props.tree),
)

/**
 * 根据关键词与状态过滤企业账号目录。
 * @returns 符合筛选条件的账号列表。
 */
function filterUsersList(): ManagedUser[] {
  const q = searchQuery.value.trim().toLowerCase()
  const out: ManagedUser[] = []
  for (const u of props.users) {
    if (userStatusFilter.value === 'active' && !u.active) continue
    if (userStatusFilter.value === 'disabled' && u.active) continue
    if (q) {
      const orgText = (u.memberships ?? [])
        .map(
          /**
           * 拼接组织节点名称用于本地检索。
           * @param m - 任职记录。
           * @returns 节点文本。
           */
          (m) => `${m.name || ''} ${m.node_id}`,
        )
        .join(' ')
      const haystack = `${u.display_name} ${u.username} ${orgText}`.toLowerCase()
      if (!haystack.includes(q)) continue
    }
    out.push(u)
  }
  return out
}
const filteredUsers = computed(filterUsersList)

/**
 * 提取成员头像单字缩写。
 * @param row - 账号对象。
 * @returns 大写头像首字。
 */
function memberInitial(row: ManagedUser): string {
  const clean = (row.display_name || row.username || 'U').trim()
  return clean ? clean.slice(0, 1).toUpperCase() : 'U'
}

/**
 * 计算成员头像的语义配色样式。
 * @param row - 账号对象。
 * @returns 配色类名。
 */
function memberTone(row: ManagedUser): string {
  if (row.is_boss) return 'tone-exec'
  if (row.is_leader) return 'tone-lead'
  if (row.system_admin) return 'tone-admin'
  return 'tone-member'
}

/**
 * 构建成员列表展示的规范化身份标签组。
 * @param row - 账号对象。
 * @returns 展示标签与语义类型数组。
 */
function displayRoles(
  row: ManagedUser,
): { text: string; type: 'primary' | 'success' | 'warning' | 'info' }[] {
  const tags: { text: string; type: 'primary' | 'success' | 'warning' | 'info' }[] = []
  if (row.is_boss) tags.push({ text: '企业负责人', type: 'primary' })
  else if (row.is_leader) tags.push({ text: '部门主管', type: 'success' })
  else tags.push({ text: '普通员工', type: 'info' })
  if (row.system_admin) tags.push({ text: '系统管理员', type: 'warning' })
  return tags
}

/**
 * 将组织节点类型映射为规范中文标签。
 * @param kind - 组织节点类型。
 * @returns 中文节点类别。
 */
function orgKindLabel(kind: string): string {
  if (kind === 'company') return '企业'
  if (kind === 'department') return '部门'
  return '小组'
}

/**
 * 映射审批流程状态的视觉类名。
 * @param state - 审批状态编码。
 * @returns 样式类名。
 */
function approvalStateClass(state: string): string {
  if (state === 'pending') return 'status-pending'
  if (state === 'approved') return 'status-active'
  return 'status-disabled'
}

/**
 * 映射审批流程状态的中文文本。
 * @param state - 审批状态编码。
 * @returns 中文状态文本。
 */
function approvalStateText(state: string): string {
  if (state === 'pending') return '待审批'
  if (state === 'approved') return '已通过'
  if (state === 'rejected') return '已驳回'
  return state
}

/**
 * 将审批载荷转为合规可读的一行业务摘要。
 * @param row - 审批记录。
 * @returns 可读摘要文案。
 */
function summarize(row: Approval): string {
  const p = row.payload || {}
  if (row.kind === 'membership')
    return `${p.remove ? '移出部门' : '分配岗位'}：${roleName(p.role || '')}`
  if (row.kind === 'document_policy')
    return `可见范围=${p.scope === 'company' ? '全企业' : '指定部门'} · 密级=L${p.level} · AI分析=${p.ai_allowed ? '允许' : '禁止'}`
  if (row.kind === 'document_grant')
    return `${p.revoke ? '收回阅读授权' : '授予阅读权限'}${p.can_download ? '（含原件下载）' : ''}`
  if (row.kind === 'user_security')
    return [
      p.reset_password ? '重置登录密码' : '',
      p.active !== undefined ? (p.active ? '启用账号' : '停用账号') : '',
      p.system_admin !== undefined ? (p.system_admin ? '设为系统管理员' : '取消系统管理员') : '',
    ]
      .filter(Boolean)
      .join(' · ')
  if (row.kind === 'org_change') return `部门变更：${p.name || ''}`
  return JSON.stringify(p)
}
</script>
