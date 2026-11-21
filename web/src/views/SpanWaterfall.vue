<template>
  <div class="analysis-controls">
    <div class="view-switch" role="group" aria-label="节点分析视图">
      <button
        v-for="option in views"
        :key="option.value"
        type="button"
        :aria-pressed="view === option.value"
        @click="view = option.value"
      >
        {{ option.value === 'tree' && kind ? '调用时间线' : option.label }}
      </button>
    </div>
    <span class="coverage"
      >已加载 {{ spans.length }} /
      {{ kind ? (run.kind_counts[kind] ?? 0) : run.span_count }} 个节点</span
    >
  </div>
  <p v-if="view === 'latency'" class="analysis-note">
    按已结束子调用的总耗时降序排列，包含各自的下级调用。
  </p>
  <p v-if="view === 'issues'" class="analysis-note">显示本次执行中失败或中断的节点。</p>
  <div v-if="visibleRows.length" class="chart-scroll">
    <div class="chart" aria-label="调用树和耗时瀑布图">
      <div class="chart-grid chart-scale">
        <span>调用节点</span>
        <span class="axis"
          ><span>0 ms</span><span>{{ formatDuration(waterfall.total) }}</span></span
        >
        <span>耗时</span>
      </div>
      <div v-for="row in visibleRows" :key="row.span.span_id">
        <div
          class="chart-grid span-row"
          :data-category="row.span.category"
          :class="{ selected: selectedSpanId === row.span.span_id }"
        >
          <div
            class="node"
            :style="{ paddingLeft: view === 'tree' ? Math.min(row.depth, 6) * 9 + 'px' : 0 }"
          >
            <button
              v-if="view === 'tree' && row.hasChildren"
              class="branch"
              type="button"
              :aria-label="
                (collapsedSpanIds.has(row.span.span_id) ? '展开 ' : '折叠 ') + row.span.display_name
              "
              :aria-expanded="!collapsedSpanIds.has(row.span.span_id)"
              @click="toggleBranch(row.span.span_id)"
            >
              {{ collapsedSpanIds.has(row.span.span_id) ? '▸' : '▾' }}
            </button>
            <span v-else class="branch leaf" aria-hidden="true">·</span>
            <button
              class="name"
              type="button"
              :title="row.span.name"
              :aria-expanded="selectedSpanId === row.span.span_id"
              @click="inspect(row.span.span_id)"
            >
              <strong>{{ row.span.display_name }}</strong>
              <small :class="{ 'span-error': row.span.status === 'error' }"
                ><b class="node-kind">{{ activityCategoryLabels[row.span.category] }}</b> ·
                {{ spanStatusLabels[row.span.status]
                }}<template v-if="spanSecondaryName(row.span)">
                  · {{ spanSecondaryName(row.span) }}</template
                ></small
              >
            </button>
          </div>
          <button
            class="lane"
            type="button"
            :aria-label="'查看 ' + row.span.display_name + ' 的详情'"
            :aria-expanded="selectedSpanId === row.span.span_id"
            @click="inspect(row.span.span_id)"
          >
            <span
              class="bar"
              :class="'bar-' + row.span.status"
              :style="{ left: row.offset + '%', width: row.width + '%' }"
            ></span>
          </button>
          <span class="duration">{{ formatDuration(row.duration) }}</span>
        </div>
        <dl v-if="selectedSpanId === row.span.span_id" class="span-fields">
          <dt>节点标识</dt>
          <dd>
            <code>{{ row.span.name }}</code>
          </dd>
          <dt>OpenInference 类型</dt>
          <dd>{{ row.span.kind }}</dd>
          <dt>Span ID</dt>
          <dd>
            <code>{{ row.span.span_id }}</code>
          </dd>
          <dt>父 Span ID</dt>
          <dd>
            <code>{{ row.span.parent_span_id || '根节点' }}</code>
          </dd>
          <dt>开始时间</dt>
          <dd>{{ preciseTraceTime(row.span.started_at) }}</dd>
          <dt v-if="row.span.ended_at">结束时间</dt>
          <dd v-if="row.span.ended_at">{{ preciseTraceTime(row.span.ended_at) }}</dd>
          <template v-if="row.span.diagnostics.length">
            <dt>问题日志</dt>
            <dd><TraceDiagnosticLog :entries="row.span.diagnostics" /></dd>
          </template>
          <template v-if="row.span.error_type">
            <dt>异常类型</dt>
            <dd class="span-error">{{ row.span.error_type }}</dd>
          </template>
          <template v-for="field in spanFields(row.span)" :key="field.key">
            <dt :title="field.key">{{ field.label }}</dt>
            <dd>{{ field.value }}</dd>
          </template>
        </dl>
      </div>
    </div>
  </div>
  <div v-else class="analysis-empty" role="status">
    {{
      view === 'issues' ? '已加载的节点中没有失败或中断记录' : '已加载的节点中没有已结束的子调用'
    }}
  </div>
