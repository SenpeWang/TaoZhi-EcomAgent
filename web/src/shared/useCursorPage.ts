import { computed, onMounted, onScopeDispose, ref, shallowRef, type ComputedRef } from 'vue'
import type { ApiReader, CursorPage } from './apiTypes'

/**
 * 按游标翻页，同时丢弃筛选变化后迟到的响应。
 * @param api - 已绑定账号的读取接口。
 * @param endpoint - 包含当前筛选条件的页面接口路径。
 * @returns 当前数据、分页位置和读取操作。
 */
export function useCursorPage<T>(api: ApiReader, endpoint: ComputedRef<string>) {
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
      const result = await api<CursorPage<T>>(path)
      if (version === request) {
        rows.value = result.items
        nextCursor.value = result.next_cursor
      }
    } catch (cause) {
      if (version === request) error.value = cause instanceof Error ? cause.message : '读取列表失败'
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

  /** 返回当前筛选条件的第一页。 */
  function reset(): void {
    cursors.value = ['']
    void refresh()
  }

  onMounted(refresh)
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
    reset,
    turn,
  }
}
