<template>
  <div class="evaluation-tab evaluation-overview">
    <el-alert v-if="trendsError" type="warning" show-icon :title="trendsError" :closable="false" />
    <el-skeleton v-else-if="!trends" :rows="3" animated />
    <template v-else-if="trendItems.length">
      <div class="evaluation-overview-summary">
        <span class="evaluation-overview-batches">批次 {{ trends.aggregate.batches }}</span>
        <span v-if="aggregateSpec">
          {{ aggregateLabel }}：最新
          {{ formatAggregateValue(aggregateSpec, aggregateStats.latest) }} · 平均
          {{ formatAggregateValue(aggregateSpec, aggregateStats.average) }} · 最佳
          {{ formatAggregateValue(aggregateSpec, aggregateStats.best) }}
        </span>
      </div>
      <div class="evaluation-metric-grid">
        <el-tooltip
          v-for="card in overviewMetricCards"
          :key="card.spec.key"
          placement="top"
          :content="metricTooltip(card.spec)"
        >
          <div class="evaluation-metric-card evaluation-metric-card-compact">
            <span class="evaluation-card-label">{{ shortMetricLabel(card.spec) }}</span>
            <strong class="evaluation-metric-value">{{
              formatMetricValue(card.spec, card.latest)
            }}</strong>
            <span
              v-if="card.delta !== null"
              class="evaluation-metric-delta"
              :class="metricDeltaTone(card.spec, card.delta)"
              >较基线 {{ formatMetricDelta(card.spec, card.delta) }}</span
            >
          </div>
        </el-tooltip>
      </div>
      <el-button
        v-if="metricsCount > coreMetricCount"
        link
        type="primary"
        class="evaluation-expand-metrics"
        @click="showAllMetrics = !showAllMetrics"
        >{{ showAllMetrics ? '收起指标' : `展开全部指标（${metricsCount} 项）` }}</el-button
      >
      <h4>批次趋势（最近 {{ trendItems.length }} 个批次）</h4>
      <el-table
        :data="trendItems"
        row-key="run_id"
        class="evaluation-datasets evaluation-trend-table"
        size="small"
      >
        <el-table-column label="批次时间" width="155">
          <template #default="{ row }">
            <button class="evaluation-link" type="button" @click="emit('openRun', row.run_id)">
              {{ traceTime(row.created_at) }}
            </button>
          </template>
        </el-table-column>
        <el-table-column label="算法版本" width="115">
          <template #default="{ row }">{{ row.algorithm_version || '未绑定' }}</template>
        </el-table-column>
        <el-table-column label="执行方式" width="100">
          <template #default="{ row }">{{ evaluationModeLabel(row.mode) }}</template>
        </el-table-column>
        <el-table-column
          v-for="spec in tableMetrics"
          :key="spec.key"
          :label="shortMetricLabel(spec)"
          width="100"
        >
          <template #default="{ row }">
            <el-tooltip :content="metricTooltip(spec)" placement="top">
              <span>{{ formatMetricValue(spec, row.summary?.[spec.key] ?? null) }}</span>
            </el-tooltip>
          </template>
        </el-table-column>
        <el-table-column label="门禁" width="70">
          <template #default="{ row }">
            <el-tag :type="row.gate_passed ? 'success' : 'danger'" size="small">
              {{ row.gate_passed ? '通过' : '阻断' }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column label="基线" width="70">
          <template #default="{ row }">
            <el-tag v-if="row.is_baseline" type="warning" size="small">基线</el-tag>
            <el-tag v-else-if="!row.comparable" type="info" size="small">不可比</el-tag>
            <span v-else class="evaluation-meta">—</span>
          </template>
        </el-table-column>
        <el-table-column label="操作" width="70" fixed="right">
          <template #default="{ row }">
            <el-button link type="primary" @click="emit('openRun', row.run_id)">查看</el-button>
          </template>
        </el-table-column>
      </el-table>
    </template>
    <div v-else class="evaluation-empty-card">
      <strong>该版本暂无 {{ evalKindLabel }} 批次</strong>
      <p class="evaluation-meta">
        数据集版本 {{ datasetVersion || '—' }} · 口径 {{ evaluationScopeLabel(scope) }} · 执行方式
        {{ evaluationModeLabel(mode) }}
      </p>
      <el-button type="primary" @click="emit('launch')">去测评启动</el-button>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, ref } from 'vue'
import {
  evaluationModeLabel,
  evaluationScopeLabel,
  formatMetricDelta,
  formatMetricValue,
  metricDeltaTone,
  type EvaluationMetricSpec,
  type EvaluationMode,
  type EvaluationScope,
  type EvaluationTrends,
} from './evaluationTypes'
import {
  formatAggregateValue,
  metricTooltip,
  shortMetricLabel,
  type MetricCard,
} from './evaluationHelpers'
import { traceTime } from '../../shared/labels'

const props = defineProps<{
  trends: EvaluationTrends | null
  trendsError: string
  metricCards: MetricCard[]
  coreMetricKeys: Set<string>
  metricsCount: number
  coreMetricCount: number
  tableMetrics: EvaluationMetricSpec[]
  aggregateSpec: EvaluationMetricSpec | null
  aggregateStats: {
    batches: number
    latest: number | null
    average: number | null
    best: number | null
    label: string
  }
  aggregateLabel: string
  evalKindLabel: string
  datasetVersion: string
  scope: EvaluationScope
  mode: EvaluationMode
}>()
const emit = defineEmits<{
  (e: 'openRun', runId: string): void
  (e: 'launch'): void
}>()

const showAllMetrics = ref(false)
const trendItems = computed(() => props.trends?.items ?? [])
const overviewMetricCards = computed(() =>
  showAllMetrics.value
    ? props.metricCards
    : props.metricCards.filter((card) => props.coreMetricKeys.has(card.spec.key)),
)
</script>
