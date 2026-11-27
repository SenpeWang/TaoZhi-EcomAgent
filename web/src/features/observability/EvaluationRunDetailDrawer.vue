<template>
  <el-drawer
    v-model="runDetailOpen"
    :title="runDetail ? `批次详情 · ${traceTime(runDetail.run.created_at)}` : '批次详情'"
    size="76%"
  >
    <el-alert
      v-if="runDetailError"
      type="error"
      show-icon
      :title="runDetailError"
      :closable="false"
    />
    <el-skeleton v-else-if="runDetailBusy && !runDetail" :rows="5" animated />
    <template v-else-if="runDetail">
      <dl class="evaluation-facts">
        <div>
          <dt>数据集版本</dt>
          <dd>{{ runDetail.run.dataset_version || '—' }}</dd>
        </div>
        <div>
          <dt>算法版本</dt>
          <dd>{{ runDetail.run.profile_version_tag || '未绑定' }}</dd>
        </div>
        <div>
          <dt>运行次数</dt>
          <dd>{{ runDetail.run.run_seq ? `第 ${runDetail.run.run_seq} 次` : '—' }}</dd>
        </div>
        <div>
          <dt>执行方式与口径</dt>
          <dd>
            {{ evaluationModeLabel(runDetail.run.mode) }} ·
            {{ evaluationScopeLabel(runDetail.run.scope) }}
          </dd>
        </div>
        <div>
          <dt>门禁</dt>
          <dd>
            <el-tag :type="runDetail.run.gate_passed ? 'success' : 'danger'" size="small">
              {{ runDetail.run.gate_passed ? '通过' : '阻断' }}
            </el-tag>
          </dd>
        </div>
        <div>
          <dt>基线</dt>
          <dd>
            <el-tag v-if="runDetail.run.is_baseline" type="warning" size="small">当前基线</el-tag>
            <span v-else class="evaluation-meta">—</span>
          </dd>
        </div>
        <div>
          <dt>发起人</dt>
          <dd>{{ runDetail.run.owner_name || runDetail.run.owner_id }}</dd>
        </div>
        <div v-if="runDetail.run.trigger_note">
          <dt>运行备注</dt>
          <dd>{{ runDetail.run.trigger_note }}</dd>
        </div>
        <div v-if="runDetail.run.parent_run_id">
          <dt>重跑来源</dt>
          <dd class="evaluation-code">{{ runDetail.run.parent_run_id }}</dd>
        </div>
      </dl>
      <div class="evaluation-detail-actions">
        <span class="evaluation-meta"
          >用例 {{ runDetail.run.passed_cases }} / {{ runDetail.run.total_cases }} 通过 · 失败
          {{ runDetail.run.failed_cases }} · 不可判定 {{ runDetail.run.unassessable_cases }}</span
        >
        <div class="evaluation-detail-action-buttons">
          <el-button
            :disabled="!runDetail.run.report_available"
            :title="runDetail.run.report_available ? '下载原始报告 JSON' : '该批次未保存原始报告'"
            @click="downloadRunReport(runDetail.run.run_id)"
            >下载原始报告</el-button
          >
          <el-button
            v-if="view === 'results' && canWriteEval && runDetail.run.dataset_id"
            @click="emit('setBaseline', runDetail.run)"
            >设为基线</el-button
          >
          <el-button
            v-if="
              view === 'results' &&
              canWriteEval &&
              runDetail.run.dataset_id &&
              runDetail.run.mode === 'live'
            "
            @click="emit('rerun', runDetail.run)"
            >复跑</el-button
          >
        </div>
      </div>
      <div v-if="runDetail.dataset_snapshot" class="evaluation-snapshot-line">
        <span>
          数据集快照 {{ runDetail.dataset_snapshot.dataset_version }} · 用例
          {{ runDetail.dataset_snapshot.case_count }}
        </span>
        <el-button link type="primary" @click="openSnapshotCases(runDetail.run.run_id)"
          >查看原始用例</el-button
        >
      </div>
      <p v-else class="evaluation-meta">
        该批次没有数据集内容快照（历史批次未登记快照），无法查看本次运行使用的原始用例。
      </p>
      <div class="evaluation-detail-metrics-head">
        <h4>核心指标</h4>
        <el-button
          v-if="runMetricCards.length > detailMetricCards.length || showAllDetailMetrics"
          link
          type="primary"
          @click="showAllDetailMetrics = !showAllDetailMetrics"
          >{{
            showAllDetailMetrics ? '收起指标' : `展开全部指标（${runMetricCards.length} 项）`
          }}</el-button
        >
      </div>
      <div class="evaluation-metric-grid evaluation-metric-grid-detail">
        <el-tooltip
          v-for="card in detailMetricCards"
          :key="card.spec.key"
          placement="top"
          :content="metricTooltip(card.spec)"
        >
          <div class="evaluation-metric-card evaluation-metric-card-compact">
            <span class="evaluation-card-label">{{ shortMetricLabel(card.spec) }}</span>
            <strong class="evaluation-metric-value">{{
              formatMetricValue(card.spec, card.value)
            }}</strong>
          </div>
        </el-tooltip>
      </div>
      <el-collapse v-model="detailTechOpen" class="evaluation-tech-collapse">
        <el-collapse-item title="技术信息" name="tech">
          <dl class="evaluation-facts evaluation-facts-tech">
            <div>
              <dt>批次编号</dt>
              <dd class="evaluation-code">{{ runDetail.run.run_id }}</dd>
            </div>
            <div>
              <dt>快照编号</dt>
              <dd class="evaluation-code">{{ runDetail.run.dataset_snapshot_id || '—' }}</dd>
            </div>
            <div>
              <dt>快照 bundle sha256</dt>
              <dd class="evaluation-code">
                {{ runDetail.dataset_snapshot?.bundle_sha256 || '—' }}
              </dd>
            </div>
            <div>
              <dt>数据集摘要</dt>
              <dd class="evaluation-code">{{ runDetail.run.dataset_sha256 }}</dd>
            </div>
            <div>
              <dt>判定器摘要</dt>
              <dd class="evaluation-code">{{ runDetail.run.judges_sha256 }}</dd>
            </div>
            <div>
              <dt>发起人账号</dt>
              <dd class="evaluation-code">{{ runDetail.run.owner_id }}</dd>
            </div>
          </dl>
        </el-collapse-item>
      </el-collapse>
      <h4>逐例结果</h4>
      <el-alert
        v-if="runCaseError"
        type="error"
        show-icon
        :title="runCaseError"
        :closable="false"
      />
      <el-table
        v-else
        v-loading="runCaseBusy"
        :data="runCaseRows"
        row-key="case_id"
        size="small"
        empty-text="该批次没有用例结果"
      >
        <el-table-column label="用例" min-width="230">
          <template #default="{ row }">
            <button class="evaluation-link" type="button" @click="openCaseJson(row.case_id)">
              {{ row.name }}
            </button>
            <small class="evaluation-meta evaluation-case-id" :title="row.case_id">{{
              row.case_id
            }}</small>
          </template>
        </el-table-column>
        <el-table-column label="分组" width="100">
          <template #default="{ row }">{{
            evaluationSuites[row.suite] || row.suite || '—'
          }}</template>
        </el-table-column>
        <el-table-column label="得分" width="70">
          <template #default="{ row }">{{
            typeof row.score === 'number' ? row.score.toFixed(2) : '—'
          }}</template>
        </el-table-column>
        <el-table-column label="判定" width="110">
          <template #default="{ row }">
            <el-tag :type="evaluationTags[row.status as EvaluationStatus]" size="small">{{
              evaluationStatuses[row.status as EvaluationStatus]
            }}</el-tag>
            <small v-if="row.hard_failure" class="evaluation-meta">硬性违规</small>
          </template>
        </el-table-column>
        <el-table-column label="操作" width="86" fixed="right">
          <template #default="{ row }">
            <el-button
              link
              type="primary"
              class="evaluation-json-btn"
              @click="openCaseJson(row.case_id)"
            >
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8">
                <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z" />
                <path d="M14 2v6h6" />
              </svg>
              JSON
            </el-button>
          </template>
        </el-table-column>
      </el-table>
      <nav class="evaluation-pager" aria-label="批次用例分页">
        <span>第 {{ runCasePage }} 页</span>
        <el-button :disabled="runCaseBusy || runCasePage === 1" @click="turnRunCase(false)"
          >上一页</el-button
        >
        <el-button :disabled="runCaseBusy || !runCaseNext" @click="turnRunCase(true)"
          >下一页</el-button
        >
      </nav>
    </template>
  </el-drawer>

  <el-dialog
    v-model="caseJsonDialog"
    title="用例原始判定 JSON"
    width="min(900px, 94vw)"
    :z-index="3000"
  >
    <p class="evaluation-meta">
      用例 {{ caseJsonCaseId }} · 以下为服务端返回的 expected / observed / verdicts
      原始字段，未裁剪； 末尾附该批次运行时固化的数据集原始用例。
    </p>
    <el-button
      v-if="caseJsonTrace"
      type="primary"
      plain
      class="evaluation-case-trace"
      @click="inspectCaseTrace"
      >查看关联执行轨迹</el-button
    >
    <pre class="evaluation-json">{{ caseJsonText }}</pre>
    <h4>数据集原始用例</h4>
    <p class="evaluation-meta">
      用例摘要 sha256 {{ caseJsonDatasetSha256 || '—' }} ·
      以下为该次运行数据集快照中保存的完整原始用例，未裁剪、未改名。
    </p>
    <pre class="evaluation-json">{{ caseJsonDatasetText }}</pre>
  </el-dialog>

  <el-drawer v-model="snapshotDrawer" title="数据集快照原始用例" size="72%">
    <el-alert
      v-if="snapshotError"
      type="error"
      show-icon
      :title="snapshotError"
      :closable="false"
    />
    <template v-else>
      <div v-if="snapshotMeta" class="evaluation-filters">
        <el-tag effect="plain" size="small">版本 {{ snapshotMeta.dataset_version }}</el-tag>
        <el-tag type="info" effect="plain" size="small"
          >bundle {{ shortSha(snapshotMeta.bundle_sha256) }}</el-tag
        >
        <el-tag type="success" effect="plain" size="small"
          >用例 {{ snapshotMeta.case_count }}</el-tag
        >
        <span class="evaluation-meta">快照 {{ snapshotMeta.snapshot_id }}</span>
        <el-button :loading="snapshotBusy" @click="loadSnapshotCases">刷新</el-button>
      </div>
      <p class="evaluation-meta">
        以下为批次 <code>{{ snapshotRunId }}</code>
        运行时固化的数据集原始用例（按套件与序号排序）；每条可查看完整 case_payload
        JSON，不读取当前数据集。
      </p>
      <el-table
        v-loading="snapshotBusy"
        :data="snapshotCases"
        row-key="case_id"
        max-height="520"
        empty-text="该快照没有登记原始用例"
      >
        <el-table-column label="套件" width="150">
          <template #default="{ row }">{{
            evaluationSuites[row.suite] || row.suite || '—'
          }}</template>
        </el-table-column>
        <el-table-column label="用例 ID" width="170" prop="case_id" />
        <el-table-column label="序号" width="80" prop="ordinal" />
        <el-table-column label="用例摘要" width="150">
          <template #default="{ row }">{{ shortSha(row.case_sha256) }}</template>
        </el-table-column>
        <el-table-column label="操作" width="86" fixed="right">
          <template #default="{ row }">
            <el-button
              link
              type="primary"
              class="evaluation-json-btn"
              @click="openSnapshotCaseJson(row)"
            >
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8">
                <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z" />
                <path d="M14 2v6h6" />
              </svg>
              JSON
            </el-button>
          </template>
        </el-table-column>
      </el-table>
      <nav class="evaluation-pager" aria-label="数据集快照用例分页">
        <span>第 {{ snapshotPage }} 页 / 共 {{ snapshotTotal }} 条</span>
        <el-button :disabled="snapshotBusy || snapshotPage === 1" @click="turnSnapshotCase(false)"
          >上一页</el-button
        >
        <el-button :disabled="snapshotBusy || !snapshotNext" @click="turnSnapshotCase(true)"
          >下一页</el-button
        >
      </nav>
    </template>
  </el-drawer>

  <el-dialog
    v-model="rawCaseDialog"
    :title="rawCaseTitle ? `原始用例 JSON：${rawCaseTitle}` : '原始用例 JSON'"
    width="min(880px, 94vw)"
  >
    <p class="evaluation-meta">以下为该批次快照中保存的完整原始用例字段，未裁剪、未改名。</p>
    <pre class="evaluation-json">{{ rawCaseJson }}</pre>
  </el-dialog>
