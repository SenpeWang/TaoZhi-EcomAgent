<template>
  <section class="panel evaluation-dataset-page">
    <div class="section-heading evaluation-section-head">
      <div>
        <h3>测评数据集</h3>
        <p class="evaluation-meta">
          查询 {{ kindMeta.title }} 的原始数据集构成，以及每个批次实际使用的数据集版本与用例规模。
        </p>
      </div>
      <div class="evaluation-head-actions">
        <el-switch v-model="showArchived" active-text="显示归档版本" />
        <el-button :loading="datasetsBusy" @click="loadDatasets">刷新</el-button>
      </div>
    </div>

    <el-alert
      v-if="datasetsError"
      type="error"
      show-icon
      :title="datasetsError"
      :closable="false"
    />

    <div v-else class="evaluation-dataset-layout">
      <aside class="evaluation-dataset-sidebar" aria-label="数据集版本列表">
        <el-skeleton v-if="datasetsBusy && !groups.length" :rows="4" animated />
        <div v-else-if="!groups.length" class="evaluation-empty-card">
          <strong>当前体系还没有登记数据集</strong>
          <p class="evaluation-meta">先到「测评启动」登记数据集版本并完成一次真实评测。</p>
        </div>
        <button
          v-for="group in groups"
          v-else
          :key="group.version"
          type="button"
          class="evaluation-dataset-card"
          :class="{ 'evaluation-dataset-card-active': group.version === selectedVersion }"
          @click="selectVersion(group.version)"
        >
          <span class="evaluation-dataset-card-title">{{ group.version }}</span>
          <span class="evaluation-dataset-card-tags">
            <el-tag
              v-for="suite in group.suites"
              :key="suite.dataset_id"
              size="small"
              effect="plain"
            >
              {{ suiteLabel(suite) }}
            </el-tag>
            <el-tag v-if="group.archived" type="info" size="small">归档</el-tag>
          </span>
          <span class="evaluation-dataset-card-meta">
            用例 {{ group.caseCount }} · 批次 {{ group.batchCount }}
          </span>
          <span class="evaluation-dataset-card-meta">
            最近使用 {{ group.lastRunAt ? traceTime(group.lastRunAt) : '尚未使用' }}
          </span>
        </button>
      </aside>

      <main v-if="selectedGroup" class="evaluation-dataset-detail">
        <div class="evaluation-dataset-detail-head">
          <div>
            <h4>{{ selectedGroup.version }}</h4>
            <p class="evaluation-meta">
              {{ kindMeta.title }} · {{ selectedGroup.suites.length }} 个套件 · 共
              {{ selectedGroup.caseCount }} 例
            </p>
          </div>
          <el-button link type="primary" :loading="rawBusy" @click="loadRaw"
            >刷新原始构成</el-button
          >
        </div>

        <el-tabs v-model="detailTab" class="evaluation-dataset-tabs">
          <el-tab-pane label="原始构成" name="composition">
            <div class="evaluation-dataset-suite-bar">
              <el-radio-group v-model="selectedDatasetId" size="small">
                <el-radio-button
                  v-for="suite in selectedGroup.suites"
                  :key="suite.dataset_id"
                  :value="suite.dataset_id"
                >
                  {{ suiteLabel(suite) }}（{{ suite.case_count }}）
                </el-radio-button>
              </el-radio-group>
              <span class="evaluation-meta">
                内容 sha256
                <code class="evaluation-code">{{ shortSha(selectedDataset?.content_sha256) }}</code>
              </span>
            </div>

            <el-alert v-if="rawError" type="error" show-icon :title="rawError" :closable="false" />
            <el-skeleton v-else-if="rawBusy && !raw" :rows="5" animated />

            <template v-else-if="raw">
              <div class="evaluation-dataset-summary">
                <div class="evaluation-dataset-stat">
                  <span>用例</span>
                  <strong>{{ raw.cases.length }}</strong>
                </div>
                <div class="evaluation-dataset-stat">
                  <span>能力大类</span>
                  <strong>{{ composition.capability.length }}</strong>
                </div>
                <div class="evaluation-dataset-stat">
                  <span>子类</span>
                  <strong>{{ composition.category.length }}</strong>
                </div>
                <div class="evaluation-dataset-stat">
                  <span>用途</span>
                  <strong>{{ composition.track.length }}</strong>
                </div>
                <div class="evaluation-dataset-stat">
                  <span>文件</span>
                  <strong>{{ datasetFiles.length }}</strong>
                </div>
              </div>

              <div class="evaluation-dataset-distributions">
                <section
                  v-for="section in compositionSections"
                  :key="section.key"
                  class="evaluation-dataset-distribution"
                >
                  <h5>{{ section.label }}</h5>
                  <div v-if="section.items.length" class="evaluation-dataset-bars">
                    <div
                      v-for="item in section.items"
                      :key="item.label"
                      class="evaluation-dataset-bar-row"
                    >
                      <span class="evaluation-dataset-bar-label" :title="item.label">{{
                        item.label
                      }}</span>
                      <span class="evaluation-dataset-bar-track">
                        <span
                          class="evaluation-dataset-bar-fill"
                          :style="{ width: `${item.ratio * 100}%` }"
                        />
                      </span>
                      <span class="evaluation-dataset-bar-count">{{ item.count }}</span>
                    </div>
                  </div>
                  <p v-else class="evaluation-muted">该版本未登记此维度字段。</p>
                </section>
              </div>

              <div class="evaluation-dataset-actions">
                <el-button :loading="dsRawBusy" @click="downloadRaw"
                  >下载当前套件原始用例</el-button
                >
                <el-button :disabled="!raw.cases.length" @click="openRawCase(raw.cases[0])"
                  >查看首条用例 JSON</el-button
                >
              </div>

              <h4>文件清单（{{ datasetFiles.length }}）</h4>
              <el-table
                v-if="datasetFiles.length"
                :data="datasetFiles"
                size="small"
                row-key="path"
                max-height="240"
              >
                <el-table-column label="仓库相对路径" min-width="240">
                  <template #default="{ row }">
                    <code>{{ row.path }}</code>
                  </template>
                </el-table-column>
                <el-table-column label="sha256" width="190">
                  <template #default="{ row }">
                    <span :title="row.sha256">{{ shortSha(row.sha256) }}</span>
                  </template>
                </el-table-column>
                <el-table-column label="操作" width="150">
                  <template #default="{ row }">
                    <el-button link type="primary" @click="viewFile(row.path)">查看</el-button>
                    <el-button link type="primary" @click="downloadFile(row.path)">下载</el-button>
                  </template>
                </el-table-column>
              </el-table>
              <p v-else class="evaluation-muted">该套件没有登记文件清单。</p>

              <div class="evaluation-subsection-head">
                <h4>原始用例（{{ raw.cases.length }}）</h4>
                <el-button link type="primary" @click="openRawCase(currentPageCases[0])"
                  >查看 JSON</el-button
                >
              </div>
              <el-table :data="currentPageCases" size="small" row-key="case_id" max-height="460">
                <el-table-column
                  v-for="column in caseColumns"
                  :key="column.key"
                  :label="column.label"
                  :min-width="column.width"
                >
                  <template #default="{ row }">
                    <span v-if="column.preview">{{ previewCase(row, column.keys) }}</span>
                    <span v-else>{{ textCase(row, column.keys) }}</span>
                  </template>
                </el-table-column>
                <el-table-column label="操作" width="80" fixed="right">
                  <template #default="{ row }">
                    <el-button link type="primary" @click="openRawCase(row)">JSON</el-button>
                  </template>
                </el-table-column>
              </el-table>
              <nav class="evaluation-pager" aria-label="原始用例分页">
                <span>第 {{ casePage + 1 }} / {{ casePageCount }} 页</span>
                <el-button :disabled="casePage === 0" @click="casePage -= 1">上一页</el-button>
                <el-button :disabled="casePage >= casePageCount - 1" @click="casePage += 1"
                  >下一页</el-button
                >
              </nav>
            </template>
            <div v-else class="evaluation-empty-card">
              <strong>该套件没有登记原始用例内容</strong>
              <p class="evaluation-meta">文件清单与下载仍可查看。</p>
            </div>
          </el-tab-pane>

          <el-tab-pane label="批次使用" name="usage">
            <div class="evaluation-dataset-suite-bar">
              <el-select
                v-model="usageDatasetId"
                placeholder="全部套件"
                aria-label="按数据集套件筛选批次"
                class="evaluation-dataset-usage-filter"
              >
                <el-option value="" label="全部套件" />
                <el-option
                  v-for="suite in selectedGroup.suites"
                  :key="suite.dataset_id"
                  :value="suite.dataset_id"
                  :label="`${suiteLabel(suite)}（${suite.case_count} 例）`"
                />
              </el-select>
              <span class="evaluation-meta">
                累计 {{ selectedGroup.batchCount }} 个批次 · 最近使用
                {{ selectedGroup.lastRunAt ? traceTime(selectedGroup.lastRunAt) : '尚未使用' }}
              </span>
            </div>

            <el-alert
              v-if="usageError"
              type="error"
              show-icon
              :title="usageError"
              :closable="false"
            />
            <el-table
              v-else
              v-loading="usageBusy"
              :data="usageRuns"
              row-key="run_id"
              size="small"
              empty-text="该版本尚未用于真实评测"
            >
              <el-table-column label="批次时间" min-width="150">
                <template #default="{ row }">{{ traceTime(row.created_at) }}</template>
              </el-table-column>
              <el-table-column label="数据集套件" min-width="140">
                <template #default="{ row }">{{ suiteLabelByRun(row) }}</template>
              </el-table-column>
              <el-table-column label="口径" width="90">
                <template #default="{ row }">{{ evaluationScopeLabel(row.scope) }}</template>
              </el-table-column>
              <el-table-column label="执行方式" min-width="110">
                <template #default="{ row }">{{ evaluationModeLabel(row.mode) }}</template>
              </el-table-column>
              <el-table-column label="算法版本" min-width="110">
                <template #default="{ row }">{{ row.profile_version_tag || '未绑定' }}</template>
              </el-table-column>
              <el-table-column label="用例（通过/总数）" width="145">
                <template #default="{ row }">
                  {{ row.passed_cases }} / {{ row.total_cases }}
                </template>
              </el-table-column>
              <el-table-column label="门禁" width="80">
                <template #default="{ row }">
                  <el-tag :type="row.gate_passed ? 'success' : 'danger'" size="small">
                    {{ row.gate_passed ? '通过' : '阻断' }}
                  </el-tag>
                </template>
              </el-table-column>
              <el-table-column label="操作" width="100" fixed="right">
                <template #default="{ row }">
                  <el-button link type="primary" @click="emit('open-run', row.run_id)"
                    >查看批次</el-button
                  >
                </template>
              </el-table-column>
            </el-table>
            <nav class="evaluation-pager" aria-label="批次使用分页">
              <span>第 {{ usagePage }} 页</span>
              <el-button :disabled="usageBusy || usagePage === 1" @click="turnUsage(false)"
                >上一页</el-button
              >
              <el-button :disabled="usageBusy || !usageNext" @click="turnUsage(true)"
                >下一页</el-button
              >
            </nav>
          </el-tab-pane>
        </el-tabs>
      </main>

      <main v-else class="evaluation-empty-card evaluation-dataset-detail">
        <strong>选择一个数据集版本</strong>
        <p class="evaluation-meta">左侧会按版本汇总套件、用例与批次使用情况。</p>
      </main>
    </div>

    <el-dialog
      v-model="rawCaseDialog"
      :title="rawCaseTitle ? `原始用例 JSON：${rawCaseTitle}` : '原始用例 JSON'"
      width="min(880px, 94vw)"
    >
      <p class="evaluation-meta">该用例为数据集中登记的完整原始字段，未裁剪、未改名。</p>
      <pre class="evaluation-json">{{ rawCaseJson }}</pre>
    </el-dialog>
  </section>
