<template>
  <section class="panel evaluation-center">
    <div class="section-heading evaluation-section-head">
      <h3>{{ centerTitle }}</h3>
      <div class="evaluation-head-actions">
        <el-button :loading="refreshing" @click="refreshAll">刷新</el-button>
      </div>
    </div>

    <div class="evaluation-controls">
      <label class="evaluation-control-item">
        <span>数据集版本</span>
        <el-select
          v-model="datasetVersion"
          placeholder="暂无可选版本"
          :disabled="!datasetVersions.length"
          aria-label="选择数据集版本"
        >
          <el-option
            v-for="version in datasetVersions"
            :key="version"
            :value="version"
            :label="version"
          />
        </el-select>
      </label>
      <label class="evaluation-control-item">
        <span>评测口径</span>
        <el-select v-model="scope" aria-label="选择评测口径">
          <el-option
            v-for="item in scopeOptions"
            :key="item.value"
            :value="item.value"
            :label="item.label"
          />
        </el-select>
      </label>
      <label class="evaluation-control-item">
        <span>执行方式</span>
        <el-select v-model="mode" aria-label="选择执行方式">
          <el-option
            v-for="item in allowedModes"
            :key="item"
            :value="item"
            :label="evaluationModeLabel(item)"
          />
        </el-select>
      </label>
    </div>
    <el-alert
      v-if="catalogError"
      type="error"
      show-icon
      :title="catalogError"
      :closable="false"
      class="evaluation-catalog-alert"
    />

    <el-tabs v-model="activeTab" class="evaluation-tabs">
      <el-tab-pane v-if="view === 'results'" label="概览" name="overview">
        <EvaluationOverviewPanel
          :trends="trends"
          :trends-error="trendsError"
          :metric-cards="metricCards"
          :core-metric-keys="coreMetricKeys"
          :metrics-count="metrics.length"
          :core-metric-count="coreMetrics.length"
          :table-metrics="tableMetrics"
          :aggregate-spec="aggregateMetric"
          :aggregate-stats="aggregateStats"
          :aggregate-label="aggregateLabel"
          :eval-kind-label="evalKindLabel"
          :dataset-version="datasetVersion"
          :scope="scope"
          :mode="mode"
          @open-run="openRunDetail"
          @launch="emit('launch')"
        />
      </el-tab-pane>

      <el-tab-pane v-if="view === 'results'" label="批次" name="runs">
        <div class="evaluation-tab">
          <div class="evaluation-subsection-head">
            <h4>
              评测批次
              <el-tooltip
                content="指标列来自指标目录；同一数据集、口径与执行方式再次运行会保留原批次并递增运行次数。默认只显示核心指标，可切换到全部指标。"
                placement="top"
              >
                <span class="evaluation-help">?</span>
              </el-tooltip>
            </h4>
            <div class="evaluation-filters evaluation-filters-inline">
              <TenantOwnerSelect v-if="isAdmin" v-model="owner" :api="api" />
              <el-select
                v-model="runDatasetId"
                clearable
                placeholder="全部数据集"
                aria-label="按数据集筛选批次"
              >
                <el-option value="" label="全部数据集" />
                <el-option
                  v-for="dataset in kindDatasets"
                  :key="dataset.dataset_id"
                  :value="dataset.dataset_id"
                  :label="`${dataset.name}（${dataset.version}）`"
                />
              </el-select>
              <el-switch v-model="showAllRunMetrics" active-text="显示全部指标" />
              <el-button :loading="runsBusy" @click="refreshRuns">刷新</el-button>
            </div>
          </div>
          <EvaluationRunPanel
            :runs="runs"
            :runs-busy="runsBusy"
            :runs-error="runsError"
            :runs-next="runsNext"
            :runs-page-no="runsPageNo"
            :kind-dataset-count="kindDatasets.length"
            :metrics-count="metrics.length"
            :table-metrics="tableMetrics"
            :can-write-eval="canWriteEval"
            @turn="turnRuns"
            @open-run="openRunDetail"
            @download-report="downloadRunReport"
            @rerun="openRerunDialog"
          />
        </div>
      </el-tab-pane>

      <el-tab-pane v-if="view === 'results'" label="基线" name="baselines">
        <div class="evaluation-tab">
          <div class="evaluation-subsection-head">
            <h4>
              评测基线
              <el-tooltip
                content="基线按「数据集版本 + 口径 + 执行方式」唯一：同一组合只保留一个 active 基线，概览的环比使用 active 基线。"
                placement="top"
              >
                <span class="evaluation-help">?</span>
              </el-tooltip>
            </h4>
            <div class="evaluation-filters evaluation-filters-inline">
              <el-switch v-model="baselineActiveOnly" active-text="只看 active" />
              <el-button :loading="baselinesBusy" @click="loadBaselines">刷新</el-button>
            </div>
          </div>
          <EvaluationBaselinePanel
            :baselines="baselines"
            :baselines-busy="baselinesBusy"
            :baselines-error="baselinesError"
            :baseline-metric-label="baselineMetricLabel"
            @open-run="openRunDetail"
          />
        </div>
      </el-tab-pane>

      <el-tab-pane v-if="view === 'launch'" label="发起评测" name="launch">
        <div class="evaluation-tab">
          <div class="evaluation-subsection-head">
            <h4>
              发起真实评测
              <el-tooltip
                content="评测由 Worker 真实执行；登记算法版本并选择数据集、口径与执行方式后触发，页面只展示任务号与当前进度，结果请到「测评结果」查看。"
                placement="top"
              >
                <span class="evaluation-help">?</span>
              </el-tooltip>
            </h4>
          </div>
          <el-alert
            v-if="!canWriteEval"
            type="info"
            show-icon
            :closable="false"
            title="当前账号可查看评测任务进度，触发/取消评测与登记算法版本需要企业负责人或系统管理员权限。"
          />
          <el-alert v-if="jobError" type="error" show-icon :title="jobError" :closable="false" />
          <p v-if="profilesError" class="evaluation-meta">{{ profilesError }}</p>
          <div v-if="!activeJob" class="evaluation-launch-card">
            <div class="evaluation-launch-copy">
              <strong>{{ evalKindLabel }} 评测</strong>
              <p class="evaluation-meta">
                登记不可变算法版本，选择数据集、口径与真实执行方式后由 Worker 执行。
              </p>
            </div>
            <div v-if="canWriteEval" class="evaluation-launch-actions">
              <el-button @click="openProfileDialog">登记算法版本</el-button>
              <el-button type="primary" @click="openTriggerDialog()">发起评测</el-button>
            </div>
          </div>
          <template v-else>
            <div class="evaluation-job-head">
              <strong>{{ activeJob.job_id }}</strong>
              <el-tag :type="stateTag(activeJob.state)" size="small">{{
                stateText(activeJob.state)
              }}</el-tag>
              <span class="evaluation-meta">{{ activeJob.stage || '等待 Worker 调度' }}</span>
              <el-button v-if="canWriteEval && !jobTerminal" :loading="jobBusy" @click="cancelJob"
                >取消任务</el-button
              >
            </div>
            <el-progress :percentage="activeJob.progress" :status="jobProgressStatus" />
            <p class="evaluation-meta">
              创建 {{ traceTime(activeJob.created_at)
              }}<template v-if="activeJob.started_at">
                · 开始 {{ traceTime(activeJob.started_at) }}</template
              >
              <template v-if="activeJob.finished_at">
                · 结束 {{ traceTime(activeJob.finished_at) }}</template
              >
            </p>
            <el-alert
              v-if="activeJob.error"
              type="error"
              :title="activeJob.error"
              :closable="false"
            />
            <p v-if="jobRunId" class="evaluation-meta">
              评测批次已入库：
              <button class="evaluation-link" type="button" @click="openRunDetail(jobRunId)">
                {{ jobRunId }}
              </button>
            </p>
          </template>
        </div>
      </el-tab-pane>

      <el-tab-pane v-if="view === 'results' && canWriteEval" label="审计" name="audit">
        <div class="evaluation-tab">
          <div class="evaluation-subsection-head">
            <h4>评测审计留痕</h4>
            <div class="evaluation-filters evaluation-filters-inline">
              <el-select v-model="auditAction" placeholder="全部动作" aria-label="按审计动作筛选">
                <el-option value="" label="全部动作" />
                <el-option
                  v-for="(label, code) in evaluationAuditActions"
                  :key="code"
                  :value="code"
                  :label="label"
                />
              </el-select>
              <el-button :loading="auditBusy" @click="loadAuditLogs">刷新</el-button>
            </div>
          </div>
          <EvaluationAuditPanel
            :audit-logs="auditLogs"
            :audit-busy="auditBusy"
            :audit-error="auditError"
            :audit-next="auditNext"
            :audit-page="auditPage"
            @turn="turnAudit"
          />
        </div>
      </el-tab-pane>
    </el-tabs>

    <EvaluationRunDetailDrawer
      ref="runDetailDrawerRef"
      :api="api"
      :view="view"
      :can-write-eval="canWriteEval"
      :schema="schema"
      :focus-run-id="focusRunId"
      @inspect="handleInspect"
      @set-baseline="openBaselineDialog"
      @rerun="openRerunDialog"
      @focus-consumed="emit('focus-consumed')"
    />

    <EvaluationTriggerDialog
      ref="triggerDialogRef"
      :api="api"
      :eval-kind="kind"
      :scope="scope"
      :mode="mode"
      :run-dataset-id="runDatasetId"
      :profiles="profiles"
      :trigger-datasets="triggerDatasets"
      :scope-options="scopeOptions"
      @profiles-changed="handleProfilesChanged"
      @triggered="handleTriggered"
      @baseline-set="handleBaselineSet"
    />
  </section>
