<template>
  <EvaluationRunInspector
    v-if="selected"
    :key="selected.run_id"
    :summary="selected"
    :api="api"
    :owner-filter="isAdmin ? owner : null"
    @back="selected = null"
    @inspect="(taskId, traceId) => emit('inspect', taskId, traceId)"
  />
  <section v-else class="panel">
    <div class="section-heading">
      <h3>{{ isAdmin ? '企业评测批次' : '我的评测批次' }}</h3>
      <el-button :loading="busy" @click="refresh">刷新</el-button>
    </div>
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
import { computed, ref } from 'vue'
import { useEvaluationPage } from '../composables/useEvaluationPage'
import {
  evaluationModes,
  evaluationSource,
  type EvaluationApi,
  type EvaluationMode,
  type EvaluationRun,
} from '../evaluations'
import { traceTime } from '../labels'
import EvaluationRunInspector from './EvaluationRunInspector.vue'
import TenantOwnerSelect from './TenantOwnerSelect.vue'
import '../evaluations.css'

const props = defineProps<{ isAdmin: boolean; api: EvaluationApi }>()
const emit = defineEmits<{ (e: 'inspect', taskId: string, traceId: string): void }>()
const selected = ref<EvaluationRun | null>(null)
const owner = ref('all')
const mode = ref<EvaluationMode | ''>('')
const gate = ref('')
const endpoint = computed(() => {
  const query = new URLSearchParams({ limit: '25' })
  if (props.isAdmin) query.set('owner_id', owner.value)
  if (mode.value) query.set('mode', mode.value)
  if (gate.value) query.set('gate_passed', gate.value)
  return '/evaluations/runs?' + query
})
const { rows, busy, error, page, nextCursor, refresh, turn } = useEvaluationPage<EvaluationRun>(
  props.api,
  endpoint,
)
</script>
