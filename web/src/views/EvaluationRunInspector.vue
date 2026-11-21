<template>
  <div>
    <section class="panel">
      <div class="section-heading">
        <h3>批次结果</h3>
        <el-button @click="emit('back')">返回批次列表</el-button>
      </div>
      <div class="evaluation-run-title">
        <strong>{{ traceTime(summary.created_at) }}</strong>
        <el-tag :type="summary.gate_passed ? 'success' : 'danger'" size="small">{{
          summary.gate_passed ? '门禁通过' : '门禁阻断'
        }}</el-tag>
        <span>{{ evaluationSource(summary) }} · {{ summary.owner_name || summary.owner_id }}</span>
      </div>
      <small class="evaluation-meta">{{ summary.run_id }}</small>
      <dl class="evaluation-metrics">
        <div>
          <dt>用例总数</dt>
          <dd>{{ summary.total_cases }}</dd>
        </div>
        <div>
          <dt>通过</dt>
          <dd>{{ summary.passed_cases }}</dd>
        </div>
        <div>
          <dt>失败</dt>
          <dd>{{ summary.failed_cases }}</dd>
        </div>
        <div>
          <dt>不可判定</dt>
          <dd>{{ summary.unassessable_cases }}</dd>
        </div>
      </dl>
      <dl v-if="detail" class="evaluation-measures">
        <div v-for="item in measures" :key="item.key">
          <dt>{{ item.label }}</dt>
          <dd>{{ formatRatio(detail.metrics[item.key]) }}</dd>
        </div>
        <div>
          <dt>已计量输入 / 输出 Token</dt>
          <dd>
            {{ detail.metrics.measured_prompt_tokens ?? '—' }} /
            {{ detail.metrics.measured_completion_tokens ?? '—' }}
          </dd>
        </div>
      </dl>
      <dl class="evaluation-revisions">
        <div>
          <dt>数据集版本</dt>
          <dd :title="summary.dataset_sha256">{{ summary.dataset_sha256.slice(0, 12) }}</dd>
        </div>
        <div>
          <dt>判定器版本</dt>
          <dd :title="summary.judges_sha256">{{ summary.judges_sha256.slice(0, 12) }}</dd>
        </div>
        <div>
          <dt>代码提交</dt>
          <dd :title="summary.git_sha ?? undefined">
            {{ summary.git_sha?.slice(0, 8) || '未记录' }}
          </dd>
        </div>
        <div v-if="summary.transcript_sha256">
          <dt>回放记录</dt>
          <dd :title="summary.transcript_sha256">{{ summary.transcript_sha256.slice(0, 12) }}</dd>
        </div>
        <div v-if="summary.counterexamples_sha256">
          <dt>反例集版本</dt>
          <dd :title="summary.counterexamples_sha256">
            {{ summary.counterexamples_sha256.slice(0, 12) }}
          </dd>
        </div>
        <div v-if="summary.model_config_sha256">
          <dt>模型配置</dt>
          <dd :title="summary.model_config_sha256">
            {{ summary.model_config_sha256.slice(0, 12) }}
          </dd>
        </div>
      </dl>
    </section>
    <div v-if="detailError" class="panel evaluation-error">
      <el-alert type="error" :title="detailError" :closable="false" /><el-button @click="loadDetail"
        >重试</el-button
      >
    </div>
    <el-skeleton v-else-if="!detail" class="panel" :rows="2" animated />
    <EvaluationBaseline
      v-else
      :stored-comparison="detail.comparison"
      :run="summary"
      :api="api"
      :owner-filter="ownerFilter"
      @select-case="selectCase"
    />
    <section class="panel">
      <div class="section-heading">
        <h3>用例结果</h3>
        <el-select
          v-model="status"
          class="evaluation-case-filter"
          placeholder="全部结果"
          aria-label="按用例结果筛选"
        >
          <el-option value="" label="全部结果" />
          <el-option
            v-for="(label, code) in evaluationStatuses"
            :key="code"
            :label="label"
            :value="code"
          />
        </el-select>
      </div>
      <div v-if="error" class="evaluation-error">
        <el-alert type="error" :title="error" :closable="false" />
        <el-button @click="refresh">重试</el-button>
      </div>
      <el-table
        v-else
        v-loading="busy"
        :data="rows"
        row-key="case_id"
        empty-text="没有符合条件的用例"
      >
        <el-table-column label="用例" min-width="215">
          <template #default="{ row }"
            ><button class="evaluation-link" type="button" @click="selectCase(row.case_id)">
              {{ row.name }}
            </button>
            <small class="evaluation-meta">{{ row.case_id }}</small>
          </template>
        </el-table-column>
        <el-table-column label="分组" min-width="90">
          <template #default="{ row }">{{ evaluationSuites[row.suite] || row.suite }}</template>
        </el-table-column>
        <el-table-column label="得分" width="75"
          ><template #default="{ row }">{{
            row.score?.toFixed(2) ?? '—'
          }}</template></el-table-column
        >
        <el-table-column label="判定" min-width="115">
          <template #default="{ row }">
            <el-tag :type="evaluationTags[row.status as EvaluationStatus]" size="small">{{
              evaluationStatuses[row.status as EvaluationStatus]
            }}</el-tag>
            <small v-if="row.hard_failure" class="evaluation-meta">硬性违规</small>
          </template>
        </el-table-column>
      </el-table>
      <nav class="evaluation-pager" aria-label="评测用例分页">
        <span>第 {{ page }} 页</span>
        <el-button :disabled="busy || page === 1" @click="turn(false)">上一页</el-button>
        <el-button :disabled="busy || !nextCursor" @click="turn(true)">下一页</el-button>
      </nav>
    </section>
    <EvaluationCaseInspector
      v-if="selectedCase"
      :key="selectedCase"
      :run="summary"
      :case-id="selectedCase"
      :api="api"
      @close="selectedCase = ''"
      @inspect="(taskId, traceId) => emit('inspect', taskId, traceId)"
    />
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useEvaluationPage } from '../composables/useEvaluationPage'
import {
  evaluationSource,
  evaluationRunPath,
  evaluationStatuses,
  evaluationTags,
  evaluationSuites,
  type EvaluationApi,
  type EvaluationRun,
  type EvaluationRunDetail,
  type EvaluationCase,
  type EvaluationRatio,
  type EvaluationStatus,
} from '../evaluations'
import { traceTime } from '../labels'
import EvaluationBaseline from './EvaluationBaseline.vue'
import EvaluationCaseInspector from './EvaluationCaseInspector.vue'

