import { computed, ref, type Ref } from 'vue'
import type { ApiClient, CursorPage } from '../../shared/apiTypes'
import { type EvaluationAuditAction, type EvaluationAuditLog } from './evaluationTypes'

const AUDIT_PAGE_SIZE = 30

/**
 * 评测审计组合式函数：维护动作筛选、游标分页与读取状态；仅写入权限账号可读取。
 * @param api - 已绑定账号的接口。
 * @param canWriteEval - 当前账号是否具备评测写入权限。
 * @returns 审计记录、动作筛选、分页状态与读取方法。
 */
export function useEvaluationAudit(api: ApiClient, canWriteEval: Ref<boolean>) {
  const auditAction = ref<EvaluationAuditAction | ''>('')
  const auditLogs = ref<EvaluationAuditLog[]>([])
  const auditCursors = ref<string[]>([''])
  const auditNext = ref<string | null>(null)
  const auditBusy = ref(false)
  const auditError = ref('')
  const auditPage = computed(() => auditCursors.value.length)

  /**
   * 读取一页评测审计留痕；仅写入权限账号可读取。
   */
  async function loadAuditLogs(): Promise<void> {
    if (!canWriteEval.value) return
    auditBusy.value = true
    auditError.value = ''
    try {
      const query = new URLSearchParams({ limit: String(AUDIT_PAGE_SIZE) })
      if (auditAction.value) query.set('action', auditAction.value)
      const cursor = auditCursors.value.at(-1)
      if (cursor) query.set('cursor', cursor)
      const page = await api<CursorPage<EvaluationAuditLog>>('/evaluations/audit-logs?' + query)
      auditLogs.value = page.items
      auditNext.value = page.next_cursor
    } catch (cause) {
      auditError.value = cause instanceof Error ? cause.message : '读取审计留痕失败'
    } finally {
      auditBusy.value = false
    }
  }

  /**
   * 切换审计动作筛选并回到第一页。
   */
  function resetAudit(): void {
    auditCursors.value = ['']
    auditNext.value = null
    void loadAuditLogs()
  }

  /**
   * 按游标前后翻动审计留痕页。
   * @param forward - 是否读取下一页。
   */
  function turnAudit(forward: boolean): void {
    if (auditBusy.value) return
    if (forward && auditNext.value) auditCursors.value.push(auditNext.value)
    else if (!forward && auditCursors.value.length > 1) auditCursors.value.pop()
    else return
    void loadAuditLogs()
  }

  return {
    auditAction,
    auditLogs,
    auditNext,
    auditBusy,
    auditError,
    auditPage,
    loadAuditLogs,
    resetAudit,
    turnAudit,
  }
}
