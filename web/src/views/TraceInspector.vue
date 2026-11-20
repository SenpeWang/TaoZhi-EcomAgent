<template>
  <el-dialog
    :model-value="true"
    title="执行轨迹"
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
      <details class="task-info">
        <summary>任务信息</summary>
        <dl class="trace-metadata">
          <div>
            <dt>发起人</dt>
            <dd>
              {{ detail.trace.display_name || detail.trace.username || detail.trace.owner_id }}
            </dd>
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
      </details>

      <el-tabs v-model="tab">
        <el-tab-pane name="run" label="执行记录">
          <TraceRunInspector
            :detail="detail"
            :visible="tab === 'run'"
            :initial-trace-id="initialTraceId"
            :api="api"
            @events="tab = 'events'"
            @recording="recording = $event"
          />
        </el-tab-pane>
        <el-tab-pane name="events" label="任务阶段">
          <div class="current-stage">
            <strong>当前阶段</strong>
            <span>{{ formatStage(detail.trace.stage) }}</span>
            <el-progress :percentage="detail.trace.progress" :stroke-width="6" />
          </div>

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
              <p>{{ formatStage(event.stage) }}</p>
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
import { formatStage, stateTag, stateText, traceKind, traceTime } from '../labels'
import type { TraceDetail } from '../types'
import TraceRunInspector from './TraceRunInspector.vue'

const props = defineProps<{
  taskId: string
  initialTraceId: string | null
  api: <T>(path: string) => Promise<T>
}>()
const emit = defineEmits<{ (e: 'close'): void }>()
const detail = ref<TraceDetail | null>(null)
const busy = ref(false)
const error = ref('')
const tab = ref('run')
const recording = ref(false)
const finalRefreshes = ref(0)
const cursors = ref([0])
const active = computed(
  /**
   * 检查任务是否仍在排队或执行中。
   * @returns 当前详情是否需要自动刷新。
   */
  () =>
    detail.value?.trace.state === 'queued' ||
    detail.value?.trace.state === 'running' ||
    (tab.value === 'run' && recording.value) ||
    finalRefreshes.value > 0,
)
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
  try {
    const snapshot = await props.api<TraceDetail>(
      '/admin/traces/' + encodeURIComponent(props.taskId) + '?' + query,
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

/** 仅在当前用户正在查看活跃任务时刷新。 */
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
  max-height: 5em;
  overflow: auto;
}
.task-info summary {
  cursor: pointer;
  color: var(--tz-ink-secondary);
  font-size: 13px;
  width: fit-content;
  margin-bottom: 10px;
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