</template>

<script setup lang="ts">
import { computed, ref } from 'vue'
import { preciseTraceTime } from '../labels'
import TraceDiagnosticLog from './TraceDiagnosticLog.vue'
import {
  analysisRows,
  buildWaterfall,
  formatDuration,
  spanFields,
  spanSecondaryName,
  activityCategoryLabels,
  spanStatusLabels,
  type SpanView,
} from '../traceWaterfall'
import type { SpanKind, TraceRun, TraceSpan } from '../types'

const props = defineProps<{
  run: TraceRun
  spans: TraceSpan[]
  kind: SpanKind | ''
  observedAt: number
}>()
const views: { value: SpanView; label: string }[] = [
  { value: 'tree', label: '调用树' },
  { value: 'latency', label: '耗时排行' },
  { value: 'issues', label: '异常节点' },
]
const view = ref<SpanView>('tree')
const selectedSpanId = ref('')
const collapsedSpanIds = ref(new Set<string>())
const noCollapsedSpans = new Set<string>()
const waterfall = computed(
  /**
   * 分析视图始终读取所有已加载的节点，忽略树的折叠状态。
   * @returns 本次执行的可见节点和时间范围。
   */
  () =>
    buildWaterfall(
      props.run,
      props.spans,
      view.value === 'tree' ? collapsedSpanIds.value : noCollapsedSpans,
      props.observedAt,
      Boolean(props.kind),
    ),
)
const visibleRows = computed(
  /**
   * 根据视图筛选排序，保留各个节点在同一时间轴中的起止位置。
   * @returns 当前视图的节点列表。
   */
  () =>
    view.value === 'tree' ? waterfall.value.rows : analysisRows(waterfall.value.rows, view.value),
)
/**
 * 展开或收起节点的子调用。
 * @param id - 目标节点编号。
 */
function toggleBranch(id: string): void {
  if (collapsedSpanIds.value.has(id)) collapsedSpanIds.value.delete(id)
  else collapsedSpanIds.value.add(id)
}

/**
 * 在选中节点下展示属性和异常详情。
 * @param id - 目标节点编号。
 */
function inspect(id: string): void {
  selectedSpanId.value = selectedSpanId.value === id ? '' : id
}
</script>

