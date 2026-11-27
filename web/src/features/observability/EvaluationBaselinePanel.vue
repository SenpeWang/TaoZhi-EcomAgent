<template>
  <el-alert
    v-if="baselinesError"
    type="warning"
    show-icon
    :title="baselinesError"
    :closable="false"
  />
  <el-table
    v-else
    v-loading="baselinesBusy"
    :data="baselines"
    row-key="baseline_id"
    class="evaluation-datasets"
    empty-text="当前范围还没有基线，可在批次页把批次设为基线"
  >
    <el-table-column label="状态" width="85">
      <template #default="{ row }">
        <el-tag :type="row.active ? 'success' : 'info'" size="small">{{
          row.active ? 'active' : '历史'
        }}</el-tag>
      </template>
    </el-table-column>
    <el-table-column label="数据集版本" width="120">
      <template #default="{ row }">{{ row.dataset_version }}</template>
    </el-table-column>
    <el-table-column label="口径" width="95">
      <template #default="{ row }">{{ evaluationScopeLabel(row.scope) }}</template>
    </el-table-column>
    <el-table-column label="执行方式" min-width="125">
      <template #default="{ row }">{{ evaluationModeLabel(row.mode) }}</template>
    </el-table-column>
    <el-table-column label="批次" min-width="190">
      <template #default="{ row }">
        <button class="evaluation-link" type="button" @click="emit('openRun', row.run_id)">
          {{ traceTime(row.run_created_at) }}
        </button>
        <small class="evaluation-meta">{{ row.run_id }}</small>
      </template>
    </el-table-column>
    <el-table-column :label="baselineMetricLabel" width="125">
      <template #default="{ row }">{{ formatEvaluationPercent(row.run_pass_rate) }}</template>
    </el-table-column>
    <el-table-column label="门禁" width="90">
      <template #default="{ row }">
        <el-tag :type="row.run_gate_passed ? 'success' : 'danger'" size="small">
          {{ row.run_gate_passed ? '通过' : '阻断' }}
        </el-tag>
      </template>
    </el-table-column>
    <el-table-column label="算法版本" min-width="120">
      <template #default="{ row }">{{ row.algorithm_version || '—' }}</template>
    </el-table-column>
    <el-table-column label="设置人" min-width="110">
      <template #default="{ row }">{{ row.created_by }}</template>
    </el-table-column>
    <el-table-column label="设置时间" min-width="150">
      <template #default="{ row }">{{ traceTime(row.created_at) }}</template>
    </el-table-column>
    <el-table-column label="备注" min-width="160">
      <template #default="{ row }">{{ row.note || '—' }}</template>
    </el-table-column>
  </el-table>
</template>

<script setup lang="ts">
import {
  evaluationModeLabel,
  evaluationScopeLabel,
  formatEvaluationPercent,
  type EvaluationBaseline,
} from './evaluationTypes'
import { traceTime } from '../../shared/labels'

defineProps<{
  baselines: EvaluationBaseline[]
  baselinesBusy: boolean
  baselinesError: string
  baselineMetricLabel: string
}>()
const emit = defineEmits<{ (e: 'openRun', runId: string): void }>()
</script>
