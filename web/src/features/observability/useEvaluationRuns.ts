import { computed, type Ref } from 'vue'
import type { ApiClient } from '../../shared/apiTypes'
import { useCursorPage } from '../../shared/useCursorPage'
import {
  type EvaluationKind,
  type EvaluationMode,
  type EvaluationRun,
  type EvaluationScope,
} from './evaluationTypes'

const RUN_PAGE_SIZE = 25

/**
 * 评测批次组合式函数：按当前筛选条件拼装游标分页接口，并复用通用游标分页。
 * @param api - 已绑定账号的接口。
 * @param options - 当前筛选条件与权限。
 * @param options.evalKind - 当前评测体系。
 * @param options.scope - 当前评测口径。
 * @param options.mode - 当前执行方式。
 * @param options.isAdmin - 当前账号是否为管理员（可跨用户筛选）。
 * @param options.owner - 管理员选择的发起人账号。
 * @param options.runDatasetId - 批次页按数据集筛选的编号。
 * @returns 批次列表、分页状态、接口路径与读取方法。
 */
export function useEvaluationRuns(
  api: ApiClient,
  options: {
    evalKind: Ref<EvaluationKind>
    scope: Ref<EvaluationScope>
    mode: Ref<EvaluationMode>
    isAdmin: Ref<boolean>
    owner: Ref<string>
    runDatasetId: Ref<string>
  },
) {
  const runEndpoint = computed(() => {
    const query = new URLSearchParams({
      limit: String(RUN_PAGE_SIZE),
      eval_kind: options.evalKind.value,
      scope: options.scope.value,
      mode: options.mode.value,
    })
    if (options.isAdmin.value && options.owner.value) query.set('owner_id', options.owner.value)
    if (options.runDatasetId.value) query.set('dataset_id', options.runDatasetId.value)
    return '/evaluations/runs?' + query
  })
  const {
    rows: runs,
    nextCursor: runsNext,
    busy: runsBusy,
    error: runsError,
    page: runsPageNo,
    refresh: refreshRuns,
    reset: resetRuns,
    turn: turnRuns,
  } = useCursorPage<EvaluationRun>(api, runEndpoint)

  return {
    runEndpoint,
    runs,
    runsNext,
    runsBusy,
    runsError,
    runsPageNo,
    refreshRuns,
    resetRuns,
    turnRuns,
  }
}