</template>

<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import type { ApiClient, Items } from '../../shared/apiTypes'
import { useCursorPage } from '../../shared/useCursorPage'
import { traceTime } from '../../shared/labels'
import {
  evaluationModeLabel,
  evaluationScopeLabel,
  evaluationSuites,
  type EvaluationDataset,
  type EvaluationKind,
  type EvaluationRun,
} from './evaluationTypes'
import { evaluationKindMeta } from './evaluationKindMeta'
import './evaluations.css'

const props = defineProps<{ api: ApiClient; kind: EvaluationKind }>()
const emit = defineEmits<{ (e: 'open-run', runId: string): void }>()

const CASE_PAGE_SIZE = 20
const USAGE_PAGE_SIZE = 20

interface DatasetFileEntry {
  path: string
  sha256: string
}

interface DatasetCaseColumn {
  key: string
  label: string
  keys: string[]
  preview: boolean
  width: number
}

interface DatasetSuiteGroup {
  version: string
  suites: EvaluationDataset[]
  caseCount: number
  batchCount: number
  lastRunAt: string | null
  archived: boolean
}

interface CompositionItem {
  label: string
  count: number
  ratio: number
}

const kindMeta = computed(() => evaluationKindMeta[props.kind])
const datasets = ref<EvaluationDataset[]>([])
const datasetsBusy = ref(false)
const datasetsError = ref('')
const showArchived = ref(false)
const selectedVersion = ref('')
const selectedDatasetId = ref('')
const detailTab = ref<'composition' | 'usage'>('composition')
const raw = ref<{ cases: Record<string, unknown>[]; files: Record<string, string> } | null>(null)
const rawBusy = ref(false)
const rawError = ref('')
const rawCaseDialog = ref(false)
const rawCaseTitle = ref('')
const rawCaseJson = ref('')
const dsRawBusy = ref(false)
const casePage = ref(0)
const usageDatasetId = ref('')
let datasetRequest = 0
let rawRequest = 0

