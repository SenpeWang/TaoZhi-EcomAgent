<template>
  <ul class="diagnostic-log" aria-label="步骤诊断日志">
    <li v-for="(entry, index) in entries" :key="index" :data-level="entry.level">
      <div class="diagnostic-heading">
        <strong
          >{{ diagnosticCategoryLabels[entry.category] }} ·
          {{ entry.level === 'error' ? '错误' : '警告' }}</strong
        >
        <span>{{ diagnosticOutcomeLabels[entry.outcome] }}</span>
      </div>
      <p>{{ entry.message }}</p>
      <div class="diagnostic-evidence">
        <time :datetime="entry.timestamp">{{ preciseTraceTime(entry.timestamp) }}</time>
        <code>{{ entry.code }}</code>
        <code v-if="entry.http_status !== null">HTTP {{ entry.http_status }}</code>
        <code v-if="entry.exception_type">{{ entry.exception_type }}</code>
      </div>
    </li>
  </ul>
</template>

<script setup lang="ts">
import { preciseTraceTime } from '../labels'
import { diagnosticCategoryLabels, diagnosticOutcomeLabels } from '../traceActivity'
import type { TraceDiagnostic } from '../types'

defineProps<{ entries: TraceDiagnostic[] }>()
</script>

<style scoped>
.diagnostic-log {
  display: grid;
  gap: 8px;
  margin: 6px 0;
  padding: 0;
  list-style: none;
}
.diagnostic-log li {
  border-inline-start: 3px solid var(--tz-warning);
  padding: 3px 0 3px 9px;
  min-width: 0;
  overflow-wrap: anywhere;
}
.diagnostic-log li[data-level='error'] {
  border-color: var(--tz-danger);
}
.diagnostic-heading,
.diagnostic-evidence {
  display: flex;
  flex-wrap: wrap;
  align-items: baseline;
  gap: 4px 9px;
  font-size: 12px;
}
.diagnostic-heading span {
  color: var(--tz-ink-secondary);
}
.diagnostic-log p {
  margin: 3px 0;
  color: var(--tz-ink);
  line-height: 1.5;
}
.diagnostic-evidence {
  color: var(--tz-ink-muted);
  font-size: 11px;
  font-variant-numeric: tabular-nums;
}
</style>
