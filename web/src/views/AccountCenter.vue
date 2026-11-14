<template>
  <section class="account-center-grid">
    <div class="account-profile-column">
      <div class="panel account-hero-card">
        <div class="account-hero-banner" />
        <div class="account-hero-body">
          <div class="account-avatar-xl" :class="clearanceTone">
            {{ avatarInitial }}
          </div>
          <div class="account-hero-meta">
            <div class="account-name-line">
              <h3>{{ user.display_name }}</h3>
              <span class="status-pill status-active"> <span class="status-dot" />状态正常 </span>
            </div>
            <p class="account-handle">账号：{{ user.username }} · {{ tenantName }}</p>
            <div class="account-badge-row">
              <span class="clearance-badge" :class="clearanceTone">{{ clearanceLabel }}</span>
              <span v-if="user.system_admin" class="role-chip role-admin">系统管理员</span>
            </div>
          </div>
        </div>

        <div class="account-divider" />

        <div class="account-meta-section">
          <h4>所属部门与岗位</h4>
          <div v-if="user.memberships.length" class="membership-list">
            <div
              v-for="m in user.memberships"
              :key="`${m.node_id}-${m.role}`"
              class="membership-item"
            >
              <div class="membership-org">
                <svg
                  class="mini-svg"
                  viewBox="0 0 24 24"
                  fill="none"
                  stroke="currentColor"
                  stroke-width="1.8"
                >
                  <path d="M3 21h18M5 21V7l8-4v18M19 21V11l-6-4" />
                </svg>
                <span>{{ m.name || m.node_id }}</span>
              </div>
              <el-tag size="small" :type="roleTagType(m.role)" effect="light">
                {{ formatRoleName(m.role) }}
              </el-tag>
            </div>
          </div>
          <div v-else class="empty-subtle-box">暂未分配所属部门</div>
        </div>

        <div class="account-divider" />

        <div class="account-meta-section">
          <h4>功能权限明细</h4>
          <div class="capability-matrix">
            <div
              v-for="item in capabilityItems"
              :key="item.key"
              class="capability-tile"
              :class="{ granted: item.enabled }"
            >
              <span class="cap-indicator">
                <svg
                  v-if="item.enabled"
                  viewBox="0 0 24 24"
                  fill="none"
                  stroke="currentColor"
                  stroke-width="2.4"
                >
                  <polyline points="20 6 9 17 4 12" />
                </svg>
                <svg v-else viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                  <line x1="5" y1="12" x2="19" y2="12" />
                </svg>
              </span>
              <div class="cap-copy">
                <strong>{{ item.label }}</strong>
                <small>{{ item.desc }}</small>
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>

    <div class="account-security-column">
      <div class="panel security-panel">
        <div class="section-head">
          <div>
            <h3>修改登录密码</h3>
            <p class="section-sub">密码更新后，当前已登录终端将自动退出并需使用新密码重新登录。</p>
          </div>
        </div>

        <el-alert
          v-if="user.must_change"
          type="warning"
          show-icon
          :closable="false"
          class="security-alert-banner"
          title="安全提醒：当前账号使用初始密码或重置后的临时密码登录，请先完成密码修改。"
        />

        <div class="password-policy-box">
          <div class="policy-box-title">密码设置说明</div>
          <ul>
            <li>生产环境建议密码长度不少于 12 位，包含大小写字母与数字组合；</li>
            <li>请勿与用户名或历史已使用密码相同；</li>
            <li>修改提交后服务端将立即注销旧会话。</li>
          </ul>
        </div>

        <el-form label-position="top" class="security-form" @submit.prevent="submitPasswordChange">
          <el-form-item label="当前密码">
            <el-input
              v-model="currentPassword"
              type="password"
              size="large"
              show-password
              autocomplete="current-password"
              placeholder="请输入当前登录密码"
            />
          </el-form-item>

          <div class="form-two-col">
            <el-form-item label="新密码">
              <el-input
                v-model="newPassword"
                type="password"
                size="large"
                show-password
                autocomplete="new-password"
                placeholder="请输入新密码（建议 ≥ 12 位）"
              />
            </el-form-item>
            <el-form-item label="确认新密码">
              <el-input
                v-model="confirmPassword"
                type="password"
                size="large"
                show-password
                autocomplete="new-password"
                placeholder="请再次输入新密码"
                @keyup.enter="submitPasswordChange"
              />
            </el-form-item>
          </div>

          <div class="security-actions">
            <el-button type="primary" size="large" :loading="busy" native-type="submit">
              保存新密码
            </el-button>
          </div>
        </el-form>
      </div>

      <div class="panel session-control-panel">
        <div class="session-control-row">
          <div>
            <h4>登录状态管理</h4>
            <p class="section-sub">如需切换账号或在公共设备上结束工作，请点击退出当前登录会话。</p>
          </div>
          <el-button type="danger" plain :loading="busy" @click="emit('signOut')">
            退出登录
          </el-button>
        </div>
      </div>
    </div>
  </section>