</template>

<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import type { ApiClient } from '../../shared/apiTypes'
import { stateTag, stateText, traceTime } from '../../shared/labels'
import {
  evaluationAuditActions,
  evaluationModeLabel,
  type EvaluationDataset,
  type EvaluationJob,
  type EvaluationJobState,
  type EvaluationKind,
  type EvaluationMetricSchema,
  type EvaluationMetricSpec,
  type EvaluationRun,
  type EvaluationTriggerReceipt,
} from './evaluationTypes'
import { downloadRunReport, selectCoreMetrics } from './evaluationHelpers'
import { useEvaluationCatalog } from './useEvaluationCatalog'
import { useEvaluationTrends } from './useEvaluationTrends'
import { useEvaluationBaselines } from './useEvaluationBaselines'
import { useEvaluationRuns } from './useEvaluationRuns'
import { useEvaluationAudit } from './useEvaluationAudit'
import EvaluationOverviewPanel from './EvaluationOverviewPanel.vue'
import EvaluationRunPanel from './EvaluationRunPanel.vue'
import EvaluationBaselinePanel from './EvaluationBaselinePanel.vue'
import EvaluationRunDetailDrawer from './EvaluationRunDetailDrawer.vue'
import EvaluationTriggerDialog from './EvaluationTriggerDialog.vue'
import EvaluationAuditPanel from './EvaluationAuditPanel.vue'
import TenantOwnerSelect from './TenantOwnerSelect.vue'
import './evaluations.css'