const props = defineProps<{
  summary: EvaluationRun
  api: EvaluationApi
  ownerFilter: string | null
}>()
const emit = defineEmits<{
  (e: 'back'): void
  (e: 'inspect', taskId: string, traceId: string): void
}>()
const measures = [
  { key: 'required_tool_coverage', label: '必调工具覆盖率' },
  { key: 'parameter_accuracy', label: '工具参数准确率' },
  { key: 'reference_fact_coverage', label: '参考事实覆盖率' },
  { key: 'call_coverage_among_known', label: 'Token 用量采集覆盖率' },
] as const
const detail = ref<EvaluationRunDetail | null>(null)
const detailError = ref('')

/** 加载已入库的批次指标及随报告保存的基线比较。 */
async function loadDetail(): Promise<void> {
  detailError.value = ''
  try {
    detail.value = await props.api<EvaluationRunDetail>(evaluationRunPath(props.summary.run_id))
  } catch (cause) {
    detailError.value = cause instanceof Error ? cause.message : '读取批次指标失败'
  }
}

/**
 * 展示有效分母与覆盖比例，缺测或没有适用调用时保留对应状态。
 * @param ratio - 该指标的适用样本和得分。
 * @returns 比例及样本数的说明。
 */
function formatRatio(ratio: EvaluationRatio | null): string {
  if (!ratio) return '未记录'
  if (ratio.rate === null) return '无可判定样本'
  return (ratio.rate * 100).toFixed(1) + '%（' + ratio.numerator + '/' + ratio.denominator + '）'
}

onMounted(loadDetail)
const status = ref('')
const selectedCase = ref('')
const endpoint = computed(() => {
  const query = new URLSearchParams({ limit: '30' })
  if (status.value) query.set('status', status.value)
  return evaluationRunPath(props.summary.run_id) + '/cases?' + query
})
const { rows, busy, error, page, nextCursor, refresh, turn } = useEvaluationPage<EvaluationCase>(
  props.api,
  endpoint,
)

/**
 * 打开本批次的一条用例判定。
 * @param id - 已展示的用例编号。
 */
function selectCase(id: string): void {
  selectedCase.value = id
}
</script>
