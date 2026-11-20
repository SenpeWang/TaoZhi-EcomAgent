<template>
  <el-dialog
    :model-value="true"
    title="提问轨迹分析"
    width="min(1060px, 95vw)"
    align-center
    @close="emit('close')"
  >
    <el-alert v-if="error" type="error" :title="error" show-icon :closable="false" />
    <el-skeleton v-else-if="!detail" :rows="6" animated />
    <div v-if="detail" class="trace-detail">
      <div class="trace-heading">
        <span>{{ traceKind(detail.trace.kind) }}</span>
        <el-tag size="small" :type="stateTag(detail.trace.state)">
          {{ stateText(detail.trace.state) }}
        </el-tag>
      </div>
      <h3 class="full-question">{{ detail.trace.question || '未记录提问正文' }}</h3>
      <dl class="trace-metadata">
        <div>
          <dt>发起人</dt>
          <dd>{{ detail.trace.display_name || detail.trace.username || detail.trace.owner_id }}</dd>
        </div>
        <div>
          <dt>组织范围</dt>
          <dd>{{ detail.trace.node_name || '—' }}</dd>
        </div>
        <div>
          <dt>执行次数</dt>
          <dd>{{ detail.trace.attempts }}</dd>
        </div>
        <div>
          <dt>发起时间</dt>
          <dd>{{ traceTime(detail.trace.created_at) }}</dd>
        </div>
        <div>
          <dt>开始时间</dt>
          <dd>{{ traceTime(detail.trace.started_at) }}</dd>
        </div>
        <div>
          <dt>结束时间</dt>
          <dd>{{ traceTime(detail.trace.finished_at) }}</dd>
        </div>
        <div class="trace-identifier">
          <dt>任务 ID</dt>
          <dd>
            <code>{{ detail.trace.id }}</code>
          </dd>
        </div>
      </dl>

      <div class="current-stage">
        <strong>当前阶段</strong>
        <span>{{ detail.trace.stage }}</span>
        <el-progress :percentage="detail.trace.progress" :stroke-width="6" />
      </div>

      <el-tabs v-model="tab">
        <el-tab-pane name="spans" label="调用分析">
          <TraceRunInspector
            :detail="detail"
            :visible="tab === 'spans'"
            :initial-run-version="initialRunVersion"
            :api="api"
            @events="tab = 'events'"
            @recording="recording = $event"
          />
        </el-tab-pane>
        <el-tab-pane name="events" label="全部阶段事件">
          <ol v-if="detail.events.length" class="trace-timeline">
            <li v-for="event in detail.events" :key="event.id">
              <div class="event-meta">
                <time :datetime="event.created_at">{{ traceTime(event.created_at) }}</time>
                <span>执行版本 {{ event.run_version }}</span>
                <el-tag size="small" effect="plain" :type="stateTag(event.status)">
                  {{ stateText(event.status) }}
                </el-tag>
                <span>{{ event.progress }}%</span>
              </div>
              <p>{{ event.stage }}</p>
            </li>
          </ol>
          <el-empty v-else description="暂无阶段事件" :image-size="48" />
          <div v-if="detail.has_more_events || cursors.length > 1" class="toolbar">
            <span>第 {{ cursors.length }} 页事件</span>
            <el-button :disabled="busy || cursors.length === 1" @click="previousEvents">
              上一页
            </el-button>
            <el-button :disabled="busy || !detail.has_more_events" @click="nextEvents">
              下一页
            </el-button>
          </div>
        </el-tab-pane>
        <el-tab-pane name="calls" label="全部模型调用">
          <el-table :data="detail.model_calls" row-key="id" empty-text="暂无模型调用记录">
            <el-table-column label="调用时间" min-width="155">
              <template #default="{ row }">{{ traceTime(row.created_at) }}</template>
            </el-table-column>
            <el-table-column label="调用角色" min-width="110">
              <template #default="{ row }">{{ row.role_label || row.role }}</template>
            </el-table-column>
            <el-table-column label="状态" width="95">
              <template #default="{ row }">{{ outcomes[row.outcome] || row.outcome }}</template>
            </el-table-column>
            <el-table-column label="耗时" width="105">
              <template #default="{ row }">
                {{ row.outcome === 'started' ? '—' : formatDuration(row.elapsed_ms) }}
              </template>
            </el-table-column>
            <el-table-column prop="prompt_tokens" label="输入 Token" width="105" />
            <el-table-column prop="completion_tokens" label="输出 Token" width="105" />
          </el-table>
          <div v-if="detail.has_more_calls || callCursors.length > 1" class="toolbar call-pages">
            <el-button :disabled="busy || !detail.has_more_calls" @click="olderCalls">
              更早的调用
            </el-button>
            <el-button :disabled="busy || callCursors.length === 1" @click="newerCalls">
              较新的调用
            </el-button>
          </div>
        </el-tab-pane>
      </el-tabs>
    </div>
    <template #footer>
      <span v-if="active" class="live-hint">每 5 秒自动更新</span>
      <el-button :loading="busy" @click="loadDetail">刷新轨迹</el-button>
      <el-button @click="emit('close')">关闭</el-button>
    </template>
  </el-dialog>
</template>

<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { stateTag, stateText, traceKind, traceTime } from '../labels'
import { formatDuration } from '../traceWaterfall'
import type { TraceDetail } from '../types'
import TraceRunInspector from './TraceRunInspector.vue'

