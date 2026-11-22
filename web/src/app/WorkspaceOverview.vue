<script setup lang="ts">
import type { User } from '../features/administration/types'
import type { DocumentListItem } from '../features/documents/types'
import { levelText } from '../shared/labels'
import type { WorkspacePage } from './workspaceNavigation'
import type { Dashboard } from './workspaceTypes'

defineProps<{
  user: User
  dashboard: Dashboard
  documents: DocumentListItem[]
  primaryOrgName: string
  roles: string
  clearanceText: string
}>()
const emit = defineEmits<{
  navigate: [page: WorkspacePage]
  openDocument: [id: string]
}>()
</script>

<template>
  <section class="overview-banner">
    <div class="overview-banner-meta">
      <h2>{{ primaryOrgName }}</h2>
      <p>{{ user.display_name }} · {{ roles }} · 数据权限 {{ clearanceText }}</p>
    </div>
    <div class="overview-banner-actions">
      <el-button @click="emit('navigate', '轨迹中台')">查看执行轨迹</el-button>
      <el-button @click="emit('navigate', '知识问答')">发起知识问答</el-button>
      <el-button type="primary" @click="emit('navigate', '选品调研')">开始选品调研</el-button>
    </div>
  </section>

  <section class="stat-grid">
    <div class="stat-card">
      <div class="stat-card-top">
        <span>可访问文档</span>
      </div>
      <strong>{{ dashboard.documents }}<small>份</small></strong>
      <span class="stat-foot">当前权限范围内文档</span>
    </div>
    <div class="stat-card">
      <div class="stat-card-top">
        <span>分析任务</span>
      </div>
      <strong>{{ dashboard.tasks }}<small>项</small></strong>
      <span class="stat-foot">知识检索与选品调研</span>
    </div>
    <div class="stat-card">
      <div class="stat-card-top">
        <span>待审核</span>
      </div>
      <strong>{{ dashboard.review }}<small>项</small></strong>
      <span class="stat-foot">需主管或负责人复核</span>
    </div>
    <div class="stat-card">
      <div class="stat-card-top">
        <span>已完成</span>
      </div>
      <strong>{{ dashboard.completed }}<small>项</small></strong>
      <span class="stat-foot">支持溯源查看与导出</span>
    </div>
  </section>

  <section class="dashboard-grid">
    <div class="panel">
      <div class="section-heading">
        <div>
          <h3>最近可访问文档</h3>
        </div>
        <el-button text type="primary" @click="emit('navigate', '知识文档')"> 全部文档 </el-button>
      </div>
      <div v-if="documents.length" class="document-cards">
        <button
          v-for="document in documents.slice(0, 6)"
          :key="document.id"
          type="button"
          class="document-card"
          @click="emit('openDocument', document.id)"
        >
          <span class="level-pill" :class="`level-${document.level}`">{{
            levelText(document.level)
          }}</span>
          <strong>{{ document.title }}</strong>
          <small>{{
            document.scope === 'company' ? '全企业' : document.node_name || document.node_id
          }}</small>
        </button>
      </div>
      <el-empty v-else description="当前权限范围内暂无文档" />
    </div>

    <div class="panel">
      <div class="section-heading">
        <h3>账号与服务状态</h3>
        <el-button text type="primary" @click="emit('navigate', '个人中心')"> 账号设置 </el-button>
      </div>
      <div class="scope-card">
        <span>姓名 / 账号</span>
        <strong>{{ user.display_name }}（{{ user.username }}）</strong>
      </div>
      <div class="scope-card">
        <span>岗位角色</span>
        <strong>{{ roles }}</strong>
      </div>
      <div class="scope-card">
        <span>所属部门</span>
        <strong>{{ primaryOrgName }}</strong>
      </div>
      <div class="scope-card">
        <span>数据权限</span>
        <strong>{{ clearanceText }}</strong>
      </div>
      <div class="scope-card">
        <span>AI 分析服务</span>
        <span
          class="status-pill"
          :class="dashboard.model_ready ? 'status-active' : 'status-disabled'"
        >
          <span class="status-dot" />
          {{ dashboard.model_ready ? '已就绪' : '未连接' }}
        </span>
      </div>
      <div class="scope-card">
        <span>经营数据集</span>
        <span
          class="status-pill"
          :class="dashboard.business_ready ? 'status-active' : 'status-disabled'"
        >
          <span class="status-dot" />
          {{ dashboard.business_ready ? '已连接' : '未连接' }}
        </span>
      </div>
    </div>
  </section>
</template>
