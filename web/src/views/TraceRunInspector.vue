<template>
  <el-alert v-if="error" type="error" :title="error" show-icon :closable="false" />
  <el-skeleton v-if="loading && !selectedRun" :rows="3" animated />
  <template v-if="selectedRun">
    <div class="run-toolbar">
      <label for="inspected-run-version">执行版本</label>
      <select
        id="inspected-run-version"
        :value="selectedRun.run_version"
        :disabled="loading"
        @change="chooseRun"
      >
        <option v-for="entry in availableRuns" :key="entry.run_version" :value="entry.run_version">
          v{{ entry.run_version }} · {{ spanStatusLabels[entry.status] }} ·
          {{ traceTime(entry.started_at) }}
        </option>
      </select>
      <template v-if="nextRunPageVersion !== null || runPageCursors.length > 1">
        <el-button
          size="small"
          :disabled="loading || nextRunPageVersion === null"
          @click="olderRuns"
          >更早的执行</el-button
        >
        <el-button
          size="small"
          :disabled="loading || runPageCursors.length === 1"
          @click="newerRuns"
          >较新的执行</el-button
        >
      </template>
    </div>
    <dl class="execution-metrics" aria-label="本次执行指标">
      <div>
        <dt>{{ selectedRun.status === 'running' ? '已运行时间' : '执行耗时' }}</dt>
        <dd>{{ formatDuration(runDuration(selectedRun, observedAt)) }}</dd>
      </div>
      <div>
        <dt>调用节点</dt>
        <dd>{{ selectedRun.span_count.toLocaleString() }}</dd>
      </div>
      <div>
        <dt>失败节点</dt>
        <dd :class="{ 'metric-error': selectedRun.failed_count > 0 }">
          {{ selectedRun.failed_count.toLocaleString() }}
        </dd>
      </div>
      <div class="usage-metric">
        <dt>Token 用量</dt>
        <dd>
          <span><small>输入</small> {{ selectedRun.input_tokens.toLocaleString() }}</span>
          <span><small>输出</small> {{ selectedRun.output_tokens.toLocaleString() }}</span>
        </dd>
      </div>
    </dl>
    <p class="trace-id">
      Trace ID <code>{{ selectedRun.trace_id }}</code>
    </p>
    <SpanWaterfall
      v-if="spans.length"
      :key="selectedRun.trace_id"
      :run="selectedRun"
      :spans="spans"
      :observed-at="observedAt"
    />
    <el-skeleton v-else-if="loading" :rows="3" animated />
    <div v-if="nextSpanCursor" class="load-nodes">
      <el-button :disabled="loading" @click="loadMoreSpans"
        >加载更多节点（已显示 {{ spans.length }}）</el-button
      >
    </div>
  </template>
  <el-empty
    v-else-if="!loading && !error"
    :image-size="48"
    :description="detail.trace.state === 'queued' ? '等待开始执行' : '此次提问暂无调用节点记录'"
  >
    <el-button size="small" @click="emit('events')">查看阶段时间线</el-button>
  </el-empty>
</template>

<script setup lang="ts">
import { onUnmounted, ref, shallowRef, watch } from 'vue'
import { traceTime } from '../labels'
import { formatDuration, runDuration, spanStatusLabels } from '../traceWaterfall'
import type { TraceDetail, TraceRun, TraceRunPage, TraceSpan, TraceSpanPage } from '../types'
import SpanWaterfall from './SpanWaterfall.vue'

const props = defineProps<{
  detail: TraceDetail
  visible: boolean
  initialRunVersion?: number | null
  api: <T>(path: string) => Promise<T>
}>()
const emit = defineEmits<{ (e: 'events'): void; (e: 'recording', active: boolean): void }>()
const availableRuns = shallowRef<TraceRun[]>([])
const selectedRun = shallowRef<TraceRun | null>(null)
const runPageCursors = ref<(number | null)[]>(
  props.initialRunVersion == null ? [null] : [null, props.initialRunVersion + 1],
)
const nextRunPageVersion = ref<number | null>(null)
const spans = shallowRef<TraceSpan[]>([])
const nextSpanCursor = ref<string | null>(null)
const observedAt = ref(0)
const loading = ref(false)
const error = ref('')
let loadedSpanPageCount = 1
let requestSequence = 0

/**
 * 返回当前任务的执行版本接口。
 * @returns 同源接口路径。
 */
function basePath(): string {
  return '/admin/traces/' + encodeURIComponent(props.detail.trace.id) + '/runs'
}

/** 清空切换前的节点及分页位置。 */
function clearSpans(): void {
  spans.value = []
  observedAt.value = 0
  nextSpanCursor.value = null
  loadedSpanPageCount = 1
}

/**
 * 加载一个或多个已展开的节点页。重新取页时也更新已结束节点及并发到达的调用。
 * @param version - 执行版本。
 * @param requestId - 本次读取的代数。
 * @param append - 是否仅追加下一页。
 * @returns 所需页读取完成的异步结果。
 */
async function readSpans(version: number, requestId: number, append = false): Promise<void> {
  const merged = new Map<string, TraceSpan>()
  if (append) for (const span of spans.value) merged.set(span.span_id, span)
  let cursor = append ? nextSpanCursor.value : null
  const count = append ? 1 : loadedSpanPageCount
  let pages = 0
  let observedAtMs = observedAt.value
  do {
    const query = new URLSearchParams({ limit: '100' })
    if (cursor) query.set('cursor', cursor)
    const page = await props.api<TraceSpanPage>(basePath() + '/' + version + '/spans?' + query)
    if (requestSequence !== requestId) return
    for (const span of page.items) merged.set(span.span_id, span)
    observedAtMs = Date.parse(page.observed_at)
    cursor = page.next_cursor
    pages += 1
  } while (cursor && pages < count)
  spans.value = [...merged.values()]
  observedAt.value = observedAtMs
  loadedSpanPageCount = append ? loadedSpanPageCount + pages : pages
  nextSpanCursor.value = cursor
}

