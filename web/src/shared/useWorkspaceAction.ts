import { computed, ref } from 'vue'
import { ElMessage } from 'element-plus'

/**
 * 管理当前组件的并行操作计数，统一显示服务端错误并忽略已取消的交互。
 * @returns 当前组件的忙碌状态与异步操作入口。
 */
export function useWorkspaceAction() {
  const pending = ref(0)

  /**
   * 运行一次界面操作，并在其完成后释放对应的加载状态。
   * @param operation - 需要执行的异步操作。
   */
  async function action(operation: () => Promise<void>): Promise<void> {
    pending.value++
    try {
      await operation()
    } catch (error: unknown) {
      if (error === 'cancel' || error === 'close') return
      if (error instanceof DOMException && error.name === 'AbortError') return
      ElMessage.error(error instanceof Error ? error.message : '操作未完成，请稍后重试')
    } finally {
      pending.value--
    }
  }

  return { busy: computed(() => pending.value > 0), action }
}