</template>

<script setup lang="ts">
import { computed, ref } from 'vue'
import { ElMessage } from 'element-plus'
import type { User } from '../types'

const props = defineProps<{
  user: User
  tenantName: string
  busy: boolean
  formatRoleName: (role: string) => string
}>()

const emit = defineEmits<{
  changePassword: [payload: { current_password: string; new_password: string }]
  signOut: []
}>()

const currentPassword = ref('')
const newPassword = ref('')
const confirmPassword = ref('')

const avatarInitial = computed((): string => {
  const raw = (props.user.display_name || props.user.username || 'U').trim()
  return raw ? raw.slice(0, 1).toUpperCase() : 'U'
})

const clearanceTone = computed((): string => {
  if (props.user.is_boss) return 'tone-exec'
  if (props.user.is_leader) return 'tone-lead'
  if (props.user.system_admin) return 'tone-admin'
  return 'tone-member'
})

const clearanceLabel = computed((): string => {
  if (props.user.is_boss) return '数据权限：机密 (L3) · 企业负责人'
  if (props.user.is_leader) return '数据权限：内部 (L2) · 部门主管'
  return '数据权限：公开 (L1) · 普通员工'
})

const capabilityItems = computed(() => [
  {
    key: 'write',
    label: '文档管理',
    desc: '上传、起草与修订企业知识文档',
    enabled: Boolean(props.user.capabilities?.write),
  },
  {
    key: 'review',
    label: '内容审核',
    desc: '复核团队成员提交的分析任务结果',
    enabled: Boolean(props.user.capabilities?.review),
  },
  {
    key: 'permissions',
    label: '权限审批',
    desc: '审批文档访问授权与密级变更申请',
    enabled: Boolean(props.user.capabilities?.permissions),
  },
  {
    key: 'system',
    label: '系统管理',
    desc: '维护企业部门结构、成员账号与运行状态',
    enabled: Boolean(props.user.capabilities?.system),
  },
])

/**
 * 映射角色标签语义色。
 * @param role - 组织角色编码。
 * @returns 标签样式类型。
 */
function roleTagType(role: string): 'primary' | 'success' | 'warning' | 'info' {
  if (role === 'boss') return 'primary'
  if (role === 'leader') return 'success'
  return 'info'
}

/**
 * 校验两次输入的新密码一致后提交改密请求。
 */
function submitPasswordChange(): void {
  if (!currentPassword.value || !newPassword.value) {
    ElMessage.warning('请完整填写当前密码与新密码')
    return
  }
  if (newPassword.value !== confirmPassword.value) {
    ElMessage.warning('两次输入的新密码不一致，请核对后重试')
    return
  }
  const payload = {
    current_password: currentPassword.value,
    new_password: newPassword.value,
  }
  confirmPassword.value = ''
  emit('changePassword', payload)
}
</script>
