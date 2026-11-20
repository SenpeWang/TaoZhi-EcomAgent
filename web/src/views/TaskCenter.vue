<template>
  <section class="panel task-center">
    <div class="section-head">
      <div>
        <h3>{{ isReviewPage ? '团队待审核任务' : '我的分析任务' }}</h3>
        <p class="section-sub">
          {{
            isReviewPage
              ? '复核团队成员提交的高密级或待人工确认分析结论。'
              : '按知识问答与选品调研分类追踪任务进度、阶段摘要与最终结论。'
          }}
        </p>
      </div>
      <div class="section-actions">
        <el-button @click="emit('reload')">刷新</el-button>
        <el-button @click="emit('navigate', '选品调研')">新建选品调研</el-button>
        <el-button type="primary" @click="emit('navigate', '知识问答')">新建问答任务</el-button>
      </div>
    </div>

    <div class="task-kpi-grid">
      <button
        type="button"
        class="task-kpi-card"
        :class="{ active: stateFilter === 'all' }"
        @click="selectStateFilter('all')"
      >
        <span class="task-kpi-label">全部任务</span>
        <strong class="task-kpi-value">{{ kindFiltered.length }}</strong>
      </button>
      <button
        type="button"
        class="task-kpi-card is-running"
        :class="{ active: stateFilter === 'active' }"
        @click="selectStateFilter('active')"
      >
        <span class="task-kpi-label">进行中 / 排队</span>
        <strong class="task-kpi-value">{{ activeCount }}</strong>
      </button>
      <button
        type="button"
        class="task-kpi-card is-done"
        :class="{ active: stateFilter === 'completed' }"
        @click="selectStateFilter('completed')"
      >
        <span class="task-kpi-label">已完成</span>
        <strong class="task-kpi-value">{{ completedCount }}</strong>
      </button>
      <button
        type="button"
        class="task-kpi-card is-alert"
        :class="{ active: stateFilter === 'attention' }"
        @click="selectStateFilter('attention')"
      >
        <span class="task-kpi-label">待审核 / 异常</span>
        <strong class="task-kpi-value">{{ attentionCount }}</strong>
      </button>
    </div>

    <div class="task-toolbar">
      <el-radio-group v-if="!isReviewPage" v-model="kindFilter" size="default">
        <el-radio-button value="all">全部 ({{ baseTasks.length }})</el-radio-button>
        <el-radio-button value="ask">{{ traceKind('ask') }} ({{ askCount }})</el-radio-button>
        <el-radio-button value="research"
          >{{ traceKind('research') }} ({{ researchCount }})</el-radio-button
        >
      </el-radio-group>

      <div class="task-toolbar-filters">
        <el-input
          v-model="keyword"
          clearable
          placeholder="搜索任务标题、阶段或任务 ID..."
          class="task-search-input"
        />
        <el-select v-model="stateFilter" class="task-state-select" placeholder="状态筛选">
          <el-option label="全部状态" value="all" />
          <el-option label="进行中 / 排队" value="active" />
          <el-option label="已完成" value="completed" />
          <el-option label="待审核" value="review" />
          <el-option label="失败 / 异常" value="failed" />
          <el-option label="已取消" value="cancelled" />
        </el-select>
      </div>
    </div>

    <el-table :data="pagedTasks" class="task-table" empty-text="当前筛选条件下暂无任务记录">
      <el-table-column label="类型" width="132">
        <template #default="{ row }">
          <el-tag
            size="small"
            :type="row.kind === 'research' ? 'warning' : 'primary'"
            effect="light"
          >
            {{ traceKind(row.kind) }}
          </el-tag>
        </template>
      </el-table-column>

      <el-table-column label="任务主题" min-width="300">
        <template #default="{ row }">
          <div class="task-title-cell">
            <button type="button" class="task-title-btn" @click="emit('open', row.id)">
              {{ row.question }}
            </button>
            <div class="task-meta-row">
              <span>发起时间：{{ formatTime(row.created_at) }}</span>
              <span class="task-meta-sep">·</span>
              <span :title="row.id">编号：{{ shortId(row.id) }}</span>
              <template v-if="row.input_level >= 2">
                <span class="task-meta-sep">·</span>
                <span>L{{ row.input_level }}</span>
              </template>
            </div>
          </div>
        </template>
      </el-table-column>

      <el-table-column label="当前阶段" min-width="210">
        <template #default="{ row }">
          <el-tooltip
            :content="row.error || row.stage || '等待处理'"
            placement="top"
            :disabled="(row.error || row.stage || '').length < 24"
          >
            <span class="task-stage-text" :class="{ 'is-error': Boolean(row.error) }">
              {{ cleanStage(row.error || row.stage) }}
            </span>
          </el-tooltip>
        </template>
      </el-table-column>

      <el-table-column label="状态与进度" width="175">
        <template #default="{ row }">
          <div class="task-status-cell">
            <el-tag size="small" :type="stateTag(row.state)" effect="light">
              {{ stateText(row.state) }}
            </el-tag>
            <el-progress
              :percentage="row.progress"
              :stroke-width="6"
              :show-text="true"
              class="task-progress-bar"
            />
          </div>
        </template>
      </el-table-column>

      <el-table-column label="操作" width="90" align="right">
        <template #default="{ row }">
          <el-button size="small" plain @click="emit('open', row.id)">详情</el-button>
        </template>
      </el-table-column>
    </el-table>

    <div v-if="filteredTasks.length > pageSize" class="task-pagination">
      <span class="task-page-summary">
        共 {{ filteredTasks.length }} 条，当前第 {{ currentPage }} / {{ totalPages }} 页
      </span>
      <el-pagination
        v-model:current-page="currentPage"
        v-model:page-size="pageSize"
        :page-sizes="[12, 20, 50]"
        layout="sizes, prev, pager, next"
        :total="filteredTasks.length"
        background
        small
      />
    </div>
  </section>
