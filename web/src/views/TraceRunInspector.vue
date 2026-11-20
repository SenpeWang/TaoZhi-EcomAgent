<template>
  <el-alert v-if="error" type="error" :title="error" show-icon :closable="false" />
  <el-skeleton v-if="loading && !selectedRun" :rows="3" animated />
  <template v-if="selectedRun">
    <div class="run-toolbar">
      <label for="inspected-trace-id">执行链路</label>
      <select
        id="inspected-trace-id"
        :value="selectedRun.trace_id"
        :disabled="loading"
        @change="chooseRun"
      >
        <option v-for="entry in availableRuns" :key="entry.trace_id" :value="entry.trace_id">
          v{{ entry.run_version }} · {{ entry.trace_id.slice(0, 8) }} ·
          {{ spanStatusLabels[entry.status] }} ·
          {{ traceTime(entry.started_at) }}
        </option>
      </select>
      <template v-if="nextRunCursor !== null || runPageCursors.length > 1">
        <el-button size="small" :disabled="loading || nextRunCursor === null" @click="olderRuns"
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
    <div class="run-views" role="group" aria-label="轨迹视图">
      <button type="button" :aria-pressed="view === 'activities'" @click="chooseView('activities')">
        行动记录
      </button>
      <button type="button" :aria-pressed="view === 'spans'" @click="chooseView('spans')">
        调用分析
      </button>
    </div>
    <dl v-if="view === 'spans'" class="execution-metrics" aria-label="本次执行指标">
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
    <p v-if="view === 'spans'" class="trace-id">
      Trace ID <code>{{ selectedRun.trace_id }}</code>
    </p>
    <div v-if="view === 'activities'" class="activity-toolbar">
      <label for="inspected-activity-kind">查看行动</label>
      <select id="inspected-activity-kind" :value="selectedKind" @change="changeActivityKind">
        <option value="">关键行动</option>
        <option
          v-for="(label, kind) in spanKindLabels"
          :key="kind"
          :value="kind"
          :disabled="!selectedRun.kind_counts[kind]"
        >
          {{ label }}（{{ selectedRun.kind_counts[kind] ?? 0 }}）
        </option>
      </select>
    </div>
    <fieldset v-else class="kind-filters" :disabled="loading">
      <legend>节点类型</legend>
      <div>
        <button type="button" :aria-pressed="selectedKind === ''" @click="chooseKind('')">
          全部 <span>{{ selectedRun.span_count }}</span>
        </button>
        <button
          v-for="(label, kind) in spanKindLabels"
          :key="kind"
          type="button"
          :aria-pressed="selectedKind === kind"
          :disabled="!selectedRun.kind_counts[kind]"
          @click="chooseKind(kind)"
        >
          {{ label }} <span>{{ selectedRun.kind_counts[kind] ?? 0 }}</span>
        </button>
      </div>
    </fieldset>
    <TraceActivityTimeline
      v-if="view === 'activities' && activities.length"
      :activities="activities"
      :observed-at="observedAt"
    />
    <SpanWaterfall
      v-else-if="view === 'spans' && spans.length"
      :key="selectedRun.trace_id + selectedKind"
      :run="selectedRun"
      :spans="spans"
      :kind="selectedKind"
      :observed-at="observedAt"
    />
    <el-skeleton v-else-if="loading" :rows="3" animated />
    <el-empty
      v-else-if="!error"
      :image-size="48"
      :description="
        view === 'activities' ? '本次执行尚无此类行动记录' : '本条执行链路没有此类型的节点'
      "
    />
    <div v-if="nextCursor" class="load-nodes">
      <el-button :disabled="loading" @click="loadMore">
        {{ view === 'activities' ? '加载更多行动' : '加载更多节点' }}（已显示
        {{ view === 'activities' ? activities.length : spans.length }}）
      </el-button>
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
import { onUnmounted, ref, shallowRef, toRef, watch } from 'vue'
import { traceTime } from '../labels'
import { formatDuration, runDuration, spanKindLabels, spanStatusLabels } from '../traceWaterfall'
import type {
  SpanKind,
  TraceActivity,
  TraceDetail,
  TraceRun,
  TraceRunPage,
  TraceSpan,
  TraceSpanPage,
} from '../types'
import SpanWaterfall from './SpanWaterfall.vue'
import TraceActivityTimeline from './TraceActivityTimeline.vue'

type RunView = 'activities' | 'spans'

