<template>
  <main class="login-portal">
    <section class="login-showcase">
      <div class="showcase-grid-bg" aria-hidden="true" />
      <div class="showcase-inner">
        <div class="showcase-brand">
          <span class="brand-monogram" aria-hidden="true">淘</span>
          <span class="showcase-brand-name">淘智企业工作台</span>
        </div>

        <div class="showcase-main-copy">
          <h1 class="showcase-title">企业经营分析与选品决策</h1>
          <p class="showcase-desc">在同一个工作台查阅经营指标、检索企业知识、追踪选品调研。</p>

          <ul class="showcase-feature-list">
            <li>
              <span class="feature-check" aria-hidden="true">✓</span>
              <span>按部门和密级访问企业资料</span>
            </li>
            <li>
              <span class="feature-check" aria-hidden="true">✓</span>
              <span>从知识问答返回引用的文档版本</span>
            </li>
            <li>
              <span class="feature-check" aria-hidden="true">✓</span>
              <span>回看调研执行过程与审核记录</span>
            </li>
          </ul>
        </div>

        <div class="showcase-footer">
          <div class="env-meta-pill">
            <span class="env-dot" />
            <span>{{ info.name || '默认企业空间' }}</span>
          </div>
          <span class="env-mode-text">租户标识：{{ info.tenant_code || 'default' }}</span>
        </div>
      </div>
    </section>

    <section class="login-auth-pane">
      <div class="auth-card">
        <div class="auth-card-header">
          <div class="auth-workspace-pill">{{ info.name || '企业工作台' }}</div>
          <h2>账号登录</h2>
          <p>请使用企业分配的账号和密码登录系统</p>
        </div>

        <el-form label-position="top" class="auth-form" @submit.prevent="emit('signIn')">
          <el-form-item label="账号">
            <el-input
              :model-value="username"
              size="large"
              placeholder="请输入用户名"
              autocomplete="username"
              clearable
              @update:model-value="onUsernameChange"
            >
              <template #prefix>
                <svg
                  class="field-svg-icon"
                  viewBox="0 0 24 24"
                  fill="none"
                  stroke="currentColor"
                  stroke-width="1.8"
                >
                  <path d="M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2" />
                  <circle cx="12" cy="7" r="4" />
                </svg>
              </template>
            </el-input>
          </el-form-item>

          <el-form-item label="密码">
            <el-input
              :model-value="password"
              type="password"
              size="large"
              show-password
              placeholder="请输入密码"
              autocomplete="current-password"
              @update:model-value="onPasswordChange"
            >
              <template #prefix>
                <svg
                  class="field-svg-icon"
                  viewBox="0 0 24 24"
                  fill="none"
                  stroke="currentColor"
                  stroke-width="1.8"
                >
                  <rect x="3" y="11" width="18" height="11" rx="2" ry="2" />
                  <path d="M7 11V7a5 5 0 0 1 10 0v4" />
                </svg>
              </template>
            </el-input>
          </el-form-item>

          <el-button
            type="primary"
            size="large"
            class="auth-submit-btn"
            :loading="busy"
            native-type="submit"
          >
            {{ busy ? '登录中...' : '登 录' }}
          </el-button>
        </el-form>

        <div v-if="info.quick_accounts?.length" class="quick-fill-bar">
          <div class="quick-fill-label">快捷填充测试账号</div>
          <el-select
            v-model="selectedQuickUsername"
            placeholder="选择角色账号一键填充..."
            clearable
            filterable
            style="width: 100%"
            @change="onSelectQuickAccount"
          >
            <el-option
              v-for="acc in info.quick_accounts"
              :key="acc.username"
              :label="`${acc.display_name}（@${acc.username} · ${formatRoleBadge(acc.role)}）`"
              :value="acc.username"
            >
              <div class="quick-option-row">
                <span class="quick-option-name">
                  {{ acc.display_name }}
                  <small>@{{ acc.username }}</small>
                </span>
                <span class="sandbox-role-pill" :class="roleTone(acc.role)">
                  {{ formatRoleBadge(acc.role) }}
                </span>
              </div>
            </el-option>
          </el-select>
        </div>

        <div class="auth-security-foot">仅限授权企业成员访问 · 登录行为将记录至操作日志</div>
      </div>
    </section>
  </main>
</template>

<script setup lang="ts">
import { ref, watch } from 'vue'
import type { QuickAccount, WorkspaceInfo } from '../types'

const props = defineProps<{
  info: WorkspaceInfo
  username: string
  password: string
  busy: boolean
}>()

const emit = defineEmits<{
  'update:username': [value: string]
  'update:password': [value: string]
  signIn: []
  pickAccount: [account: QuickAccount]
}>()

const selectedQuickUsername = ref('')

watch(
  /**
   * 监听用户名变化以同步快捷账号下拉选中态。
   * @returns 当前输入用户名。
   */
  () => props.username,
  /**
   * 当用户名被清空或手动改写为非列表项时清除下拉选中状态。
   * @param val - 当前用户名。
   */
  (val) => {
    if (!val) selectedQuickUsername.value = ''
  },
)

/**
 * 同步更新登录账号输入框内容。
 * @param val - 最新账号字符串。
 */
function onUsernameChange(val: string): void {
  emit('update:username', val)
}

/**
 * 同步更新登录密码输入框内容。
 * @param val - 最新密码字符串。
 */
function onPasswordChange(val: string): void {
  emit('update:password', val)
}

/**
 * 根据下拉选中的用户名查找并填充对应测试账号信息。
 * @param uname - 选中的用户名。
 */
function onSelectQuickAccount(uname: string): void {
  if (!uname || !props.info.quick_accounts) return
  for (const acc of props.info.quick_accounts) {
    if (acc.username === uname) {
      emit('pickAccount', acc)
      break
    }
  }
}

/**
 * 将角色编码映射为规范企业角色名称。
 * @param role - 服务端返回的角色名称。
 * @returns 企业角色文案。
 */
function formatRoleBadge(role: string): string {
  if (role.includes('老板') || role.includes('boss')) return '企业负责人'
  if (role.includes('组长') || role.includes('leader')) return '部门主管'
  if (role.includes('管理员') || role.includes('admin')) return '系统管理员'
  return '普通员工'
}

/**
 * 根据角色名称返回统一的语义配色类名。
 * @param role - 服务端返回的角色名称。
 * @returns 配色样式类。
 */
function roleTone(role: string): string {
  if (role.includes('老板') || role.includes('boss')) return 'tone-exec'
  if (role.includes('组长') || role.includes('leader')) return 'tone-lead'
  if (role.includes('管理员') || role.includes('admin')) return 'tone-admin'
  return 'tone-member'
}
</script>
