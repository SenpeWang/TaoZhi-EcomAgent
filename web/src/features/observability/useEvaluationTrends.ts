import { computed, ref, type Ref } from 'vue'
import type { ApiClient } from '../../shared/apiTypes'
import {
  type EvaluationKind,
  type EvaluationMetricSpec,
  type EvaluationMode,
  type EvaluationScope,
  type EvaluationTrends,
} from './evaluationTypes'
import { shortMetricLabel, type MetricCard } from './evaluationHelpers'

const TREND_LIMIT = 30

/**
 * 评测趋势组合式函数：按评测体系、数据集版本、口径与执行方式读取指标趋势，
 * 并派生概览指标卡、聚合统计与趋势表所需的批次序列。
 * @param api - 已绑定账号的接口。
 * @param options - 当前筛选条件与指标目录。
 * @param options.evalKind - 当前评测体系。
 * @param options.scope - 当前评测口径。
 * @param options.mode - 当前执行方式。
 * @param options.datasetVersion - 当前数据集版本；为空时不加版本过滤。
 * @param options.metrics - 当前口径的指标定义。
 * @returns 趋势数据、指标卡、聚合统计与读取方法。
 */
export function useEvaluationTrends(
  api: ApiClient,
  options: {
    evalKind: Ref<EvaluationKind>
    scope: Ref<EvaluationScope>
    mode: Ref<EvaluationMode>
    datasetVersion: Ref<string>
    metrics: Ref<EvaluationMetricSpec[]>
  },
) {
  const trends = ref<EvaluationTrends | null>(null)
  const trendsBusy = ref(false)
  const trendsError = ref('')
  let trendRequest = 0

  const trendItems = computed(() => trends.value?.items ?? [])
  const latestTrend = computed(() => trendItems.value.at(-1) ?? null)
  const baselineTrend = computed(() => {
    const summary = trends.value?.baseline
    if (!summary) return null
    return (
      trendItems.value.find((item) => item.run_id === summary.run_id) ??
      trendItems.value.find((item) => item.is_baseline) ??
      null
    )
  })
  const baselineComparable = computed(() => {
    const latest = latestTrend.value
    const baseline = baselineTrend.value
    return Boolean(latest && baseline && latest.comparable)
  })
  const metricCards = computed<MetricCard[]>(() =>
    options.metrics.value.map((spec) => {
      const latest = latestTrend.value?.summary?.[spec.key] ?? null
      const baseline = baselineTrend.value?.summary?.[spec.key] ?? null
      const delta =
        baselineComparable.value && latest !== null && baseline !== null ? latest - baseline : null
      return { spec, latest, baseline, delta }
    }),
  )
  const aggregateMetric = computed<EvaluationMetricSpec | null>(
    () =>
      options.metrics.value.find((item) => item.unit === 'ratio' || item.unit === 'percent') ??
      null,
  )
  const aggregateStats = computed(() => {
    const spec = aggregateMetric.value
    const values = trendItems.value
      .map((item) => (spec ? (item.summary?.[spec.key] ?? null) : null))
      .filter((value): value is number => value !== null)
    return {
      batches: trends.value?.aggregate.batches ?? trendItems.value.length,
      latest: values.length ? values[values.length - 1] : null,
      average: values.length ? values.reduce((sum, value) => sum + value, 0) / values.length : null,
      best: values.length ? Math.max(...values) : null,
      label: spec?.label ?? '通过率',
    }
  })
  const aggregateLabel = computed(() =>
    aggregateMetric.value ? shortMetricLabel(aggregateMetric.value) : '通过率',
  )

  /**
   * 读取当前 kind + 数据集版本 + 口径 + 执行方式的指标趋势与 active 基线摘要。
   */
  async function loadTrends(): Promise<void> {
    const request = ++trendRequest
    trendsBusy.value = true
    trendsError.value = ''
    try {
      const query = new URLSearchParams({
        eval_kind: options.evalKind.value,
        scope: options.scope.value,
        mode: options.mode.value,
        limit: String(TREND_LIMIT),
      })
      if (options.datasetVersion.value) query.set('dataset_version', options.datasetVersion.value)
      const response = await api<EvaluationTrends>('/evaluations/metrics/trends?' + query)
      if (request !== trendRequest) return
      trends.value = response
    } catch (cause) {
      if (request === trendRequest) {
        trendsError.value = cause instanceof Error ? cause.message : '读取指标趋势失败'
      }
    } finally {
      if (request === trendRequest) trendsBusy.value = false
    }
  }

  return {
    trends,
    trendsBusy,
    trendsError,
    trendItems,
    latestTrend,
    metricCards,
    aggregateMetric,
    aggregateStats,
    aggregateLabel,
    loadTrends,
  }
}
