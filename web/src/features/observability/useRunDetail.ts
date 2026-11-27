import { computed, ref } from 'vue'
import { ElMessage } from 'element-plus'
import type { ApiClient, CursorPage } from '../../shared/apiTypes'
import {
  type EvaluationCase,
  type EvaluationCaseDetail,
  type EvaluationDatasetSnapshot,
  type EvaluationRunDetail,
  type EvaluationRunSnapshotCase,
  type EvaluationRunSnapshotPage,
} from './evaluationTypes'

const RUN_CASE_PAGE_SIZE = 30
const SNAPSHOT_CASE_PAGE_SIZE = 20

/**
 * 批次详情组合式函数：管理批次详情抽屉、逐例结果分页、用例原始判定弹窗
 * 与数据集快照抽屉；只在打开详情时发起请求。
 * @param api - 已绑定账号的接口。
 * @returns 详情抽屉、逐例结果、快照抽屉状态与全部读取方法。
 */
export function useRunDetail(api: ApiClient) {
  const runDetailOpen = ref(false)
  const runDetailId = ref('')
  const runDetail = ref<EvaluationRunDetail | null>(null)
  const runDetailBusy = ref(false)
  const runDetailError = ref('')
  const runCaseRows = ref<EvaluationCase[]>([])
  const runCaseCursors = ref<string[]>([''])
  const runCaseNext = ref<string | null>(null)
  const runCaseBusy = ref(false)
  const runCaseError = ref('')
  const caseJsonDialog = ref(false)
  const caseJsonText = ref('')
  const caseJsonCaseId = ref('')
  const caseJsonTrace = ref<{ task_id: string; trace_id: string } | null>(null)
  const caseJsonDatasetText = ref('')
  const caseJsonDatasetSha256 = ref<string | null>(null)
  const snapshotDrawer = ref(false)
  const snapshotRunId = ref('')
  const snapshotMeta = ref<EvaluationDatasetSnapshot | null>(null)
  const snapshotCases = ref<EvaluationRunSnapshotCase[]>([])
  const snapshotCursors = ref<string[]>([''])
  const snapshotNext = ref<string | null>(null)
  const snapshotTotal = ref(0)
  const snapshotBusy = ref(false)
  const snapshotError = ref('')
  const rawCaseDialog = ref(false)
  const rawCaseJson = ref('')
  const rawCaseTitle = ref('')

  let runDetailRequest = 0
  let runCaseRequest = 0
  let snapshotRequest = 0

  const runCasePage = computed(() => runCaseCursors.value.length)
  const snapshotPage = computed(() => snapshotCursors.value.length)

  /**
   * 打开批次详情抽屉并装载指标 schema、逐例结果。
   * @param runId - 批次编号。
   */
  async function openRunDetail(runId: string): Promise<void> {
    runDetailId.value = runId
    runDetail.value = null
    runDetailError.value = ''
    runDetailOpen.value = true
    runCaseRows.value = []
    runCaseCursors.value = ['']
    runCaseNext.value = null
    runCaseError.value = ''
    await Promise.all([loadRunDetail(), loadRunCases()])
  }

  /**
   * 读取批次详情（含 summary 与 metric_schema）。
   */
  async function loadRunDetail(): Promise<void> {
    const request = ++runDetailRequest
    runDetailBusy.value = true
    runDetailError.value = ''
    try {
      const detail = await api<EvaluationRunDetail>(
        '/evaluations/runs/' + encodeURIComponent(runDetailId.value),
      )
      if (request === runDetailRequest) runDetail.value = detail
    } catch (cause) {
      if (request === runDetailRequest) {
        runDetailError.value = cause instanceof Error ? cause.message : '读取批次详情失败'
      }
    } finally {
      if (request === runDetailRequest) runDetailBusy.value = false
    }
  }

  /**
   * 按游标读取批次逐例判定。
   */
  async function loadRunCases(): Promise<void> {
    const request = ++runCaseRequest
    runCaseBusy.value = true
    runCaseError.value = ''
    try {
      const query = new URLSearchParams({ limit: String(RUN_CASE_PAGE_SIZE) })
      const cursor = runCaseCursors.value.at(-1)
      if (cursor) query.set('cursor', cursor)
      const page = await api<CursorPage<EvaluationCase>>(
        '/evaluations/runs/' + encodeURIComponent(runDetailId.value) + '/cases?' + query,
      )
      if (request !== runCaseRequest) return
      runCaseRows.value = page.items
      runCaseNext.value = page.next_cursor
    } catch (cause) {
      if (request === runCaseRequest) {
        runCaseError.value = cause instanceof Error ? cause.message : '读取逐例结果失败'
      }
    } finally {
      if (request === runCaseRequest) runCaseBusy.value = false
    }
  }

  /**
   * 按游标前后翻动批次逐例结果页。
   * @param forward - 是否读取下一页。
   */
  function turnRunCase(forward: boolean): void {
    if (runCaseBusy.value) return
    if (forward && runCaseNext.value) runCaseCursors.value.push(runCaseNext.value)
    else if (!forward && runCaseCursors.value.length > 1) runCaseCursors.value.pop()
    else return
    void loadRunCases()
  }

  /**
   * 打开批次数据集快照抽屉并读取第一页原始用例。
   * @param runId - 批次编号。
   */
  async function openSnapshotCases(runId: string): Promise<void> {
    snapshotRunId.value = runId
    snapshotMeta.value = null
    snapshotCases.value = []
    snapshotCursors.value = ['']
    snapshotNext.value = null
    snapshotTotal.value = 0
    snapshotError.value = ''
    snapshotDrawer.value = true
    await loadSnapshotCases()
  }

  /**
   * 按游标读取批次数据集快照中的原始用例页。
   */
  async function loadSnapshotCases(): Promise<void> {
    const request = ++snapshotRequest
    snapshotBusy.value = true
    snapshotError.value = ''
    try {
      const query = new URLSearchParams({ limit: String(SNAPSHOT_CASE_PAGE_SIZE) })
      const cursor = snapshotCursors.value.at(-1)
      if (cursor) query.set('cursor', cursor)
      const page = await api<EvaluationRunSnapshotPage>(
        '/evaluations/runs/' +
          encodeURIComponent(snapshotRunId.value) +
          '/dataset-snapshot?' +
          query,
      )
      if (request !== snapshotRequest) return
      snapshotMeta.value = page.snapshot
      snapshotCases.value = page.items
      snapshotNext.value = page.next_cursor
      snapshotTotal.value = page.total
    } catch (cause) {
      if (request === snapshotRequest) {
        snapshotError.value = cause instanceof Error ? cause.message : '读取数据集快照用例失败'
      }
    } finally {
      if (request === snapshotRequest) snapshotBusy.value = false
    }
  }

  /**
   * 按游标前后翻动数据集快照用例页。
   * @param forward - 是否读取下一页。
   */
  function turnSnapshotCase(forward: boolean): void {
    if (snapshotBusy.value) return
    if (forward && snapshotNext.value) snapshotCursors.value.push(snapshotNext.value)
    else if (!forward && snapshotCursors.value.length > 1) snapshotCursors.value.pop()
    else return
    void loadSnapshotCases()
  }

  /**
   * 展示快照用例的完整原始 case_payload JSON。
   * @param item - 快照中的原始用例记录。
   */
  function openSnapshotCaseJson(item: EvaluationRunSnapshotCase): void {
    rawCaseJson.value = JSON.stringify(item.case_payload, null, 2)
    rawCaseTitle.value = item.suite + ' / ' + item.case_id
    rawCaseDialog.value = true
  }

  /**
   * 打开用例详情并展示 expected / observed / verdicts 与数据集快照原始用例。
   * @param caseId - 用例编号。
   */
  async function openCaseJson(caseId: string): Promise<void> {
    if (!runDetail.value) return
    const runId = runDetail.value.run.run_id
    try {
      const detail = await api<EvaluationCaseDetail>(
        '/evaluations/runs/' + encodeURIComponent(runId) + '/cases/' + encodeURIComponent(caseId),
      )
      caseJsonCaseId.value = caseId
      caseJsonText.value = JSON.stringify(
        { expected: detail.expected, observed: detail.observed, verdicts: detail.verdicts },
        null,
        2,
      )
      caseJsonDatasetText.value = detail.dataset_case
        ? JSON.stringify(detail.dataset_case, null, 2)
        : runDetail.value.dataset_snapshot
          ? '该用例未在数据集快照中匹配到原始定义（历史批次结果与快照用例编号不一致）。'
          : '该批次没有数据集内容快照（历史批次未登记快照），无法查看数据集原始用例。'
      caseJsonDatasetSha256.value = detail.dataset_case_sha256
      caseJsonTrace.value = detail.trace_link
      caseJsonDialog.value = true
    } catch (cause) {
      ElMessage.error(cause instanceof Error ? cause.message : '读取用例原始判定失败')
    }
  }

  return {
    runDetailOpen,
    runDetailId,
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
    loadRunDetail,
    loadRunCases,
    turnRunCase,
    openSnapshotCases,
    loadSnapshotCases,
    turnSnapshotCase,
    openSnapshotCaseJson,
    openCaseJson,
  }
}