const props = defineProps<{
  detail: TraceDetail
  visible: boolean
  initialTraceId: string | null
  api: <T>(path: string) => Promise<T>
}>()
const emit = defineEmits<{ (e: 'events'): void; (e: 'recording', active: boolean): void }>()
const availableRuns = shallowRef<TraceRun[]>([])
const selectedRun = shallowRef<TraceRun | null>(null)
const runPageCursors = ref<(string | null)[]>([null])
const nextRunCursor = ref<string | null>(null)
const selectedKind = ref<SpanKind | ''>('')
const view = ref<RunView>('activities')
const activities = shallowRef<TraceActivity[]>([])
const spans = shallowRef<TraceSpan[]>([])
const nextCursor = ref<string | null>(null)
const observedAt = ref(0)
const loading = ref(false)
const error = ref('')
let pinnedTraceId = props.initialTraceId
let loadedPageCount = 1
let requestSequence = 0

/**
 * 返回当前任务的执行版本接口。
 * @returns 同源接口路径。
 */
function basePath(): string {
  return '/admin/traces/' + encodeURIComponent(props.detail.trace.id) + '/runs'
}

/** 清空切换前的节点及分页位置。 */
function clearEntries(): void {
  activities.value = []
  spans.value = []
  observedAt.value = 0
  nextCursor.value = null
  loadedPageCount = 1
}

/**
 * 加载一个或多个已展开的节点页。重新取页时也更新已结束节点及并发到达的调用。
 * @param endpoint - 所选链路对应的行动或节点接口。
 * @param previous - 追加前已显示的行动或节点。
 * @param requestId - 本次读取的代数。
 * @param append - 是否仅追加下一页。
 * @returns 服务端顺序排列的记录，过期请求返回空值。
 */
async function readEntries<T extends TraceSpan>(
  endpoint: string,
  previous: T[],
  requestId: number,
  append: boolean,
): Promise<T[] | null> {
  const merged = new Map<string, T>()
  if (append) for (const item of previous) merged.set(item.span_id, item)
  let cursor = append ? nextCursor.value : null
  const count = append ? 1 : loadedPageCount
  let pages = 0
  let observedAtMs = observedAt.value
  do {
    const query = new URLSearchParams({ limit: '100' })
    if (cursor) query.set('cursor', cursor)
    if (selectedKind.value) query.set('kind', selectedKind.value)
    const page = await props.api<TraceSpanPage<T>>(endpoint + '?' + query)
    if (requestSequence !== requestId) return null
    for (const span of page.items) merged.set(span.span_id, span)
    observedAtMs = Date.parse(page.observed_at)
    cursor = page.next_cursor
    pages += 1
  } while (cursor && pages < count)
  observedAt.value = observedAtMs
  loadedPageCount = append ? loadedPageCount + pages : pages
  nextCursor.value = cursor
  return [...merged.values()]
}

/**
 * 同步执行列表，并保留按 Trace ID 搜索或手动选中的链路，即使它位于别页。
 * @param preferredId - 要保留的链路编号，翻页时为空。
 * @param requestId - 本次读取的代数。
 * @returns 当前应显示的执行记录。
 */
async function readRuns(preferredId: string | null, requestId: number): Promise<TraceRun | null> {
  const query = new URLSearchParams({ limit: '30' })
  const cursor = runPageCursors.value.at(-1)
  if (cursor) query.set('cursor', cursor)
  const page = await props.api<TraceRunPage>(basePath() + '?' + query)
  if (requestSequence !== requestId) return null
  let target = page.items[0] ?? null
  if (preferredId) {
    const match = page.items.find((entry) => entry.trace_id === preferredId)
    if (match) target = match
    else {
      const exact = await props.api<TraceRunPage>(
        basePath() + '?trace_id=' + encodeURIComponent(preferredId),
      )
      if (requestSequence !== requestId) return null
      target = exact.items[0] ?? null
      if (!target || target.trace_id !== preferredId) throw new Error('未找到所选执行链路')
      page.items.unshift(target)
    }
  }
  availableRuns.value = page.items
  nextRunCursor.value = page.next_cursor
  return target
}

/**
 * 获取当前链路与所选类型的节点，筛选切换时清除旧的分页位置。
 * @param action - 刷新、切换链路、翻页、筛选或追加节点。
 * @param traceId - 下拉列表中的执行链路编号。
 * @returns 所有当前页请求完成的异步结果。
 */