const JOB_POLL_MS = 3000
const JOB_TERMINAL_STATES = new Set<EvaluationJobState>([
  'completed',
  'cancelled',
  'failed',
  'rejected',
  'legacy',
])

const props = defineProps<{
  isAdmin: boolean
  canWriteEval: boolean
  api: ApiClient
  view: 'launch' | 'results'
  kind: EvaluationKind
  focusRunId?: string | null
}>()
const emit = defineEmits<{
  (e: 'inspect', taskId: string, traceId: string): void
  (e: 'launch'): void
  (e: 'focus-consumed'): void
}>()

const evalKind = computed<EvaluationKind>(() => props.kind)
const evalKindLabel = computed(() => (props.kind === 'agent' ? 'Agent' : 'RAG'))
const centerTitle = computed(() => `${evalKindLabel.value} 评测`)
const isAdminRef = computed(() => props.isAdmin)
const canWriteRef = computed(() => props.canWriteEval)

const activeTab = ref(props.view === 'launch' ? 'launch' : 'overview')
const owner = ref('all')
const refreshing = ref(false)
const showAllRunMetrics = ref(false)
const runDatasetId = ref('')
const baselineActiveOnly = ref(true)

const {
  catalog,
  catalogError,
  profiles,
  profilesError,
  scope,
  mode,
  datasetVersion,
  datasetVersions,
  kindDatasets,
  allowedModes,
  scopeOptions,
  triggerDatasets,
  loadCatalog,
  loadDatasets,
  loadProfiles,
  ensureSelections,
} = useEvaluationCatalog(props.api, evalKind)