const groups = computed<DatasetSuiteGroup[]>(() => groupDatasets(datasets.value))
const selectedGroup = computed(
  () => groups.value.find((group) => group.version === selectedVersion.value) ?? null,
)
const selectedDataset = computed(
  () =>
    selectedGroup.value?.suites.find((suite) => suite.dataset_id === selectedDatasetId.value) ??
    null,
)

const usageEndpoint = computed(() => {
  const query = new URLSearchParams({
    limit: String(USAGE_PAGE_SIZE),
    eval_kind: props.kind,
  })
  if (selectedVersion.value) query.set('dataset_version', selectedVersion.value)
  if (usageDatasetId.value) query.set('dataset_id', usageDatasetId.value)
  return '/evaluations/runs?' + query
})
const {
  rows: usageRuns,
  nextCursor: usageNext,
  busy: usageBusy,
  error: usageError,
  page: usagePage,
  turn: turnUsage,
  reset: resetUsage,
} = useCursorPage<EvaluationRun>(props.api, usageEndpoint)

const datasetFiles = computed<DatasetFileEntry[]>(() => {
  const files = selectedDataset.value?.metadata?.files
  if (!files || typeof files !== 'object') return []
  return Object.entries(files as Record<string, unknown>)
    .filter((entry): entry is [string, string] => typeof entry[1] === 'string')
    .map(([path, sha256]) => ({ path, sha256 }))
})