async function load(
  action: 'refresh' | 'page' | 'select' | 'filter' | 'append',
  traceId?: string,
): Promise<void> {
  const requestId = ++requestSequence
  loading.value = true
  error.value = ''
  try {
    let target = selectedRun.value
    if (action === 'refresh' || action === 'page') {
      const preferredId = action === 'refresh' ? pinnedTraceId : null
      target = await readRuns(preferredId, requestId)
    } else if (action === 'select') {
      target = availableRuns.value.find((entry) => entry.trace_id === traceId) ?? null
    }
    if (requestSequence !== requestId) return
    const changed = target?.trace_id !== selectedRun.value?.trace_id
    if (action === 'page' || action === 'select') pinnedTraceId = target?.trace_id ?? null
    if (changed) selectedKind.value = ''
    if (changed || action === 'filter') clearEntries()
    selectedRun.value = target
    emit('recording', target?.status === 'running')
    if (!target) return
    const endpoint = basePath() + '/' + target.trace_id + '/' + view.value
    if (view.value === 'activities') {
      const items = await readEntries(endpoint, activities.value, requestId, action === 'append')
      if (items && requestId === requestSequence) activities.value = items
    } else {
      const items = await readEntries(endpoint, spans.value, requestId, action === 'append')
      if (items && requestId === requestSequence) spans.value = items
    }
  } catch (cause) {
    if (requestSequence === requestId)
      error.value = cause instanceof Error ? cause.message : '读取调用链失败'
  } finally {
    if (requestSequence === requestId) loading.value = false
  }
}

/**
 * 按真实节点类型筛选选中的执行链路。
 * @param kind - 节点类型，空字符串表示全部。
 */
function chooseKind(kind: SpanKind | ''): void {
  if (selectedKind.value === kind) return
  selectedKind.value = kind
  void load('filter')
}

/**
 * 切换行动时间线与节点分析，分别使用各自接口的默认范围和游标。
 * @param mode - 想要查看的轨迹视图。
 */
function chooseView(mode: RunView): void {
  if (view.value === mode) return
  view.value = mode
  selectedKind.value = ''
  void load('filter')
}

/**
 * 使用行动筛选框选中的节点类型。
 * @param event - 筛选框的选择事件。
 */
function changeActivityKind(event: Event): void {
  chooseKind((event.target as HTMLSelectElement).value as SpanKind | '')
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
  void load('select', (event.target as HTMLSelectElement).value)
}

/** 加载更早的一页执行版本。 */
function olderRuns(): void {
  if (nextRunCursor.value === null || loading.value) return
  runPageCursors.value.push(nextRunCursor.value)
  void load('page')
}

/** 回到较新的一页执行版本。 */
function newerRuns(): void {
  if (runPageCursors.value.length === 1 || loading.value) return
  runPageCursors.value.pop()
  void load('page')
}

/** 将下一页记录追加到当前轨迹视图。 */
function loadMore(): void {
  if (nextCursor.value && !loading.value) void load('append')
}

/** 退出时丢弃尚未返回的读取。 */
function stop(): void {
  requestSequence++
  emit('recording', false)
}

watch([toRef(props, 'detail'), toRef(props, 'visible')], refresh, { immediate: true })
onUnmounted(stop)
</script>

<style scoped>
.run-toolbar,
.activity-toolbar {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px;
  margin: 4px 0 10px;
}
.run-toolbar label,
.activity-toolbar label {
  font-size: 13px;
  font-weight: 600;
}
.run-toolbar select,
.activity-toolbar select {
  max-width: 100%;
  border: 1px solid var(--tz-border);
  border-radius: 5px;
  padding: 6px;
  background: var(--tz-surface);
  color: var(--tz-ink);
  font: inherit;
  font-size: 13px;
}
.run-views {
  display: flex;
  gap: 16px;
  margin: 10px 0 14px;
  border-bottom: 1px solid var(--tz-border);
}
.run-views button {
  padding: 8px 2px;
  border: none;
  border-bottom: 2px solid transparent;
  background: transparent;
  color: var(--tz-ink-muted);
  font: inherit;
  font-weight: 600;
  cursor: pointer;
}
.run-views button[aria-pressed='true'] {
  border-bottom-color: var(--tz-primary);
  color: var(--tz-primary);
}
.kind-filters {
  margin: 14px 0;
  padding: 0;
  border: 0;
  min-width: 0;
}
.kind-filters legend {
  margin-bottom: 7px;
  padding: 0;
  color: var(--tz-ink-secondary);
  font-size: 13px;
  font-weight: 600;
}
.kind-filters > div {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}
.kind-filters button {
  padding: 5px 9px;
  border: 1px solid var(--tz-border);
  border-radius: 5px;
  color: var(--tz-ink-secondary);
  background: var(--tz-surface);
  font: inherit;
  font-size: 12px;
  cursor: pointer;
}
.kind-filters button[aria-pressed='true'] {
  border-color: var(--tz-primary);
  color: var(--tz-primary);
  background: var(--tz-primary-soft);
}
.kind-filters button:disabled {
  cursor: default;
}
.kind-filters button:disabled:not([aria-pressed='true']) {
  color: var(--tz-ink-muted);
}
.kind-filters button span {
  margin-inline-start: 3px;
  font-variant-numeric: tabular-nums;
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
