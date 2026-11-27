import { computed, ref, type Ref } from 'vue'
import type { ApiClient, Items } from '../../shared/apiTypes'
import {
  evaluationScopes,
  type EvaluationDataset,
  type EvaluationKind,
  type EvaluationMetricCatalog,
  type EvaluationMode,
  type EvaluationProfile,
  type EvaluationScope,
} from './evaluationTypes'
import { datasetExecutionModes } from './evaluationHelpers'

/**
 * 评测目录组合式函数：集中持有指标目录、数据集与算法版本，并统一维护
 * 数据集版本、口径、执行方式的当前选择，供评测中心外壳与子组件共用。
 * @param api - 已绑定账号的接口。
 * @param evalKind - 当前评测体系（agent 或 rag）。
 * @returns 目录、数据集、算法版本、筛选状态与对应读取方法。
 */
export function useEvaluationCatalog(api: ApiClient, evalKind: Ref<EvaluationKind>) {
  const catalog = ref<EvaluationMetricCatalog | null>(null)
  const catalogError = ref('')

  const datasets = ref<EvaluationDataset[]>([])
  const datasetsBusy = ref(false)
  const datasetsError = ref('')

  const profiles = ref<EvaluationProfile[]>([])
  const profilesError = ref('')

  const scope = ref<EvaluationScope>('full')
  const mode = ref<EvaluationMode>('live')
  const datasetVersion = ref('')

  let datasetRequest = 0
  let profileRequest = 0

  const kindDatasets = computed(() =>
    datasets.value.filter((item) => item.eval_kind === evalKind.value),
  )
  const activeDatasets = computed(() => kindDatasets.value.filter((item) => !item.archived))
  const datasetVersions = computed(() => {
    const versions = new Set(activeDatasets.value.map((item) => item.version))
    return [...versions].sort((a, b) => b.localeCompare(a))
  })
  const selectedVersionDatasets = computed(() =>
    activeDatasets.value.filter((item) => item.version === datasetVersion.value),
  )
  const primaryDataset = computed(
    () => selectedVersionDatasets.value[0] ?? activeDatasets.value[0] ?? null,
  )
  const allowedModes = computed<EvaluationMode[]>(() => datasetExecutionModes(primaryDataset.value))
  const scopeOptions = computed<{ value: EvaluationScope; label: string }[]>(() => {
    const kind = catalog.value?.kinds[evalKind.value]
    if (kind) {
      return Object.entries(kind.scopes).map(([value, item]) => ({
        value: value as EvaluationScope,
        label: evaluationScopes[value as EvaluationScope] ?? item.label,
      }))
    }
    const fallback: EvaluationScope[] =
      evalKind.value === 'rag'
        ? ['full', 'quick', 'adversarial', 'retrieval', 'behavior']
        : ['full']
    return fallback.map((value) => ({ value, label: evaluationScopes[value] }))
  })
  const triggerDatasets = computed(() => activeDatasets.value)

  /**
   * 读取指标目录；目录是页面所有指标列与卡片的唯一来源。
   */
  async function loadCatalog(): Promise<void> {
    catalogError.value = ''
    try {
      catalog.value = await api<EvaluationMetricCatalog>('/evaluations/metrics/catalog')
      ensureSelections()
    } catch (cause) {
      catalogError.value = cause instanceof Error ? cause.message : '读取指标目录失败'
    }
  }

  /**
   * 读取当前评测体系下可见的数据集版本（含归档，由开关控制展示）。
   */
  async function loadDatasets(): Promise<void> {
    const request = ++datasetRequest
    datasetsBusy.value = true
    datasetsError.value = ''
    try {
      const query = new URLSearchParams({
        eval_kind: evalKind.value,
        include_archived: 'true',
      })
      const page = await api<Items<EvaluationDataset>>('/evaluations/datasets?' + query)
      if (request !== datasetRequest) return
      datasets.value = page.items
      ensureSelections()
    } catch (cause) {
      if (request === datasetRequest) {
        datasetsError.value = cause instanceof Error ? cause.message : '读取数据集失败'
      }
    } finally {
      if (request === datasetRequest) datasetsBusy.value = false
    }
  }

  /**
   * 读取算法版本列表，供发起评测与任务页选择。
   */
  async function loadProfiles(): Promise<void> {
    const request = ++profileRequest
    profilesError.value = ''
    try {
      const query = new URLSearchParams({ eval_kind: evalKind.value })
      if (datasetVersion.value) query.set('dataset_version', datasetVersion.value)
      const page = await api<Items<EvaluationProfile>>('/evaluations/profiles?' + query)
      if (request !== profileRequest) return
      profiles.value = page.items
    } catch (cause) {
      if (request === profileRequest) {
        profilesError.value = cause instanceof Error ? cause.message : '读取算法版本失败'
      }
    }
  }

  /**
   * 按指标目录与数据集登记能力校正版本、口径与执行方式的当前选择。
   */
  function ensureSelections(): void {
    const versions = datasetVersions.value
    if (!datasetVersion.value || !versions.includes(datasetVersion.value)) {
      datasetVersion.value = versions[0] ?? ''
    }
    const scopeValues = scopeOptions.value.map((item) => item.value)
    if (!scopeValues.includes(scope.value)) {
      scope.value = scopeValues.includes('full') ? 'full' : (scopeValues[0] ?? 'full')
    }
    const modes = allowedModes.value
    if (!modes.includes(mode.value)) {
      mode.value = modes.includes('live') ? 'live' : (modes[0] ?? 'live')
    }
  }

  return {
    catalog,
    catalogError,
    datasets,
    datasetsBusy,
    datasetsError,
    profiles,
    profilesError,
    scope,
    mode,
    datasetVersion,
    kindDatasets,
    datasetVersions,
    allowedModes,
    scopeOptions,
    triggerDatasets,
    loadCatalog,
    loadDatasets,
    loadProfiles,
    ensureSelections,
  }
}
