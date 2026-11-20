<template>
  <table class="activity-log" aria-label="按开始时间排列的行动记录">
    <thead>
      <tr>
        <th scope="col">开始时间</th>
        <th scope="col">执行者</th>
        <th scope="col">动作与结果</th>
        <th scope="col">状态与耗时</th>
      </tr>
    </thead>
    <tbody>
      <tr v-for="{ entry, action, result, elapsed } in rows" :key="entry.span_id">
        <td class="activity-time">
          <time :datetime="entry.started_at" :title="traceTime(entry.started_at)">
            <strong>{{ clock.format(new Date(entry.started_at)) }}</strong>
            <small>{{ date.format(new Date(entry.started_at)) }}</small>
          </time>
        </td>
        <td class="activity-actor">
          <strong :title="entry.actor?.span_id">{{
            entry.actor?.display_name ?? '执行者未记录'
          }}</strong>
          <small
            v-if="entry.actor && instanceLabels.has(entry.actor.span_id)"
            :title="entry.actor.span_id"
          >
            实例 {{ instanceLabels.get(entry.actor.span_id) }}
          </small>
        </td>
        <td class="activity-action">
          <strong>{{ action.title }}</strong>
          <span v-if="action.target" class="activity-target"
            ><span v-if="entry.kind === 'TOOL' && action.title !== '调用工具'">工具 </span
            ><code>{{ action.target }}</code></span
          >
          <small v-if="result">{{ result }}</small>
        </td>
        <td class="activity-status">
          <strong :data-status="entry.status">{{ spanStatusLabels[entry.status] }}</strong>
          <small>{{ elapsed === null ? '耗时未知' : formatDuration(elapsed) }}</small>
        </td>
      </tr>
    </tbody>
  </table>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import { traceTime } from '../labels'
import { activityAction, activityResult, actorInstanceLabels } from '../traceActivity'
import { formatDuration, spanStatusLabels } from '../traceWaterfall'
import type { TraceActivity } from '../types'

const props = defineProps<{ activities: TraceActivity[]; observedAt: number }>()
const clock = new Intl.DateTimeFormat('zh-CN', {
  hour: '2-digit',
  minute: '2-digit',
  second: '2-digit',
  fractionalSecondDigits: 3,
  hour12: false,
})
const date = new Intl.DateTimeFormat('zh-CN', { year: 'numeric', month: '2-digit', day: '2-digit' })

/**
 * 汇总当前页中需要使用编号区分的同名智能体。
 * @returns 本轮同名智能体的执行编号。
 */
function buildInstanceLabels() {
  return actorInstanceLabels(props.activities)
}

/**
 * 按服务端时间顺序，将实际调用转换为可直接阅读的执行记录。
 * @returns 带动作、结果及已观测耗时的行。
 */
function timelineRows() {
  return props.activities.map((entry) => ({
    entry,
    action: activityAction(entry),
    result: activityResult(entry),
    elapsed:
      entry.duration_ms ??
      (entry.status === 'running'
        ? Math.max(0, props.observedAt - Date.parse(entry.started_at))
        : null),
  }))
}
const instanceLabels = computed(buildInstanceLabels)
const rows = computed(timelineRows)
</script>

<style scoped>
.activity-log {
  width: 100%;
  border-collapse: collapse;
  font-size: 13px;
  color: var(--tz-ink);
}
.activity-log th,
.activity-log td {
  padding: 10px 12px;
  text-align: left;
  vertical-align: top;
  border-bottom: 1px solid var(--tz-border-soft);
}
.activity-log th {
  background: var(--tz-surface-muted);
  color: var(--tz-ink-secondary);
  font-size: 12px;
  font-weight: 600;
}
.activity-time,
.activity-status {
  white-space: nowrap;
  font-variant-numeric: tabular-nums;
}
.activity-log strong {
  font-weight: 600;
}
.activity-log small {
  display: block;
  margin-top: 3px;
  color: var(--tz-ink-muted);
  font-size: 12px;
}
.activity-actor,
.activity-action {
  overflow-wrap: anywhere;
}
.activity-target {
  color: var(--tz-primary);
  font-size: 12px;
  margin-inline-start: 6px;
}
.activity-status strong[data-status='running'] {
  color: var(--tz-primary);
}
.activity-status strong[data-status='ok'] {
  color: var(--tz-success);
}
.activity-status strong[data-status='error'] {
  color: var(--tz-danger);
}
.activity-status strong[data-status='interrupted'] {
  color: var(--tz-warning);
}
@media (max-width: 640px) {
  .activity-log,
  .activity-log tbody {
    display: block;
  }
  .activity-log thead {
    position: absolute;
    width: 1px;
    height: 1px;
    overflow: hidden;
    clip-path: inset(50%);
  }
  .activity-log tr {
    display: grid;
    grid-template-columns: minmax(0, 1fr) auto;
    gap: 6px 12px;
    padding: 12px 0;
    border-bottom: 1px solid var(--tz-border);
  }
  .activity-log td {
    border: 0;
    padding: 0;
  }
  .activity-time {
    grid-column: 1;
    color: var(--tz-ink-muted);
  }
  .activity-time small {
    display: inline;
    margin-inline-start: 6px;
  }
  .activity-actor,
  .activity-action {
    grid-column: 1;
  }
  .activity-status {
    grid-column: 2;
    grid-row: 1 / 4;
  }
}
</style>
