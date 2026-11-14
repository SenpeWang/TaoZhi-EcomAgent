<script setup lang="ts">
import { computed } from 'vue'
import { renderMarkdownReport, type MarkdownHeading } from '../markdown'

const props = withDefaults(
  defineProps<{
    content: string
    idPrefix?: string
    compact?: boolean
    showOutline?: boolean
  }>(),
  {
    idPrefix: 'md-sec',
    compact: false,
    showOutline: true,
  },
)

const rendered = computed(() => renderMarkdownReport(props.content || '', props.idPrefix))

const outlineHeadings = computed<MarkdownHeading[]>(() => {
  const candidates = rendered.value.headings.filter((item) => item.level >= 2 && item.level <= 3)
  const list = candidates.length >= 2 ? candidates : rendered.value.headings
  return list.length >= 2 ? list.slice(0, 10) : []
})

/**
 * 平滑滚动到报告中的指定章节标题。
 * @param headingId - 目标章节元素 DOM ID。
 */
function scrollToHeading(headingId: string): void {
  const el = document.getElementById(headingId)
  if (el) {
    el.scrollIntoView({ behavior: 'smooth', block: 'start' })
  }
}
</script>

<template>
  <div class="md-report-shell" :class="{ 'md-report-shell--compact': compact }">
    <div v-if="showOutline && outlineHeadings.length > 0" class="md-outline-strip">
      <span class="md-outline-tag">报告目录</span>
      <button
        v-for="item in outlineHeadings"
        :key="item.id"
        type="button"
        class="md-outline-pill"
        :class="{ 'is-sub': item.level >= 3 }"
        @click="scrollToHeading(item.id)"
      >
        {{ item.text }}
      </button>
    </div>

    <!-- eslint-disable-next-line vue/no-v-html -->
    <div class="md-report-prose" v-html="rendered.html" />
  </div>
</template>

<style scoped>
.md-report-shell {
  display: flex;
  flex-direction: column;
  gap: 12px;
  min-width: 0;
}

.md-outline-strip {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 6px;
  padding: 8px 12px;
  background: #f8fafc;
  border: 1px solid #e2e8f0;
  border-radius: 8px;
}

.md-outline-tag {
  font-size: 11px;
  font-weight: 600;
  color: #64748b;
  letter-spacing: 0.02em;
  margin-right: 4px;
}

.md-outline-pill {
  background: #ffffff;
  border: 1px solid #e2e8f0;
  border-radius: 999px;
  padding: 2px 10px;
  font-size: 12px;
  line-height: 1.5;
  color: #334155;
  cursor: pointer;
  transition:
    background 0.15s,
    border-color 0.15s,
    color 0.15s;
}

.md-outline-pill.is-sub {
  color: #64748b;
}

.md-outline-pill:hover {
  background: #eff6ff;
  border-color: #93c5fd;
  color: #1d4ed8;
}

.md-report-prose {
  color: #1e293b;
  font-size: 14.5px;
  line-height: 1.78;
  word-break: break-word;
  white-space: normal;
}

.md-report-shell--compact .md-report-prose {
  font-size: 13.5px;
  line-height: 1.72;
}

.md-report-prose :deep(.md-h) {
  color: #0f172a;
  font-weight: 700;
  line-height: 1.38;
  scroll-margin-top: 16px;
}

.md-report-prose :deep(.md-h1) {
  font-size: 19px;
  margin: 4px 0 14px;
  padding-bottom: 10px;
  border-bottom: 1px solid #e2e8f0;
}

.md-report-prose :deep(.md-h2) {
  font-size: 16px;
  margin: 22px 0 10px;
  padding-left: 10px;
  border-left: 3.5px solid #2563eb;
}

.md-report-prose :deep(.md-h3) {
  font-size: 14.5px;
  margin: 16px 0 8px;
  color: #1e293b;
}

.md-report-prose :deep(.md-h4) {
  font-size: 13.5px;
  margin: 12px 0 6px;
  color: #334155;
}

.md-report-prose :deep(.md-h:first-child) {
  margin-top: 0;
}

.md-report-prose :deep(.md-p) {
  margin: 0 0 10px;
}