const caseColumns = computed<DatasetCaseColumn[]>(() => {
  if (props.kind === 'rag') {
    return [
      { key: 'case_id', label: '用例 ID', keys: ['case_id', 'id'], preview: false, width: 160 },
      { key: 'capability', label: '能力大类', keys: ['capability'], preview: false, width: 130 },
      { key: 'category', label: '子类', keys: ['category'], preview: false, width: 130 },
      { key: 'track', label: '用途', keys: ['track'], preview: false, width: 100 },
      {
        key: 'question',
        label: '问题',
        keys: ['question', 'query', 'prompt'],
        preview: true,
        width: 300,
      },
      {
        key: 'expected_documents',
        label: '预期文档 / 依据',
        keys: ['expected_documents', 'expected', 'reference_answer', 'expected_facts'],
        preview: true,
        width: 300,
      },
    ]
  }
  return [
    { key: 'id', label: '用例 ID', keys: ['id', 'case_id'], preview: false, width: 160 },
    { key: 'capability', label: '能力大类', keys: ['capability'], preview: false, width: 130 },
    { key: 'category', label: '子类', keys: ['category'], preview: false, width: 130 },
    { key: 'track', label: '用途', keys: ['track'], preview: false, width: 100 },
    {
      key: 'prompt',
      label: 'Prompt / 输入',
      keys: ['prompt', 'input', 'question'],
      preview: true,
      width: 320,
    },
    {
      key: 'required_tools',
      label: '必调工具',
      keys: ['required_tools', 'expected_tools', 'expected'],
      preview: true,
      width: 200,
    },
  ]
})

const currentPageCases = computed(() => {
  const cases = raw.value?.cases ?? []
  const start = casePage.value * CASE_PAGE_SIZE
  return cases.slice(start, start + CASE_PAGE_SIZE)
})
const casePageCount = computed(() =>
  Math.max(1, Math.ceil((raw.value?.cases.length ?? 0) / CASE_PAGE_SIZE)),
)

