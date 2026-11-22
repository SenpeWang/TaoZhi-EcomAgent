<template>
  <EvaluationRunInspector
    v-if="selected"
    :key="selected.run_id"
    :initial-run="selected"
    :api="api"
    :owner-filter="isAdmin ? owner : null"
    @back="selected = null"
    @inspect="(taskId, traceId) => emit('inspect', taskId, traceId)"
  />
  <section v-else class="panel">
    <div class="section-heading">
      <h3>{{ isAdmin ? '企业评测批次' : '我的评测批次' }}</h3>
      <el-button :loading="busy" @click="reload">刷新</el-button>
    </div>
    <el-table
      v-if="datasets.length"
      :data="datasets"
      row-key="dataset_id"
      class="evaluation-datasets"
      empty-text="还没有数据集登记"
    >
      <el-table-column label="数据集" min-width="240">
        <template #default="{ row }">
          {{ row.name }}
          <small class="evaluation-meta">{{ row.dataset_id }}</small>
        </template>
      </el-table-column>
      <el-table-column label="来源" min-width="120">
        <template #default="{ row }">{{ evaluationModeLabel(row.source) }}</template>
      </el-table-column>
      <el-table-column label="用例数" width="90" prop="case_count" />
      <el-table-column label="批次数" width="90" prop="batch_count" />
      <el-table-column label="平均通过率" width="120">
        <template #default="{ row }">{{ formatEvaluationPercent(row.avg_pass_rate) }}</template>
      </el-table-column>
      <el-table-column label="最近批次" min-width="140">
        <template #default="{ row }">{{ traceTime(row.last_run_at) }}</template>
      </el-table-column>
    </el-table>
    <el-alert
      v-if="datasetError"
      type="warning"
      show-icon
      :title="datasetError"
      :closable="false"
    />
    <div class="evaluation-filters">
      <TenantOwnerSelect v-if="isAdmin" v-model="owner" :api="api" />
      <el-select v-model="mode" placeholder="全部执行方式" aria-label="按执行方式筛选">
        <el-option value="" label="全部执行方式" />
        <el-option
          v-for="(label, code) in evaluationModes"
          :key="code"
          :value="code"
          :label="label"
        />
      </el-select>
      <el-select v-model="gate" placeholder="全部门禁结果" aria-label="按门禁结果筛选">
        <el-option value="" label="全部门禁结果" />
        <el-option value="true" label="门禁通过" />
        <el-option value="false" label="门禁阻断" />
      </el-select>
    </div>
    <el-alert v-if="error" type="error" show-icon :title="error" :closable="false" />
    <el-table
      v-else
      v-loading="busy"
      :data="rows"
      row-key="run_id"
      empty-text="当前范围没有评测批次"
    >
      <el-table-column label="评测批次" min-width="200">
        <template #default="{ row }">
          <button class="evaluation-link" type="button" @click="selected = row">
            {{ traceTime(row.created_at) }}
          </button>
          <small class="evaluation-meta">{{ row.run_id }}</small>
        </template>
      </el-table-column>
      <el-table-column label="发起人" min-width="110">
        <template #default="{ row }">{{ row.owner_name || row.owner_id }}</template>
      </el-table-column>
      <el-table-column label="数据集" min-width="150">
        <template #default="{ row }">{{ row.dataset_name || '未登记' }}</template>
      </el-table-column>
      <el-table-column label="执行方式" min-width="130">
        <template #default="{ row }">{{ evaluationSource(row) }}</template>
      </el-table-column>
      <el-table-column label="用例结果" min-width="185">
        <template #default="{ row }">
          <strong>{{ row.passed_cases }} / {{ row.total_cases }} 通过</strong>
          <small class="evaluation-meta"
            >{{ row.failed_cases }} 失败 · {{ row.unassessable_cases }} 不可判定</small
          >
        </template>
      </el-table-column>
      <el-table-column label="指标" min-width="170">
        <template #default="{ row }">
          <strong>{{ formatEvaluationPercent(row.pass_rate) }} 通过率</strong>
          <small class="evaluation-meta"
            >均分 {{ formatEvaluationPercent(row.mean_score) }} · P50
            {{ formatEvaluationLatency(row.latency_p50_ms) }}</small
          >
        </template>
      </el-table-column>
      <el-table-column label="发布门禁" width="100">
        <template #default="{ row }">
          <el-tag :type="row.gate_passed ? 'success' : 'danger'" size="small">{{
            row.gate_passed ? '通过' : '阻断'
          }}</el-tag>
        </template>
      </el-table-column>
    </el-table>
    <nav class="evaluation-pager" aria-label="评测批次分页">
      <span>第 {{ page }} 页</span>
      <el-button :disabled="busy || page === 1" @click="turn(false)">上一页</el-button>
      <el-button :disabled="busy || !nextCursor" @click="turn(true)">下一页</el-button>
    </nav>
  </section>
</template>

<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import type { ApiReader, Items } from '../../shared/apiTypes'
import { useCursorPage } from '../../shared/useCursorPage'
import {
  evaluationModes,
  evaluationModeLabel,
  evaluationSource,
  formatEvaluationLatency,
  formatEvaluationPercent,
  type EvaluationDataset,
  type EvaluationMode,
  type EvaluationRun,
} from './evaluationTypes'
import { traceTime } from '../../shared/labels'
import EvaluationRunInspector from './EvaluationRunInspector.vue'
import TenantOwnerSelect from './TenantOwnerSelect.vue'
import './evaluations.css'

const props = defineProps<{ isAdmin: boolean; api: ApiReader }>()
const emit = defineEmits<{ (e: 'inspect', taskId: string, traceId: string): void }>()
const selected = ref<EvaluationRun | null>(null)
const owner = ref('all')
const mode = ref<EvaluationMode | ''>('')
const gate = ref('')
const datasets = ref<EvaluationDataset[]>([])
const datasetError = ref('')
const endpoint = computed(() => {
  const query = new URLSearchParams({ limit: '25' })
  if (props.isAdmin) query.set('owner_id', owner.value)
  if (mode.value) query.set('mode', mode.value)
  if (gate.value) query.set('gate_passed', gate.value)
  return '/evaluations/runs?' + query
})
const { rows, busy, error, page, nextCursor, refresh, reset, turn } = useCursorPage<EvaluationRun>(
  props.api,
  endpoint,
)

/** 读取当前账号可见的数据集与批次指标汇总。 */
async function loadDatasets(): Promise<void> {
  datasetError.value = ''
  try {
    datasets.value = (await props.api<Items<EvaluationDataset>>('/evaluations/datasets')).items
  } catch (cause) {
    datasetError.value = cause instanceof Error ? cause.message : '读取数据集失败'
  }
}

/** 同时刷新数据集汇总与当前批次页。 */
async function reload(): Promise<void> {
  await Promise.all([refresh(), loadDatasets()])
}

onMounted(loadDatasets)
watch(endpoint, reset)
</script>