/**
 * 列出当前页执行，读取选定版本的节点，或在现有节点后继续翻页。
 * @param action - 刷新执行列表、切换所选执行或追加节点。
 * @param version - 切换时指定的执行版本。
 * @returns 此次读取完成的异步结果。
 */
async function load(action: 'refresh' | 'select' | 'append', version?: number): Promise<void> {
  const requestId = ++requestSequence
  loading.value = true
  error.value = ''
  try {
    const followNewestRun = action === 'refresh' && selectedRun.value === availableRuns.value[0]
    if (action === 'refresh') {
      const query = new URLSearchParams({ limit: '30' })
      const before = runPageCursors.value.at(-1)
      if (before != null) query.set('before_version', String(before))
      const page = await props.api<TraceRunPage>(basePath() + '?' + query)
      if (requestSequence !== requestId) return
      availableRuns.value = page.items
      nextRunPageVersion.value = page.next_before_version
    }
    const target = followNewestRun
      ? availableRuns.value[0]?.run_version
      : (version ?? selectedRun.value?.run_version)
    let selectedVersion = availableRuns.value[0] ?? null
    for (const entry of availableRuns.value) {
      if (entry.run_version === target) selectedVersion = entry
    }
    const changed = selectedVersion?.trace_id !== selectedRun.value?.trace_id
    if (changed) clearSpans()
    selectedRun.value = selectedVersion
    emit('recording', selectedVersion?.status === 'running')
    if (selectedVersion)
      await readSpans(selectedVersion.run_version, requestId, action === 'append')
  } catch (cause) {
    if (requestSequence === requestId) {
      availableRuns.value = []
      selectedRun.value = null
      clearSpans()
      error.value = cause instanceof Error ? cause.message : '读取调用链失败'
    }
  } finally {
    if (requestSequence === requestId) loading.value = false
  }
}

/** 用任务详情的最新快照同步运行，避免与翻页请求重叠。 */
function refresh(): void {
  if (props.visible && !loading.value) void load('refresh')
}

/**
 * 切换到指定的执行版本。
 * @param event - 版本下拉列表的选择事件。
 */
function chooseRun(event: Event): void {
  const version = Number((event.target as HTMLSelectElement).value)
  void load('select', version)
}

/** 加载更早的一页执行版本。 */
function olderRuns(): void {
  if (nextRunPageVersion.value === null || loading.value) return
  runPageCursors.value.push(nextRunPageVersion.value)
  void load('refresh')
}

/** 回到较新的一页执行版本。 */
function newerRuns(): void {
  if (runPageCursors.value.length === 1 || loading.value) return
  runPageCursors.value.pop()
  void load('refresh')
}

/** 将下一页调用合并进当前瀑布图。 */
function loadMoreSpans(): void {
  if (nextSpanCursor.value && !loading.value) void load('append')
}

/** 退出时丢弃尚未返回的读取。 */
function stop(): void {
  requestSequence++
  emit('recording', false)
}

watch(
  [
    /** 跟踪手动或自动刷新后的任务快照。 */ () => props.detail,
    /** 切换回调用分析时刷新已选版本。 */ () => props.visible,
  ],
  refresh,
  { immediate: true },
)
onUnmounted(stop)
</script>

<style scoped>
.run-toolbar {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px;
  margin: 4px 0 10px;
}
.run-toolbar label {
  font-size: 13px;
  font-weight: 600;
}
.run-toolbar select {
  max-width: 100%;
  border: 1px solid var(--tz-border);
  border-radius: 5px;
  padding: 6px;
  background: var(--tz-surface);
  color: var(--tz-ink);
  font: inherit;
  font-size: 13px;
}
.execution-metrics {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr)) minmax(0, 1.5fr);
  margin: 16px 0 8px;
  border: 1px solid var(--tz-border);
  border-radius: 9px;
  background: var(--tz-surface-muted);
}
.execution-metrics > div {
  padding: 14px 16px;
  min-width: 0;
}
.execution-metrics > div + div {
  border-inline-start: 1px solid var(--tz-border);
}
.execution-metrics dt,
.trace-id {
  color: var(--tz-ink-muted);
  font-size: 12px;
}
.execution-metrics dd {
  margin: 6px 0 0;
  color: var(--tz-ink);
  font-size: clamp(16px, 2vw, 23px);
  font-weight: 650;
  line-height: 1.3;
  font-variant-numeric: tabular-nums;
}
.execution-metrics .metric-error {
  color: var(--tz-danger);
}
.usage-metric dd {
  display: flex;
  flex-wrap: wrap;
  align-items: baseline;
  gap: 2px 12px;
  font-size: 16px;
}
.usage-metric small {
  color: var(--tz-ink-muted);
  font-size: 11px;
  font-weight: 400;
}
.trace-id {
  margin: 8px 0;
  overflow-wrap: anywhere;
}
.load-nodes {
  margin-top: 12px;
}
@media (max-width: 640px) {
  .execution-metrics {
    grid-template-columns: 1fr 1fr;
  }
  .execution-metrics > div:nth-child(3) {
    border-inline-start: 0;
  }
  .execution-metrics > div:nth-child(n + 3) {
    border-top: 1px solid var(--tz-border);
  }
  .execution-metrics > div {
    padding: 10px;
  }
}
</style>