const schema = computed<EvaluationMetricSchema | null>(() => {
  const fromCatalog = catalog.value?.kinds[evalKind.value]?.scopes[scope.value]
  if (fromCatalog) return fromCatalog
  const fromTrends = trends.value?.metric_schema
  if (fromTrends && fromTrends.eval_kind === evalKind.value && fromTrends.scope === scope.value) {
    return fromTrends
  }
  return null
})
const metrics = computed<EvaluationMetricSpec[]>(() => schema.value?.metrics ?? [])
const coreMetrics = computed<EvaluationMetricSpec[]>(() => selectCoreMetrics(metrics.value))
const coreMetricKeys = computed(() => new Set(coreMetrics.value.map((spec) => spec.key)))
const tableMetrics = computed<EvaluationMetricSpec[]>(() =>
  showAllRunMetrics.value ? metrics.value : coreMetrics.value.slice(0, 3),
)
const runPassMetricKey = computed(() => {
  if (scope.value === 'adversarial' || scope.value === 'quick') return 'adversarial.pass_rate'
  if (scope.value === 'full' || scope.value === 'behavior') return 'behavior.pass_rate'
  return 'task_completion'
})
const baselineMetricLabel = computed(() => {
  const key = runPassMetricKey.value
  return metrics.value.find((item) => item.key === key)?.label ?? '通过率'
})

const {
  trends,
  trendsError,
  metricCards,
  aggregateMetric,
  aggregateStats,
  aggregateLabel,
  loadTrends,
} = useEvaluationTrends(props.api, { evalKind, scope, mode, datasetVersion, metrics })

const { baselines, baselinesBusy, baselinesError, loadBaselines } = useEvaluationBaselines(
  props.api,
  evalKind,
  datasetVersion,
  baselineActiveOnly,
)

const {
  runs,
  runsNext,
  runsBusy,
  runsError,
  runsPageNo,
  runEndpoint,
  refreshRuns,
  resetRuns,
  turnRuns,
} = useEvaluationRuns(props.api, {
  evalKind,
  scope,
  mode,
  isAdmin: isAdminRef,
  owner,
  runDatasetId,
})

const {
  auditAction,
  auditLogs,
  auditNext,
  auditBusy,
  auditError,
  auditPage,
  loadAuditLogs,
  resetAudit,
  turnAudit,
} = useEvaluationAudit(props.api, canWriteRef)

const activeJob = ref<EvaluationJob | null>(null)
const jobError = ref('')
const jobBusy = ref(false)
const runDetailDrawerRef = ref<InstanceType<typeof EvaluationRunDetailDrawer> | null>(null)
const triggerDialogRef = ref<InstanceType<typeof EvaluationTriggerDialog> | null>(null)

let jobTimer: number | null = null
let polledJobId = ''