const composition = computed(() => ({
  capability: countBy(raw.value?.cases ?? [], 'capability'),
  category: countBy(raw.value?.cases ?? [], 'category'),
  track: countBy(raw.value?.cases ?? [], 'track'),
  topic: countBy(raw.value?.cases ?? [], 'topic'),
}))

const compositionSections = computed(() => [
  { key: 'capability', label: '能力大类分布', items: composition.value.capability },
  { key: 'track', label: '用途分布', items: composition.value.track },
  { key: 'category', label: '子类分布', items: composition.value.category },
  { key: 'topic', label: '业务主题分布', items: composition.value.topic },
])

/**
 * 按版本聚合各套件数据集，供左侧版本列表使用。
 * @param items - 数据集接口返回的套件行。
 * @returns 按版本降序排列的聚合项。
 */
function groupDatasets(items: EvaluationDataset[]): DatasetSuiteGroup[] {
  const grouped = new Map<string, DatasetSuiteGroup>()
  for (const item of items) {
    const current = grouped.get(item.version) ?? {
      version: item.version,
      suites: [],
      caseCount: 0,
      batchCount: 0,
      lastRunAt: null as string | null,
      archived: true,
    }
    current.suites.push(item)
    current.caseCount += item.case_count
    current.batchCount += item.batch_count
    current.archived = current.archived && item.archived
    if (item.last_run_at && (!current.lastRunAt || item.last_run_at > current.lastRunAt)) {
      current.lastRunAt = item.last_run_at
    }
    grouped.set(item.version, current)
  }
  return Array.from(grouped.values()).sort((left, right) =>
    right.version.localeCompare(left.version),
  )
}

/**
 * 统计某字段的分布，缺失值归入"未标注"。
 * @param cases - 原始用例。
 * @param key - 用例字段名。
 * @returns 按数量降序排列的分布项。
 */
function countBy(cases: Record<string, unknown>[], key: string): CompositionItem[] {
  const counts = new Map<string, number>()
  for (const item of cases) {
    const value = item[key]
    const label = typeof value === 'string' && value.trim() ? value : '未标注'
    counts.set(label, (counts.get(label) ?? 0) + 1)
  }
  const total = cases.length || 1
  return Array.from(counts.entries())
    .map(([label, count]) => ({ label, count, ratio: count / total }))
    .sort((left, right) => right.count - left.count)
}

/**
 * 读取当前体系的数据集版本列表。
 */
async function loadDatasets(): Promise<void> {
  const request = ++datasetRequest
  datasetsBusy.value = true
  datasetsError.value = ''
  try {
    const query = new URLSearchParams({
      eval_kind: props.kind,
      include_archived: String(showArchived.value),
    })
    const page = await props.api<Items<EvaluationDataset>>('/evaluations/datasets?' + query)
    if (request !== datasetRequest) return
    datasets.value = page.items
    ensureSelection()
  } catch (cause) {
    if (request === datasetRequest) {
      datasetsError.value = cause instanceof Error ? cause.message : '读取数据集列表失败'
    }
  } finally {
    if (request === datasetRequest) datasetsBusy.value = false
  }
}

/**
 * 保持当前版本选择有效；首次进入时选中最新版本。
 */
function ensureSelection(): void {
  if (groups.value.some((group) => group.version === selectedVersion.value)) return
  selectedVersion.value = groups.value[0]?.version ?? ''
}

/**
 * 切换数据集版本并重置详情状态。
 * @param version - 目标版本号。
 */
function selectVersion(version: string): void {
  selectedVersion.value = version
}

watch(showArchived, () => {
  void loadDatasets()
})

watch(
  () => props.kind,
  () => {
    datasets.value = []
    selectedVersion.value = ''
    selectedDatasetId.value = ''
    usageDatasetId.value = ''
    raw.value = null
    void loadDatasets()
  },
)

watch(
  selectedGroup,
  (group) => {
    if (!group) {
      selectedDatasetId.value = ''
      raw.value = null
      return
    }
    if (!group.suites.some((suite) => suite.dataset_id === selectedDatasetId.value)) {
      selectedDatasetId.value = group.suites[0]?.dataset_id ?? ''
    }
    if (!group.suites.some((suite) => suite.dataset_id === usageDatasetId.value)) {
      usageDatasetId.value = ''
    }
  },
  { immediate: true },
)

watch(selectedDatasetId, () => {
  raw.value = null
  rawError.value = ''
  casePage.value = 0
  void loadRaw()
})

