<template>
  <el-alert v-if="runsError" type="warning" show-icon :title="runsError" :closable="false" />
  <div v-else-if="!kindDatasetCount" class="evaluation-hint-card">
    当前评测体系尚未登记数据集，请先到「测评启动」登记算法版本并发起评测。
  </div>
  <div v-else-if="!metricsCount" class="evaluation-hint-card">
    当前口径尚未登记指标，暂无可展示的批次指标列。
  </div>
  <el-table
    v-else
    v-loading="runsBusy"
    :data="runs"
    row-key="run_id"
    class="evaluation-datasets evaluation-runs-table"
    size="small"
    empty-text="当前范围内没有评测批次"
  >
    <el-table-column label="批次时间" width="130">
      <template #default="{ row }">
        <button
          class="evaluation-link"
          type="button"
          :title="row.run_id"
          @click="emit('openRun', row.run_id)"
        >
          {{ traceTime(row.created_at) }}
        </button>
      </template>
    </el-table-column>
    <el-table-column label="运行次数" width="86">
      <template #default="{ row }">
        <el-tooltip placement="top">
          <template #content>
            <div>批次：{{ row.run_id }}</div>
            <div>运行备注：{{ row.trigger_note || '—' }}</div>
            <div>重跑来源：{{ row.parent_run_id || '—' }}</div>
          </template>
          <span class="evaluation-run-seq">
            <el-tag v-if="row.run_seq" effect="plain" size="small">第 {{ row.run_seq }} 次</el-tag>
            <span v-else class="evaluation-meta">—</span>
          </span>
        </el-tooltip>
      </template>
    </el-table-column>
    <el-table-column label="数据集版本" width="110">
      <template #default="{ row }">{{ row.dataset_version || '—' }}</template>
    </el-table-column>
    <el-table-column label="算法版本" width="90">
      <template #default="{ row }">{{ row.profile_version_tag || '未绑定' }}</template>
    </el-table-column>
    <el-table-column label="执行方式" width="90">
      <template #default="{ row }">{{ evaluationModeLabel(row.mode) }}</template>
    </el-table-column>
    <el-table-column
      v-for="spec in tableMetrics"
      :key="spec.key"
      :label="shortMetricLabel(spec)"
      width="90"
    >
      <template #default="{ row }">
        <el-tooltip :content="metricTooltip(spec)" placement="top">
          <span>{{ formatMetricValue(spec, row.summary?.[spec.key] ?? null) }}</span>
        </el-tooltip>
      </template>
    </el-table-column>
    <el-table-column label="门禁" width="60">
      <template #default="{ row }">
        <el-tag :type="row.gate_passed ? 'success' : 'danger'" size="small">
          {{ row.gate_passed ? '通过' : '阻断' }}
        </el-tag>
      </template>
    </el-table-column>
    <el-table-column label="基线" width="60">
      <template #default="{ row }">
        <el-tag v-if="row.is_baseline" type="warning" size="small">基线</el-tag>
        <el-tag v-else-if="!row.comparable" type="info" size="small">不可比</el-tag>
        <span v-else class="evaluation-meta">—</span>
      </template>
    </el-table-column>
    <el-table-column label="操作" width="152" fixed="right">
      <template #default="{ row }">
        <el-button link type="primary" @click="emit('openRun', row.run_id)">查看</el-button>
        <el-button link type="primary" @click="emit('downloadReport', row.run_id)"
          >原始报告</el-button
        >
        <el-button
          v-if="canWriteEval && row.dataset_id && row.mode === 'live'"
          link
          type="primary"
          @click="emit('rerun', row)"
          >复跑</el-button
        >
      </template>
    </el-table-column>
  </el-table>
  <nav class="evaluation-pager" aria-label="评测批次分页">
    <span>第 {{ runsPageNo }} 页</span>
    <el-button :disabled="runsBusy || runsPageNo === 1" @click="emit('turn', false)"
      >上一页</el-button
    >
    <el-button :disabled="runsBusy || !runsNext" @click="emit('turn', true)">下一页</el-button>
  </nav>
</template>

<script setup lang="ts">
import {
  evaluationModeLabel,
  formatMetricValue,
  type EvaluationMetricSpec,
  type EvaluationRun,
} from './evaluationTypes'
import { metricTooltip, shortMetricLabel } from './evaluationHelpers'
import { traceTime } from '../../shared/labels'

defineProps<{
  runs: EvaluationRun[]
  runsBusy: boolean
  runsError: string
  runsNext: string | null
  runsPageNo: number
  kindDatasetCount: number
  metricsCount: number
  tableMetrics: EvaluationMetricSpec[]
  canWriteEval: boolean
}>()
const emit = defineEmits<{
  (e: 'turn', forward: boolean): void
  (e: 'openRun', runId: string): void
  (e: 'downloadReport', runId: string): void
  (e: 'rerun', run: EvaluationRun): void
}>()
</script>