const props = defineProps<{
  traceId: string
  initialRunVersion?: number | null
  api: <T>(path: string) => Promise<T>
}>()
const emit = defineEmits<{ (e: 'close'): void }>()
const detail = ref<TraceDetail | null>(null)
const busy = ref(false)
const error = ref('')
const tab = ref('spans')
const recording = ref(false)
const finalRefreshes = ref(0)
const cursors = ref([0])
const callCursors = ref([0])
const active = computed(
  /**
   * 检查任务是否仍在排队或执行中。
   * @returns 当前详情是否需要自动刷新。
   */
  () =>
    detail.value?.trace.state === 'queued' ||
    detail.value?.trace.state === 'running' ||
    (tab.value === 'spans' && recording.value) ||
    finalRefreshes.value > 0,
)
const outcomes: Record<string, string> = { started: '已发起', ok: '成功', failed: '失败' }
let timer: number | undefined

/** 读取任务最新记录及当前页的阶段事件。 */
async function loadDetail(): Promise<void> {
  if (busy.value) return
  busy.value = true
  error.value = ''
  const query = new URLSearchParams({
    after_event_id: String(cursors.value.at(-1)),
    event_limit: '50',
  })
  const beforeCall = callCursors.value.at(-1)
  if (beforeCall) query.set('before_call_id', String(beforeCall))
  try {
    const snapshot = await props.api<TraceDetail>(
      '/admin/traces/' + encodeURIComponent(props.traceId) + '?' + query,
    )
    if (
      snapshot.trace.finished_at &&
      detail.value?.trace.finished_at !== snapshot.trace.finished_at &&
      (detail.value?.trace.state === 'running' ||
        detail.value?.trace.state === 'queued' ||
        Math.abs(Date.now() - Date.parse(snapshot.trace.finished_at)) < 15000)
    ) {
      // 完成状态可能先于尾部 Span 批次提交。
      finalRefreshes.value = 3
    } else if (finalRefreshes.value) finalRefreshes.value--
    detail.value = snapshot
  } catch (cause) {
    detail.value = null
    error.value = cause instanceof Error ? cause.message : '读取执行轨迹失败'
  } finally {
    busy.value = false
  }
}

/** 打开下页阶段事件。 */
function nextEvents(): void {
  const lastId = detail.value?.events.at(-1)?.id
  if (!lastId || !detail.value?.has_more_events || busy.value) return
  cursors.value.push(lastId)
  void loadDetail()
}

/** 返回前页阶段事件。 */
function previousEvents(): void {
  if (cursors.value.length === 1 || busy.value) return
  cursors.value.pop()
  void loadDetail()
}

/** 从最早一条可见调用向前翻页。 */
function olderCalls(): void {
  const firstId = detail.value?.model_calls[0]?.id
  if (!firstId || !detail.value?.has_more_calls || busy.value) return
  callCursors.value.push(firstId)
  void loadDetail()
}

/** 返回较近的一页模型调用。 */
function newerCalls(): void {
  if (callCursors.value.length === 1 || busy.value) return
  callCursors.value.pop()
  void loadDetail()
}

/** 仅在管理员正在查看活跃任务时刷新。 */
function refreshActive(): void {
  if (active.value && !document.hidden) void loadDetail()
}

/** 加载首屏并启动可见任务轮询。 */
function start(): void {
  void loadDetail()
  timer = window.setInterval(refreshActive, 5000)
}

/** 关闭详情时释放轮询定时器。 */
function stop(): void {
  window.clearInterval(timer)
}

onMounted(start)
onUnmounted(stop)
</script>

<style scoped>
.trace-detail {
  max-height: 68vh;
  overflow-y: auto;
  padding: 0 5px;
}
.trace-heading,
.event-meta {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 10px;
  color: var(--tz-ink-muted);
  font-size: 12px;
}
.full-question,
.trace-timeline p {
  white-space: pre-wrap;
  overflow-wrap: anywhere;
}
.full-question {
  margin: 10px 0 16px;
  color: var(--tz-ink);
  font-size: clamp(18px, 2.4vw, 25px);
  line-height: 1.55;
}
.trace-metadata {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(150px, 1fr));
  gap: 12px 20px;
  margin: 0 0 16px;
  padding: 16px;
  border-radius: 8px;
  border: 1px solid var(--tz-border);
  background: var(--tz-surface-muted);
}
.trace-metadata dt {
  color: var(--tz-ink-muted);
  font-size: 12px;
}
.trace-metadata dd {
  margin: 4px 0 0;
  overflow-wrap: anywhere;
}
.trace-identifier {
  grid-column: 1 / -1;
}
.current-stage {
  display: grid;
  grid-template-columns: auto 1fr;
  align-items: baseline;
  gap: 6px 12px;
  margin-bottom: 14px;
  overflow-wrap: anywhere;
}
.current-stage .el-progress {
  grid-column: 1 / -1;
}
.trace-timeline {
  padding-left: 12px;
  list-style: none;
}
.trace-timeline li {
  position: relative;
  border-left: 2px solid var(--tz-border);
  padding: 0 0 18px 18px;
}
.trace-timeline li:last-child {
  border-color: transparent;
}
.trace-timeline li::before {
  position: absolute;
  top: 5px;
  left: -5px;
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: var(--tz-primary);
  content: '';
}
.trace-timeline p {
  margin: 6px 0 0;
}
.call-pages {
  margin-top: 12px;
}
.live-hint {
  margin-right: 12px;
  color: var(--tz-ink-muted);
  font-size: 12px;
}
@media (max-width: 640px) {
  .trace-detail {
    padding: 0;
    max-height: 70dvh;
  }
  .trace-metadata {
    grid-template-columns: repeat(2, minmax(0, 1fr));
    padding: 12px;
    gap: 10px;
  }
  .trace-detail :deep(.el-tabs__item) {
    padding-inline: 10px;
  }
  .live-hint {
    display: block;
    margin: 0 0 6px;
  }
}
</style>