<style scoped>
.analysis-controls,
.view-switch {
  display: flex;
  align-items: center;
}
.analysis-controls {
  justify-content: space-between;
  flex-wrap: wrap;
  gap: 8px;
  margin: 18px 0 10px;
}
.view-switch {
  gap: 2px;
  padding: 3px;
  border: 1px solid var(--tz-border);
  border-radius: 7px;
  background: var(--tz-surface-muted);
}
.view-switch button {
  padding: 5px 10px;
  border: 0;
  border-radius: 4px;
  color: var(--tz-ink-secondary);
  background: transparent;
  font: inherit;
  font-size: 13px;
  cursor: pointer;
}
.view-switch [aria-pressed='true'] {
  background: var(--tz-primary);
  color: var(--tz-surface);
}
.coverage,
.analysis-note,
.analysis-empty {
  color: var(--tz-ink-muted);
  font-size: 12px;
}
.analysis-note {
  margin: 8px 0;
}
.analysis-empty {
  border: 1px dashed var(--tz-border);
  padding: 24px;
  text-align: center;
}
.chart-scroll {
  overflow-x: auto;
  border: 1px solid var(--tz-border);
  border-radius: 8px;
}
.chart {
  min-width: 460px;
}
.chart-grid {
  display: grid;
  grid-template-columns: minmax(168px, 1fr) minmax(130px, 1.1fr) minmax(76px, auto);
  align-items: center;
  column-gap: 12px;
  padding: 0 12px;
}
.chart-scale {
  height: 30px;
  background: var(--tz-surface-muted);
  color: var(--tz-ink-muted);
  font-size: 12px;
}
.axis {
  display: flex;
  justify-content: space-between;
}
.chart-scale > :last-child,
.duration {
  text-align: right;
}
.span-row[data-category='LLM'] {
  --node-color: #1e3a8a;
}
.span-row[data-category='TOOL'] {
  --node-color: #7c3aed;
}
.span-row[data-category='RETRIEVER'] {
  --node-color: #047857;
}
.span-row[data-category='EMBEDDING'] {
  --node-color: #0284c7;
}
.span-row[data-category='RERANKER'] {
  --node-color: #b45309;
}
.span-row[data-category='AGENT'] {
  --node-color: var(--tz-primary);
}
.span-row[data-category='CONTEXT'] {
  --node-color: var(--tz-success);
}
.span-row[data-category='GUARDRAIL'] {
  --node-color: var(--tz-warning);
}
.span-row[data-category='CHAIN'] {
  --node-color: var(--tz-ink-secondary);
}
.span-row[data-category='INTERNAL'] {
  --node-color: var(--tz-ink-muted);
}
.node-kind {
  color: var(--node-color);
  font-weight: 600;
}
.span-row {
  min-height: 45px;
  border-top: 1px solid var(--tz-border-soft);
}
.span-row:hover,
.span-row.selected {
  background: var(--tz-primary-soft);
}
.node {
  display: flex;
  align-items: center;
  min-width: 0;
}
.branch,
.name,
.lane {
  color: inherit;
  background: transparent;
  font: inherit;
  border: 0;
  padding: 0;
  cursor: pointer;
}
.branch {
  flex: 0 0 22px;
  text-align: center;
  color: var(--tz-ink-muted);
}
.branch.leaf {
  cursor: default;
}
.name {
  min-width: 0;
  text-align: left;
}
.name strong,
.name small {
  display: block;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.name strong {
  font-size: 13px;
  font-weight: 600;
}
.name small {
  margin-top: 2px;
  color: var(--tz-ink-muted);
  font-size: 11px;
}
.lane {
  position: relative;
  height: 32px;
  border-inline: 1px solid var(--tz-border);
  background: repeating-linear-gradient(
    to right,
    transparent 0 calc(25% - 1px),
    var(--tz-border-soft) calc(25% - 1px) 25%
  );
}
.bar {
  position: absolute;
  top: 10px;
  height: 12px;
  min-width: 2px;
  border-radius: 3px;
  background: var(--node-color);
}
.bar-error {
  background: var(--tz-danger);
}
.bar-interrupted {
  background: var(--tz-ink-muted);
}
.bar-running {
  background: repeating-linear-gradient(135deg, #bfdbfe 0 4px, var(--tz-primary) 4px 8px);
}
.duration {
  font-size: 12px;
  font-variant-numeric: tabular-nums;
}
.span-fields {
  margin: 0;
  display: grid;
  grid-template-columns: auto 1fr;
  gap: 4px 16px;
  border-top: 1px solid var(--tz-border-soft);
  padding: 10px 14px;
  background: var(--tz-surface-muted);
  font-size: 12px;
}
.span-fields dt {
  color: var(--tz-ink-muted);
}
.span-fields dd {
  margin: 0;
  overflow-wrap: anywhere;
}
.span-error,
.name .span-error {
  color: var(--tz-danger);
}
@media (max-width: 640px) {
  .chart {
    min-width: 0;
  }
  .chart-grid {
    grid-template-columns: minmax(100px, 1.5fr) minmax(50px, 1fr) auto;
    column-gap: 6px;
    padding-inline: 5px;
  }
  .branch {
    flex-basis: 14px;
  }
  .span-fields {
    grid-template-columns: minmax(75px, 40%) minmax(0, 1fr);
    gap: 5px;
    overflow-wrap: anywhere;
  }
}
</style>