</template>

<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import type { ApiClient } from '../../shared/apiTypes'
import {
  evaluationModeLabel,
  evaluationScopeLabel,
  evaluationStatuses,
  evaluationSuites,
  evaluationTags,
  formatMetricValue,
  type EvaluationMetricSchema,
  type EvaluationRun,
  type EvaluationStatus,
} from './evaluationTypes'
import {
  downloadRunReport,
  metricTooltip,
  selectCoreMetrics,
  shortMetricLabel,
  shortSha,
  type RunMetricCard,
} from './evaluationHelpers'
import { useRunDetail } from './useRunDetail'
import { traceTime } from '../../shared/labels'

const props = defineProps<{
  api: ApiClient
  view: 'launch' | 'results'
  canWriteEval: boolean
  schema: EvaluationMetricSchema | null
  focusRunId?: string | null
}>()
const emit = defineEmits<{
  (e: 'inspect', taskId: string, traceId: string): void
  (e: 'setBaseline', run: EvaluationRun): void
  (e: 'rerun', run: EvaluationRun): void
  (e: 'focusConsumed'): void
}>()

const {
  runDetailOpen,
  runDetail,
  runDetailBusy,
  runDetailError,
  runCaseRows,
  runCaseNext,
  runCaseBusy,
  runCaseError,
  runCasePage,
  caseJsonDialog,
  caseJsonText,
  caseJsonCaseId,
  caseJsonTrace,
  caseJsonDatasetText,
  caseJsonDatasetSha256,
  snapshotDrawer,
  snapshotRunId,
  snapshotMeta,
  snapshotCases,
  snapshotNext,
  snapshotTotal,
  snapshotBusy,
  snapshotError,
  snapshotPage,
  rawCaseDialog,
  rawCaseJson,
  rawCaseTitle,
  openRunDetail,
  openSnapshotCases,
  loadSnapshotCases,
  turnSnapshotCase,
  openSnapshotCaseJson,
  openCaseJson,
  turnRunCase,
} = useRunDetail(props.api)