</template>

<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import type { Task } from '../types'
import { traceKind } from '../labels'

const props = defineProps<{
  tasks: Task[]
  isReviewPage: boolean
  stateText: (state: string) => string
  stateTag: (state: string) => 'primary' | 'success' | 'warning' | 'danger' | 'info'
}>()

const emit = defineEmits<{
  (e: 'open', taskId: string): void
  (e: 'navigate', page: string): void
  (e: 'reload'): void
}>()

const kindFilter = ref<'all' | 'ask' | 'research'>('all')
const stateFilter = ref<string>('all')
const keyword = ref('')
const currentPage = ref(1)
const pageSize = ref(12)

const baseTasks = computed(() => props.tasks)
const askCount = computed(() => baseTasks.value.filter((t) => t.kind !== 'research').length)
const researchCount = computed(() => baseTasks.value.filter((t) => t.kind === 'research').length)

const kindFiltered = computed(() => {
  if (props.isReviewPage || kindFilter.value === 'all') return baseTasks.value
  if (kindFilter.value === 'research') {
    return baseTasks.value.filter((t) => t.kind === 'research')
  }
  return baseTasks.value.filter((t) => t.kind !== 'research')
})

const activeCount = computed(
  () => kindFiltered.value.filter((t) => t.state === 'running' || t.state === 'queued').length,
)
const completedCount = computed(
  () => kindFiltered.value.filter((t) => t.state === 'completed').length,
)
const attentionCount = computed(
  () =>
    kindFiltered.value.filter((t) =>
      ['review', 'failed', 'rejected', 'cancelled'].includes(t.state),
    ).length,
)

const filteredTasks = computed(() => {
  const kw = keyword.value.trim().toLowerCase()
  return kindFiltered.value.filter((t) => {
    if (stateFilter.value === 'active' && t.state !== 'running' && t.state !== 'queued') {
      return false
    }
    if (
      stateFilter.value === 'attention' &&
      !['review', 'failed', 'rejected', 'cancelled'].includes(t.state)
    ) {
      return false
    }
    if (
      !['all', 'active', 'attention'].includes(stateFilter.value) &&
      t.state !== stateFilter.value
    ) {
      return false
    }
    if (!kw) return true
    return (
      t.question.toLowerCase().includes(kw) ||
      t.id.toLowerCase().includes(kw) ||
      (t.stage || '').toLowerCase().includes(kw)
    )
  })
})

const totalPages = computed(() =>
  Math.max(1, Math.ceil(filteredTasks.value.length / pageSize.value)),
)

const pagedTasks = computed(() => {
  const start = (currentPage.value - 1) * pageSize.value
  return filteredTasks.value.slice(start, start + pageSize.value)
})

