import { ref, type Ref } from 'vue'
import type { ApiClient, Items } from '../../shared/apiTypes'
import { type EvaluationBaseline, type EvaluationKind } from './evaluationTypes'

/**
 * 评测基线组合式函数：按评测体系、数据集版本与 active 开关读取基线列表。
 * @param api - 已绑定账号的接口。
 * @param evalKind - 当前评测体系。
 * @param datasetVersion - 当前数据集版本；为空时不加版本过滤。
 * @param baselineActiveOnly - 是否只看 active 基线。
 * @returns 基线列表、读取状态与读取方法。
 */
export function useEvaluationBaselines(
  api: ApiClient,
  evalKind: Ref<EvaluationKind>,
  datasetVersion: Ref<string>,
  baselineActiveOnly: Ref<boolean>,
) {
  const baselines = ref<EvaluationBaseline[]>([])
  const baselinesBusy = ref(false)
  const baselinesError = ref('')
  let baselineRequest = 0

  /**
   * 读取当前评测体系与数据集版本下的基线（active 与历史）。
   */
  async function loadBaselines(): Promise<void> {
    const request = ++baselineRequest
    baselinesBusy.value = true
    baselinesError.value = ''
    try {
      const query = new URLSearchParams({
        eval_kind: evalKind.value,
        include_history: String(!baselineActiveOnly.value),
      })
      if (datasetVersion.value) query.set('dataset_version', datasetVersion.value)
      const page = await api<Items<EvaluationBaseline>>('/evaluations/baselines?' + query)
      if (request !== baselineRequest) return
      baselines.value = page.items
    } catch (cause) {
      if (request === baselineRequest) {
        baselinesError.value = cause instanceof Error ? cause.message : '读取基线失败'
      }
    } finally {
      if (request === baselineRequest) baselinesBusy.value = false
    }
  }

  return {
    baselines,
    baselinesBusy,
    baselinesError,
    loadBaselines,
  }
}
