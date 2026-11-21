import { computed, onScopeDispose, ref, shallowRef, watch, type ComputedRef } from 'vue'
import type { EvaluationApi, EvaluationPage } from '../evaluations'

/**
 * 按游标加载评测批次或用例，筛选变化时清空旧页并丢弃迟到响应。
 * @param api - 已绑定账号的读取接口。
 * @param endpoint - 包含当前筛选条件的页面接口路径。
 * @returns 当前数据、分页位置和读取操作。
 */
export function useEvaluationPage<T>(api: EvaluationApi, endpoint: ComputedRef<string>) {
  const rows = shallowRef<T[]>([])
  const cursors = ref([''])
  const nextCursor = ref<string | null>(null)
  const busy = ref(false)
  const error = ref('')
  let request = 0

  /** 从当前筛选条件和游标读取一页。 */
  async function refresh(): Promise<void> {
    const version = ++request
    const cursor = cursors.value.at(-1)
    const path = endpoint.value + (cursor ? '&cursor=' + encodeURIComponent(cursor) : '')
    busy.value = true
    error.value = ''
    rows.value = []
    nextCursor.value = null
    try {
      const result = await api<EvaluationPage<T>>(path)
      if (version === request) {
        rows.value = result.items
        nextCursor.value = result.next_cursor
      }
    } catch (cause) {
      if (version === request)
        error.value = cause instanceof Error ? cause.message : '读取评测记录失败'
    } finally {
      if (version === request) busy.value = false
    }
  }

  /**
   * 根据当前游标前后翻页。
   * @param forward - 是否读取下一页。
   */
  function turn(forward: boolean): void {
    if (busy.value) return
    if (forward && nextCursor.value) cursors.value.push(nextCursor.value)
    else if (!forward && cursors.value.length > 1) cursors.value.pop()
    else return
    void refresh()
  }

  watch(
    endpoint,
    () => {
      cursors.value = ['']
      void refresh()
    },
    { immediate: true },
  )
  onScopeDispose(() => {
    request++
  })
  return {
    rows,
    nextCursor,
    busy,
    error,
    page: computed(() => cursors.value.length),
    refresh,
    turn,
  }
}
