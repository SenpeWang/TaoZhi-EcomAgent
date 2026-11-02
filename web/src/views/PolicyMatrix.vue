<script setup lang="ts">
import type { Approval, Capabilities, Organization, OrganizationTree, User } from '../types'

defineProps<{
  mode: 'approvals' | 'org'
  requests: Approval[]
  tree: OrganizationTree[]
  people: User[]
  caps: Partial<Capabilities>
  personName: (id: string) => string
  nodeName: (id: string) => string
}>()

const emit = defineEmits<{
  (e: 'decideRequest', row: Approval, approve: boolean): void
  (e: 'newOrg'): void
  (e: 'editOrg', data: Organization): void
  (e: 'newAccount'): void
  (e: 'membership', row: User): void
  (e: 'resetPassword', row: User): void
  (e: 'disableUser', row: User): void
}>()

const stateMap: Record<string, string> = {
  pending: '待审核',
  approved: '已批准',
  rejected: '已拒绝',
}

const kindMap: Record<string, string> = {
  company: '公司',
  department: '部门',
  team: '小组',
  leader: '负责人',
  employee: '员工',
}

/**
 * 格式化审批状态。
 * @param s - 审批状态码。
 * @returns 状态中文。
 */
function state(s: string) {
  return stateMap[s] || s
}

/**
 * 格式化组织或角色类型。
 * @param k - 类型代码。
 * @returns 类型中文。
 */
function kind(k: string) {
  return kindMap[k] || k
}

/**
 * 生成审批详情摘要说明。
 * @param a - 审批项。
 * @returns 摘要文本。
 */
function applicationSummary(a: Approval) {
  const p = a.payload || {}
  if (a.kind === 'publish') {
    return '申请发布资料第 ' + (p.expected_version || 1) + ' 版，请核对密级与权限'
  }
  if (a.kind === 'grant') {
    return (
      '申请单文档授权：' +
      (p.can_write ? '可写' : '只读') +
      '，到期时间：' +
      (p.expires_at || '永久')
    )
  }
  if (a.kind === 'membership') {
    return '申请调整任职：' + kind(p.role || '') + '（' + (p.remove ? '移除' : '新增') + '）'
  }
  return '申请审批事项'
}
</script>

<template>
  <!-- 审核与授权视图 -->
  <section v-if="mode === 'approvals'" class="panel">
    <el-alert
      title="批准不会改变他人权限；资料或人员变化后请重新提交申请。"
      type="info"
      :closable="false"
    />
    <el-table :data="requests">
      <el-table-column type="expand">
        <template #default="{ row }">
          <p class="application-summary">{{ applicationSummary(row) }}</p>
        </template>
      </el-table-column>
      <el-table-column label="申请类型" prop="label" width="140" />
      <el-table-column label="相关资料或人员" min-width="240">
        <template #default="{ row }">
          {{ row.title || personName(row.payload?.user_id || row.target_id || '') }}
          <small class="cell-small">{{
            row.title
              ? ''
              : row.kind === 'membership'
                ? nodeName(row.payload?.node_id || '') + ' · ' + kind(row.payload?.role || '')
                : '受限详情按当前权限展示'
          }}</small>
        </template>
      </el-table-column>
      <el-table-column label="申请人" width="130">
        <template #default="{ row }">{{ personName(row.requested_by || '') }}</template>
      </el-table-column>
      <el-table-column label="审批身份" width="110">
        <template #default="{ row }">
          {{ row.required_role === 'boss' ? '老板' : '团队负责人' }}
        </template>
      </el-table-column>
      <el-table-column label="状态" width="100">
        <template #default="{ row }">{{ state(row.state) }}</template>
      </el-table-column>
      <el-table-column label="操作" width="170">
        <template #default="{ row }">
          <template v-if="row.can_decide && row.state === 'pending'">
            <el-button text type="primary" @click="emit('decideRequest', row, true)"
              >批准</el-button
            >
            <el-button text type="danger" @click="emit('decideRequest', row, false)"
              >拒绝</el-button
            >
          </template>
          <span v-else class="muted">等待或已处理</span>
        </template>
      </el-table-column>
    </el-table>
    <el-empty v-if="!requests.length" description="暂无申请" />
  </section>

  <!-- 组织与人员视图 -->
  <div v-else-if="mode === 'org'" class="organization-grid">
    <section class="panel">
      <div class="section-heading">
        <h3>公司组织架构</h3>
        <el-button text type="primary" @click="emit('newOrg')">新增组织</el-button>
      </div>
      <el-tree :data="tree" node-key="id" default-expand-all :expand-on-click-node="false">
        <template #default="{ data }">
          <div class="org-tree-row">
            <span>{{ data.name }}</span>
            <el-tag size="small" type="info">{{ kind(data.kind) }}</el-tag>
            <el-button
              v-if="data.kind !== 'company'"
              text
              size="small"
              @click="emit('editOrg', data)"
            >
              调整
            </el-button>
          </div>
        </template>
      </el-tree>
    </section>

    <section class="panel">
      <div class="section-heading">
        <h3>人员与任职</h3>
        <el-button type="primary" @click="emit('newAccount')">新增账号</el-button>
      </div>
      <el-table :data="people">
        <el-table-column label="姓名 / 账号" min-width="160">
          <template #default="{ row }">
            {{ row.display_name }}
            <small class="cell-small">{{ row.username }}</small>
          </template>
        </el-table-column>
        <el-table-column label="任职与范围" min-width="210">
          <template #default="{ row }">
            <el-tag v-if="row.system_admin" size="small">管理员</el-tag>
            <div v-for="m in row.memberships" :key="m.node_id + m.role">
              {{ nodeName(m.node_id) }} · {{ kind(m.role) }}
            </div>
            <span v-if="!row.memberships?.length && !row.system_admin">待分配</span>
          </template>
        </el-table-column>
        <el-table-column label="状态" width="80">
          <template #default="{ row }">{{ row.active ? '正常' : '已停用' }}</template>
        </el-table-column>
        <el-table-column label="管理" width="220">
          <template #default="{ row }">
            <el-button text @click="emit('membership', row)">任职</el-button>
            <el-button text @click="emit('resetPassword', row)">重置</el-button>
            <el-button
              text
              :type="row.active ? 'danger' : 'primary'"
              @click="emit('disableUser', row)"
            >
              {{ row.active ? '停用' : '启用' }}
            </el-button>
          </template>
        </el-table-column>
      </el-table>
    </section>
  </div>
</template>