watch([kindFilter, stateFilter, keyword, pageSize, () => props.isReviewPage], () => {
  currentPage.value = 1
})

/**
 * 点击顶部概览卡切换状态筛选条件。
 * @param target 目标状态筛选值。
 */
function selectStateFilter(target: string): void {
  stateFilter.value = stateFilter.value === target && target !== 'all' ? 'all' : target
}

/**
 * 清洗后端阶段文本中的机器标签前缀，提升列表可读性。
 * @param raw 原始阶段或错误文本。
 * @returns 面向用户的精简阶段说明。
 */
function cleanStage(raw?: string | null): string {
  const txt = (raw || '').trim()
  if (!txt) return '等待处理'
  return txt.replace(/^(\[[a-zA-Z0-9_]+\]\s*)+/, '').trim() || txt
}

/**
 * 将 ISO 时间字符串格式化为紧凑的本地月日时分。
 * @param iso 时间字符串。
 * @returns 可读本地时间。
 */
function formatTime(iso?: string | null): string {
  if (!iso) return '—'
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return iso.slice(0, 16).replace('T', ' ')
  const m = String(d.getMonth() + 1).padStart(2, '0')
  const day = String(d.getDate()).padStart(2, '0')
  const hh = String(d.getHours()).padStart(2, '0')
  const mm = String(d.getMinutes()).padStart(2, '0')
  return `${m}-${day} ${hh}:${mm}`
}

/**
 * 缩短任务 ID 展示，减少表格视觉噪音。
 * @param id 完整任务编号。
 * @returns 精简编号。
 */
function shortId(id: string): string {
  return id.length > 14 ? `${id.slice(0, 9)}…${id.slice(-4)}` : id
}
</script>

<style scoped>
.task-kpi-grid {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 12px;
  margin-bottom: 16px;
}

.task-kpi-card {
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  gap: 4px;
  padding: 12px 14px;
  border: 1px solid var(--border-color, #e5e7eb);
  border-radius: 10px;
  background: var(--surface-subtle, #f9fafb);
  cursor: pointer;
  transition: all 0.16s ease;
  text-align: left;
}

.task-kpi-card:hover {
  border-color: var(--el-color-primary-light-5, #a0cfff);
  background: #fff;
}

.task-kpi-card.active {
  border-color: var(--el-color-primary, #2563eb);
  background: #eff6ff;
}

.task-kpi-label {
  font-size: 12px;
  color: #6b7280;
}

.task-kpi-value {
  font-size: 20px;
  line-height: 1.2;
  font-weight: 700;
  color: #111827;
}

.task-kpi-card.is-running .task-kpi-value {
  color: #2563eb;
}

.task-kpi-card.is-done .task-kpi-value {
  color: #16a34a;
}

.task-kpi-card.is-alert .task-kpi-value {
  color: #d97706;
}

.task-toolbar {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  margin-bottom: 14px;
}

.task-toolbar-filters {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-left: auto;
}

.task-search-input {
  width: 240px;
}

.task-state-select {
  width: 140px;
}

.task-title-cell {
  display: flex;
  flex-direction: column;
  gap: 4px;
  padding: 2px 0;
}

.task-title-btn {
  padding: 0;
  border: 0;
  background: transparent;
  color: #1f2937;
  font-size: 14px;
  font-weight: 500;
  line-height: 1.45;
  text-align: left;
  cursor: pointer;
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
}

.task-title-btn:hover {
  color: var(--el-color-primary, #2563eb);
  text-decoration: underline;
}

.task-meta-row {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 12px;
  color: #6b7280;
}

.task-meta-sep {
  color: #d1d5db;
}

.task-stage-text {
  display: inline-block;
  max-width: 100%;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-size: 13px;
  color: #374151;
}

.task-stage-text.is-error {
  color: #dc2626;
}

.task-status-cell {
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.task-progress-bar {
  width: 140px;
}

.task-pagination {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-top: 16px;
  padding-top: 12px;
  border-top: 1px solid #f3f4f6;
}

.task-page-summary {
  font-size: 13px;
  color: #6b7280;
}

@media (max-width: 900px) {
  .task-kpi-grid {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
  .task-toolbar-filters {
    width: 100%;
    margin-left: 0;
  }
  .task-search-input {
    flex: 1;
  }
}
</style>