const jobTerminal = computed(
  () => activeJob.value !== null && JOB_TERMINAL_STATES.has(activeJob.value.state),
)
const jobProgressStatus = computed<'success' | 'exception' | undefined>(() => {
  if (!activeJob.value) return undefined
  if (activeJob.value.state === 'completed') return 'success'
  if (JOB_TERMINAL_STATES.has(activeJob.value.state)) return 'exception'
  return undefined
})
const jobRunId = computed(() => {
  const result = activeJob.value?.result
  return result && typeof result.run_id === 'string' ? result.run_id : ''
})

/**
 * 按当前页签装载其专属数据，保持各页签互不阻塞。
 * @param tab - 当前激活的页签名称。
 */
async function ensureTabData(tab: string): Promise<void> {
  if (tab === 'datasets') await loadDatasets()
  else if (tab === 'baselines') await loadBaselines()
  else if (tab === 'launch') await loadProfiles()
  else if (tab === 'audit') await loadAuditLogs()
  else if (tab === 'runs') await refreshRuns()
  else if (tab === 'overview') await loadTrends()
}

/**
 * 同时刷新当前页面职责范围内的数据与顶部控制区所需的目录。
 */
async function refreshAll(): Promise<void> {
  refreshing.value = true
  try {
    const requests: Promise<void>[] = [loadDatasets(), loadProfiles()]
    if (props.view === 'results') {
      requests.push(loadTrends(), refreshRuns(), loadBaselines())
    }
    await Promise.all(requests)
    if (props.view === 'results' && activeTab.value === 'audit' && props.canWriteEval) {
      await loadAuditLogs()
    }
  } finally {
    refreshing.value = false
  }
}

/**
 * 打开批次详情抽屉并装载指标 schema、逐例结果。
 * @param runId - 批次编号。
 */
function openRunDetail(runId: string): void {
  void runDetailDrawerRef.value?.openRun(runId)
}

/**
 * 打开算法版本登记弹窗。
 */
function openProfileDialog(): void {
  triggerDialogRef.value?.openProfile()
}

/**
 * 打开受控评测发起弹窗，并按所选数据集登记能力预选口径与执行方式。
 * @param dataset - 从数据集页直接发起时预选的数据集。
 */
function openTriggerDialog(dataset?: EvaluationDataset): void {
  triggerDialogRef.value?.openTrigger(dataset)
  void loadProfiles()
}

/**
 * 打开复跑弹窗，沿用来源批次的数据集、口径、执行方式与算法版本。
 * @param run - 要复跑的来源批次。
 */
function openRerunDialog(run: EvaluationRun): void {
  triggerDialogRef.value?.openRerun(run)
}

/**
 * 打开把批次设为基线的弹窗。
 * @param run - 待设为基线的批次。
 */
function openBaselineDialog(run: EvaluationRun): void {
  triggerDialogRef.value?.openBaseline(run)
}

/**
 * 把批次详情中的轨迹关联转交工作台打开执行轨迹。
 * @param taskId - 关联任务编号。
 * @param traceId - 关联轨迹编号。
 */
function handleInspect(taskId: string, traceId: string): void {
  emit('inspect', taskId, traceId)
}

/**
 * 算法版本登记或发起弹窗打开后重新读取算法版本列表。
 */
function handleProfilesChanged(): void {
  void loadProfiles()
}

/**
 * 处理评测或复跑提交成功：挂载任务进度并刷新目录与批次。
 * @param receipt - 触发接口返回的任务号与审计号。
 */
async function handleTriggered(receipt: EvaluationTriggerReceipt): Promise<void> {
  presentTriggeredJob(receipt)
  await Promise.all([loadDatasets(), loadTrends(), refreshRuns()])
}

/**
 * 基线设置成功后刷新基线、趋势与批次列表。
 */
async function handleBaselineSet(): Promise<void> {
  await Promise.all([loadBaselines(), loadTrends(), refreshRuns()])
}

/**
 * 把触发成功的任务挂到当前面板并开始轮询进度。
 * @param receipt - 触发接口返回的任务号与审计号。
 */