.md-report-prose :deep(.md-p:last-child) {
  margin-bottom: 0;
}

.md-report-prose :deep(strong) {
  color: #0f172a;
  font-weight: 600;
}

.md-report-prose :deep(.md-divider) {
  border: none;
  border-top: 1px solid #e2e8f0;
  margin: 18px 0;
}

.md-report-prose :deep(.md-ul),
.md-report-prose :deep(.md-ol) {
  margin: 6px 0 12px;
  padding-left: 22px;
}

.md-report-prose :deep(.md-li) {
  margin: 4px 0;
}

.md-report-prose :deep(.md-li > .md-ul),
.md-report-prose :deep(.md-li > .md-ol) {
  margin: 4px 0 4px;
}

.md-report-prose :deep(.md-li--todo) {
  list-style: none;
  margin-left: -18px;
  display: flex;
  align-items: flex-start;
  gap: 8px;
}

.md-report-prose :deep(.md-todo) {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 15px;
  height: 15px;
  margin-top: 5px;
  border-radius: 4px;
  border: 1.5px solid #94a3b8;
  font-size: 10px;
  flex-shrink: 0;
  color: #ffffff;
}

.md-report-prose :deep(.md-todo--done) {
  background: #10b981;
  border-color: #10b981;
}

.md-report-prose :deep(.md-table-wrap) {
  margin: 12px 0 16px;
  width: 100%;
  overflow-x: auto;
  border: 1px solid #e2e8f0;
  border-radius: 8px;
  background: #ffffff;
}

.md-report-prose :deep(.md-table) {
  width: 100%;
  border-collapse: collapse;
  font-size: 13px;
  line-height: 1.6;
}

.md-report-prose :deep(.md-table th) {
  background: #f8fafc;
  color: #0f172a;
  font-weight: 600;
  padding: 9px 12px;
  border-bottom: 1px solid #cbd5e1;
  white-space: nowrap;
}

.md-report-prose :deep(.md-table td) {
  padding: 8px 12px;
  color: #334155;
  border-bottom: 1px solid #f1f5f9;
  vertical-align: top;
}

.md-report-prose :deep(.md-table tbody tr:last-child td) {
  border-bottom: none;
}

.md-report-prose :deep(.md-table tbody tr:nth-child(even)) {
  background: #f8fafc;
}

.md-report-prose :deep(.md-table tbody tr:hover) {
  background: #eff6ff;
}

.md-report-prose :deep(.md-callout) {
  margin: 12px 0 14px;
  padding: 10px 14px;
  border-radius: 8px;
  font-size: 13.5px;
  line-height: 1.7;
}

.md-report-prose :deep(.md-callout--neutral) {
  background: #f8fafc;
  border-left: 3.5px solid #94a3b8;
  color: #334155;
}

.md-report-prose :deep(.md-callout--warn) {
  background: #fffbeb;
  border: 1px solid #fde68a;
  border-left: 3.5px solid #d97706;
  color: #92400e;
}

.md-report-prose :deep(.md-callout--info) {
  background: #eff6ff;
  border: 1px solid #dbeafe;
  border-left: 3.5px solid #2563eb;
  color: #1e40af;
}

.md-report-prose :deep(.md-inline-code) {
  font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
  font-size: 0.88em;
  background: #f1f5f9;
  color: #0f172a;
  padding: 1px 6px;
  border-radius: 4px;
  border: 1px solid #e2e8f0;
}

.md-report-prose :deep(.md-code-block) {
  position: relative;
  margin: 12px 0 14px;
  padding: 12px 14px;
  border-radius: 8px;
  background: #0f172a;
  color: #e2e8f0;
  font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
  font-size: 12.5px;
  line-height: 1.65;
  overflow-x: auto;
}

.md-report-prose :deep(.md-code-lang) {
  font-size: 11px;
  color: #94a3b8;
  text-transform: uppercase;
  margin-bottom: 6px;
}

.md-report-prose :deep(.md-link) {
  color: #2563eb;
  text-decoration: none;
  border-bottom: 1px solid rgba(37, 99, 235, 0.35);
}

.md-report-prose :deep(.md-link:hover) {
  color: #1d4ed8;
  border-bottom-color: #1d4ed8;
}
</style>