watch([selectedVersion, usageDatasetId], () => {
  if (selectedVersion.value) resetUsage()
})

/**
 * 读取当前套件全部原始用例与文件清单。
 */
async function loadRaw(): Promise<void> {
  const dataset = selectedDataset.value
  if (!dataset) return
  const request = ++rawRequest
  rawBusy.value = true
  rawError.value = ''
  try {
    const payload = await props.api<{
      cases: Record<string, unknown>[]
      files: Record<string, string>
    }>('/evaluations/datasets/' + encodeURIComponent(dataset.dataset_id) + '/raw')
    if (request !== rawRequest) return
    raw.value = payload
    casePage.value = 0
  } catch (cause) {
    if (request === rawRequest) {
      rawError.value = cause instanceof Error ? cause.message : '读取原始数据集失败'
    }
  } finally {
    if (request === rawRequest) rawBusy.value = false
  }
}

/**
 * 展示套件的中文名称。
 * @param dataset - 数据集套件行。
 * @returns 套件中文名或原始编码。
 */
function suiteLabel(dataset: EvaluationDataset): string {
  return evaluationSuites[dataset.suite] ?? dataset.name ?? dataset.suite
}

/**
 * 展示批次行对应的数据集套件。
 * @param run - 评测批次。
 * @returns 套件中文名或批次内记录的名称。
 */
function suiteLabelByRun(run: EvaluationRun): string {
  return run.dataset_name ? (evaluationSuites[run.dataset_name] ?? run.dataset_name) : '—'
}

/**
 * 截断摘要文本。
 * @param value - 摘要字符串。
 * @returns 前 12 位或缺失占位。
 */
function shortSha(value: string | null | undefined): string {
  return value ? value.slice(0, 12) : '—'
}

/**
 * 读取用例某个字段的文本表示。
 * @param row - 原始用例。
 * @param keys - 候选字段。
 * @returns 字段文本；多个值以顿号连接。
 */
function textCase(row: Record<string, unknown>, keys: string[]): string {
  for (const key of keys) {
    const value = row[key]
    if (value === undefined || value === null) continue
    if (Array.isArray(value)) return value.map((item) => String(item)).join('、')
    if (typeof value === 'object') return JSON.stringify(value)
    return String(value)
  }
  return '—'
}

/**
 * 读取用例字段的预览文本，超长时截断。
 * @param row - 原始用例。
 * @param keys - 候选字段。
 * @returns 预览文本。
 */
function previewCase(row: Record<string, unknown>, keys: string[]): string {
  const text = textCase(row, keys)
  return text.length > 100 ? text.slice(0, 100) + '…' : text
}

/**
 * 打开用例完整 JSON 弹窗。
 * @param row - 原始用例。
 */
function openRawCase(row: Record<string, unknown> | undefined): void {
  if (!row) return
  rawCaseTitle.value = String(row.case_id ?? row.id ?? '')
  rawCaseJson.value = JSON.stringify(row, null, 2)
  rawCaseDialog.value = true
}

/**
 * 导出当前套件全部原始用例与文件摘要。
 */
async function downloadRaw(): Promise<void> {
  const dataset = selectedDataset.value
  if (!dataset) return
  dsRawBusy.value = true
  try {
    const payload = raw.value
    if (!payload) throw new Error('原始用例尚未加载')
    const text = JSON.stringify(payload, null, 2)
    saveBlob(new Blob([text], { type: 'application/json' }), dataset.dataset_id + '-raw.json')
  } catch (cause) {
    ElMessage.error(cause instanceof Error ? cause.message : '导出原始用例失败')
  } finally {
    dsRawBusy.value = false
  }
}

/**
 * 构造数据集文件下载地址。
 * @param datasetId - 数据集编号。
 * @param path - 文件相对路径。
 * @returns 同源下载地址。
 */
function fileUrl(datasetId: string, path: string): string {
  return (
    '/api/evaluations/datasets/' +
    encodeURIComponent(datasetId) +
    '/files?path=' +
    encodeURIComponent(path)
  )
}

/**
 * 在新窗口查看数据集原始文件。
 * @param path - 文件相对路径。
 */
function viewFile(path: string): void {
  const dataset = selectedDataset.value
  if (!dataset) return
  window.open(fileUrl(dataset.dataset_id, path), '_blank', 'noopener')
}

/**
 * 通过临时链接下载数据集原始文件。
 * @param path - 文件相对路径。
 */