const showAllDetailMetrics = ref(false)
const detailTechOpen = ref<string[]>([])

const runMetricCards = computed<RunMetricCard[]>(() => {
  const detailMetrics = runDetail.value?.metric_schema?.metrics ?? props.schema?.metrics ?? []
  return detailMetrics.map((spec) => ({
    spec,
    value: runDetail.value?.run.summary?.[spec.key] ?? null,
  }))
})
const detailCoreCards = computed<RunMetricCard[]>(() => {
  const core = selectCoreMetrics(runMetricCards.value.map((card) => card.spec))
  const keys = new Set(core.map((spec) => spec.key))
  return runMetricCards.value.filter((card) => keys.has(card.spec.key))
})
const detailMetricCards = computed<RunMetricCard[]>(() =>
  showAllDetailMetrics.value ? runMetricCards.value : detailCoreCards.value,
)

/** 关闭用例 JSON 弹窗并跳转关联执行轨迹。 */
function inspectCaseTrace(): void {
  const link = caseJsonTrace.value
  if (!link) return
  caseJsonDialog.value = false
  emit('inspect', link.task_id, link.trace_id)
}

/**
 * 打开批次详情抽屉，并重置指标展开与技术信息折叠状态。
 * @param runId - 批次编号。
 */
async function openRun(runId: string): Promise<void> {
  showAllDetailMetrics.value = false
  detailTechOpen.value = []
  await openRunDetail(runId)
}

watch(
  () => props.focusRunId,
  (runId) => {
    if (!runId) return
    void openRun(runId)
    emit('focusConsumed')
  },
  { immediate: true, flush: 'post' },
)

defineExpose({ openRun })
</script>