function presentTriggeredJob(receipt: EvaluationTriggerReceipt): void {
  activeJob.value = {
    job_id: receipt.job_id,
    state: receipt.status,
    stage: '已提交，等待 Worker 执行',
    progress: 0,
    result: null,
    error: '',
    created_at: new Date().toISOString(),
    started_at: null,
    finished_at: null,
  }
  startJobPolling(receipt.job_id)
}

/**
 * 停止评测任务轮询。
 */
function stopJobPolling(): void {
  if (jobTimer !== null) {
    window.clearInterval(jobTimer)
    jobTimer = null
  }
  polledJobId = ''
}

/**
 * 读取评测任务最新状态；进入终态后停止轮询并刷新数据。
 * @param jobId - 评测任务编号。
 */
async function pollJob(jobId: string): Promise<void> {
  try {
    const job = await props.api<EvaluationJob>('/evaluations/jobs/' + encodeURIComponent(jobId))
    if (polledJobId !== jobId) return
    const wasTerminal = activeJob.value !== null && JOB_TERMINAL_STATES.has(activeJob.value.state)
    activeJob.value = job
    jobError.value = ''
    if (JOB_TERMINAL_STATES.has(job.state)) {
      stopJobPolling()
      if (!wasTerminal) await finishJob(job)
    }
  } catch (cause) {
    if (polledJobId !== jobId) return
    jobError.value = cause instanceof Error ? cause.message : '读取评测任务失败'
    stopJobPolling()
  }
}

/**
 * 处理评测任务终态：提示结果并刷新评测目录与批次。
 * @param job - 已结束的评测任务。
 */
async function finishJob(job: EvaluationJob): Promise<void> {
  if (job.state === 'completed') {
    const result = job.result
    const runId = result && typeof result.run_id === 'string' ? result.run_id : ''
    ElMessage.success(runId ? `评测完成，批次 ${runId} 已入库` : '评测任务已完成')
    await Promise.all([loadDatasets(), loadTrends(), loadBaselines(), refreshRuns()])
    return
  }
  if (job.state === 'cancelled') {
    ElMessage.warning('评测任务已取消')
    return
  }
  ElMessage.error(job.error || '评测任务执行失败')
}

/**
 * 立即刷新任务并按固定节奏轮询直到终态。
 * @param jobId - 评测任务编号。
 */
function startJobPolling(jobId: string): void {
  stopJobPolling()
  polledJobId = jobId
  void pollJob(jobId)
  jobTimer = window.setInterval(() => {
    void pollJob(jobId)
  }, JOB_POLL_MS)
}

/**
 * 请求取消排队或执行中的评测任务。
 */
async function cancelJob(): Promise<void> {
  const job = activeJob.value
  if (!job) return
  jobBusy.value = true
  jobError.value = ''
  try {
    await props.api<{ message: string }>(
      '/evaluations/jobs/' + encodeURIComponent(job.job_id) + '/cancel',
      'POST',
    )
    ElMessage.success('已请求取消评测任务')
  } catch (cause) {
    jobError.value = cause instanceof Error ? cause.message : '取消评测任务失败'
  } finally {
    jobBusy.value = false
  }
}

watch(scope, () => {
  if (props.view === 'results') void loadTrends()
})
watch(mode, () => {
  if (props.view === 'results') {
    void loadTrends()
    void loadBaselines()
  }
})
watch(datasetVersion, () => {
  if (props.view === 'results') {
    void loadTrends()
    void loadBaselines()
  }
  void loadProfiles()
})
watch(runEndpoint, () => {
  if (props.view === 'results') resetRuns()
})
watch(baselineActiveOnly, () => {
  if (props.view === 'results') void loadBaselines()
})
watch(auditAction, () => {
  if (props.view === 'results') resetAudit()
})
watch(activeTab, (tab) => {
  void ensureTabData(tab)
})
onMounted(async () => {
  await loadCatalog()
  await loadDatasets()
  ensureSelections()
  if (props.view === 'results') await loadTrends()
  else await loadProfiles()
})
onBeforeUnmount(stopJobPolling)
</script>