async function downloadFile(path: string): Promise<void> {
  const dataset = selectedDataset.value
  if (!dataset) return
  try {
    const response = await fetch(fileUrl(dataset.dataset_id, path))
    if (!response.ok) throw new Error('读取数据集文件失败：文件未登记或内容摘要不一致')
    saveBlob(await response.blob(), path.split('/').at(-1) || 'dataset-file')
  } catch (cause) {
    ElMessage.error(cause instanceof Error ? cause.message : '下载数据集文件失败')
  }
}

/**
 * 触发浏览器下载并释放对象 URL。
 * @param blob - 文件内容。
 * @param filename - 保存文件名。
 */
function saveBlob(blob: Blob, filename: string): void {
  const url = URL.createObjectURL(blob)
  const link = document.createElement('a')
  link.href = url
  link.download = filename
  document.body.append(link)
  link.click()
  link.remove()
  URL.revokeObjectURL(url)
}

onMounted(() => {
  void loadDatasets()
})
</script>

<style scoped>
.evaluation-dataset-layout {
  display: grid;
  grid-template-columns: minmax(230px, 280px) minmax(0, 1fr);
  gap: 16px;
  align-items: start;
}

.evaluation-dataset-sidebar {
  display: flex;
  flex-direction: column;
  gap: 8px;
  max-height: 760px;
  overflow: auto;
}

.evaluation-dataset-card {
  display: flex;
  flex-direction: column;
  gap: 6px;
  padding: 10px 12px;
  color: var(--tz-ink);
  text-align: left;
  background: var(--tz-surface);
  border: 1px solid var(--tz-border);
  border-radius: 10px;
  cursor: pointer;
}

.evaluation-dataset-card:hover {
  border-color: var(--tz-primary);
}

.evaluation-dataset-card-active {
  background: var(--tz-primary-soft, rgba(64, 128, 255, 0.08));
  border-color: var(--tz-primary);
}

.evaluation-dataset-card-title {
  font-size: 14px;
  font-weight: 600;
}

.evaluation-dataset-card-tags {
  display: flex;
  flex-wrap: wrap;
  gap: 4px;
}

.evaluation-dataset-card-meta {
  color: var(--tz-ink-muted);
  font-size: 12px;
}

.evaluation-dataset-detail {
  min-width: 0;
}

.evaluation-dataset-detail-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 12px;
}

.evaluation-dataset-suite-bar {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 10px 16px;
  margin-bottom: 12px;
}

.evaluation-dataset-summary {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(100px, 1fr));
  gap: 8px;
  margin-bottom: 14px;
}

.evaluation-dataset-stat {
  display: flex;
  flex-direction: column;
  gap: 4px;
  padding: 10px 12px;
  background: var(--tz-surface-muted, var(--tz-surface));
  border: 1px solid var(--tz-border);
  border-radius: 10px;
}

.evaluation-dataset-stat span {
  color: var(--tz-ink-muted);
  font-size: 12px;
}

.evaluation-dataset-stat strong {
  font-size: 20px;
}

.evaluation-dataset-distributions {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(260px, 1fr));
  gap: 14px;
  margin-bottom: 16px;
}

.evaluation-dataset-distribution h5 {
  margin: 0 0 8px;
}

.evaluation-dataset-bars {
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.evaluation-dataset-bar-row {
  display: grid;
  grid-template-columns: minmax(90px, 160px) minmax(80px, 1fr) 48px;
  align-items: center;
  gap: 8px;
}

.evaluation-dataset-bar-label {
  overflow: hidden;
  color: var(--tz-ink-secondary);
  font-size: 12px;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.evaluation-dataset-bar-track {
  height: 8px;
  overflow: hidden;
  background: var(--tz-surface-muted, rgba(0, 0, 0, 0.06));
  border-radius: 999px;
}

.evaluation-dataset-bar-fill {
  display: block;
  height: 100%;
  background: var(--tz-primary);
  border-radius: 999px;
}

.evaluation-dataset-bar-count {
  color: var(--tz-ink-muted);
  font-size: 12px;
  text-align: right;
}

.evaluation-dataset-actions {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  margin-bottom: 16px;
}

.evaluation-dataset-usage-filter {
  width: 220px;
}

@media (max-width: 900px) {
  .evaluation-dataset-layout {
    grid-template-columns: minmax(0, 1fr);
  }

  .evaluation-dataset-sidebar {
    max-height: none;
  }
}
</style>
