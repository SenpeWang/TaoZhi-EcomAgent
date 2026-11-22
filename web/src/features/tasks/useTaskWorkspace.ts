import { computed, onMounted, onScopeDispose, ref, watch, type Ref } from 'vue'
import { ElMessage } from 'element-plus'
import type { ApiClient, CreatedResource } from '../../shared/apiTypes'
import { openTaskEventStream } from '../../shared/taskStream'
import type { User } from '../administration/types'
import type { KnowledgeQuestion, Task } from './types'

/**
 * 管理任务详情选中状态、流式预览和交互动作，按当前会话及页面校验异步响应。
 * @param options - 当前身份、页面状态及任务访问接口。
 * @returns 任务窗口和问答工作区使用的状态与操作。
 */
export function useTaskWorkspace(options: {
  api: ApiClient
  user: Ref<User | null>
  tasks: Ref<Task[]>
  knowledgePage: Readonly<Ref<boolean>>
  reviewPage: Readonly<Ref<boolean>>
  busy: Readonly<Ref<boolean>>
  action: (operation: () => Promise<void>) => Promise<void>
  load: () => Promise<void>
}) {
  const { api, user, tasks, knowledgePage, reviewPage, busy, action, load } = options

  const taskOpen = ref(false)
  const activeTask = ref<Task | null>(null)
  const taskDraft = ref('')
  const inlineQuestion = ref(false)
  const taskVisible = computed(
    () => taskOpen.value || (inlineQuestion.value && knowledgePage.value),
  )
  let taskSelection = 0

  /**
   * 过滤当前任务视图（我的任务仅展示本人发起的任务，团队审核展示全部待审任务）。
   * @returns 当前页面可见任务列表。
   */
  function computeVisibleTasks(): Task[] {
    if (reviewPage.value) return tasks.value
    const currentUserId = user.value?.id
    return currentUserId
      ? tasks.value.filter((task) => task.owner_id === currentUserId)
      : tasks.value
  }
  const visibleTasks = computed(computeVisibleTasks)

  /** 清除当前所选答复，作废先前请求和正在显示的草稿。 */
  function newQuestion(): void {
    taskSelection++
    inlineQuestion.value = false
    activeTask.value = null
    taskDraft.value = ''
  }

  /**
   * 在问答工作区读取已选的个人提问。
   * @param id - 历史任务编号。
   */
  async function selectQuestion(id: string): Promise<void> {
    newQuestion()
    const selection = taskSelection
    inlineQuestion.value = true
    try {
      await action(() => showTask(id, selection, true))
    } finally {
      if (selection === taskSelection && !activeTask.value) inlineQuestion.value = false
    }
  }

  /**
   * 创建知识提问，在工作区内订阅执行进度和回答。
   * @param question - 问题、业务编号及本次授权选择。
   */
  async function submitQuestion(question: KnowledgeQuestion): Promise<void> {
    if (busy.value || !question.question.trim()) return
    const selection = ++taskSelection
    await action(async () => {
      const result = await api<CreatedResource>('/tasks', 'POST', question)
      if (selection === taskSelection) await showTask(result.id, selection, true)
      await load()
    })
  }

  watch(
    () =>
      taskVisible.value &&
      activeTask.value &&
      !activeTask.value.restricted &&
      ['queued', 'running'].includes(activeTask.value.state)
        ? activeTask.value.id
        : null,
    (taskId, _, onCleanup) => {
      taskDraft.value = ''
      if (!taskId) return
      let selected = true
      const close = openTaskEventStream(taskId, {
        /**
         * 合并当前任务的阶段记录。
         * @param row - 按事件编号去重的阶段消息。
         */
        onMessage: (row) => {
          const task = activeTask.value
          if (!selected || task?.id !== taskId || !row.stage) return
          task.stage = row.stage
          if (typeof row.progress === 'number') task.progress = row.progress
          task.events ??= []
          if (row.id !== undefined && !task.events.some((event) => event.id === row.id)) {
            task.events.push({
              id: row.id,
              stage: row.stage,
              progress: row.progress ?? 0,
              status: row.status ?? 'running',
            })
          }
        },
        /**
         * 更新任务详情中的正文。
         * @param text - 当前执行可展示的全文草稿。
         */
        onResult: (text) => {
          if (selected) taskDraft.value = text
        },
        /** 以服务端发布的终态结果替换草稿。 */
        onDone: () => {
          void api<Task>('/tasks/' + taskId)
            .then((task) => {
              if (selected && activeTask.value?.id === taskId) applyTaskSnapshot(task)
            })
            .catch(() => {})
        },
      })
      onCleanup(() => {
        selected = false
        taskDraft.value = ''
        close()
      })
    },
  )

  /**
   * 同步可见详情和对应的历史摘要，权限变更时同时更新两处展示。
   * @param snapshot - 刚通过服务端权限校验的任务快照。
   */
  function applyTaskSnapshot(snapshot: Task): void {
    activeTask.value = snapshot
    const summary = tasks.value.find((item) => item.id === snapshot.id)
    if (summary) {
      summary.question = snapshot.question
      summary.state = snapshot.state
      summary.restricted = snapshot.restricted
    }
  }

  /**
   * 加载并展示指定任务详情。
   * @param id - 任务 ID。
   */
  async function openTask(id: string): Promise<void> {
    const selection = ++taskSelection
    await action(() => showTask(id, selection))
  }

  /**
   * 仅展示最近选择的任务，避免切换任务或登出后的旧响应覆盖界面。
   * @param id - 所选任务编号。
   * @param selection - 本次选择的顺序编号。
   * @param inline - 在知识问答工作区内查看该任务。
   */
  async function showTask(id: string, selection: number, inline = false): Promise<void> {
    const snapshot = await api<Task>('/tasks/' + id)
    if (selection !== taskSelection || !user.value) return
    if (
      inline &&
      (!knowledgePage.value || snapshot.kind !== 'ask' || snapshot.owner_id !== user.value.id)
    )
      return
    applyTaskSnapshot(snapshot)
    inlineQuestion.value = inline
    taskOpen.value = !inline
  }

  /**
   * 审核点击时选定的任务，并在仍查看该任务时更新详情。
   * @param taskId - 已选任务编号。
   * @param approve - 是否通过审核。
   */
  async function reviewTask(taskId: string, approve: boolean): Promise<void> {
    await action(async () => {
      await api('/tasks/' + taskId + '/review', 'POST', { approve, comment: '' })
      ElMessage.success(approve ? '已通过并发布' : '已驳回')
      if (taskVisible.value && activeTask.value?.id === taskId)
        await showTask(taskId, taskSelection, inlineQuestion.value)
      await load()
    })
  }

  /**
   * 取消正在等待或执行的任务。
   * @param taskId - 目标任务 ID。
   */
  async function cancelTask(taskId?: string): Promise<void> {
    const tid = taskId || activeTask.value?.id
    if (!tid) return
    await action(async () => {
      await api('/tasks/' + tid + '/cancel', 'POST')
      ElMessage.success('已申请取消任务')
      await load()
    })
  }

  /**
   * 重新发起任务执行。
   * @param taskId - 目标任务 ID。
   */
  async function retryTask(taskId?: string): Promise<void> {
    const tid = taskId || activeTask.value?.id
    if (!tid) return
    await action(async () => {
      await api('/tasks/' + tid + '/retry', 'POST')
      if (taskVisible.value && activeTask.value?.id === tid)
        await showTask(tid, taskSelection, inlineQuestion.value)
      ElMessage.success('已重新发起任务')
      await load()
    })
  }

  /**
   * 向运行中任务发送补充要求。
   * @param taskId - 目标任务 ID。
   * @param message - 补充要求正文。
   */
  async function steerTask(taskId: string, message: string): Promise<void> {
    if (!taskId || !message.trim()) return
    await action(async () => {
      await api('/tasks/' + taskId + '/steer', 'POST', { input: message.trim() })
      ElMessage.success('补充要求已发送')
      await load()
    })
  }

  /** 清除会话所属的任务窗口、请求及草稿。 */
  function clearTaskWorkspace(): void {
    taskOpen.value = false
    newQuestion()
  }

  let timer: ReturnType<typeof setInterval>
  onMounted(() => {
    timer = setInterval(async () => {
      if (!user.value || user.value.must_change) return
      const selected = activeTask.value
      if (!taskVisible.value || !selected) return
      try {
        const snapshot = await api<Task>('/tasks/' + selected.id)
        if (!taskVisible.value || activeTask.value !== selected) return
        applyTaskSnapshot(snapshot)
      } catch {
        if (taskVisible.value && activeTask.value === selected) {
          taskOpen.value = false
          newQuestion()
          ElMessage.warning('暂时无法复核当前任务，请从最新任务列表重新打开。')
          await action(load)
        }
      }
    }, 2500)
  })
  onScopeDispose(() => clearInterval(timer))

  return {
    taskOpen,
    activeTask,
    taskDraft,
    inlineQuestion,
    visibleTasks,
    newQuestion,
    selectQuestion,
    submitQuestion,
    openTask,
    reviewTask,
    cancelTask,
    retryTask,
    steerTask,
    clearTaskWorkspace,
  }
}
