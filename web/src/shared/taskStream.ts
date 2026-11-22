/** 统一任务进度 SSE 事件流订阅载荷 */
interface TaskStreamEventRow {
  id?: number
  stage?: string
  progress?: number
  status?: string
}

/** 统一任务 SSE 订阅回调契约 */
interface TaskStreamHandlers {
  onMessage: (row: TaskStreamEventRow) => void
  onResult?: (text: string) => void
  onDone?: () => void
  onError?: () => void
}

/** 终态任务轮询配置与回调契约 */
interface TaskPollingOptions<T extends { state: string }> {
  fetchTask: () => Promise<T>
  maxAttempts?: number
  signal?: AbortSignal
  onTick?: (task: T) => void
}

const terminalStates = new Set(['completed', 'review', 'failed', 'cancelled', 'rejected'])

/**
 * 在 SSE 断连或降级时按固定间隔轮询任务直至进入终态。
 * @param options - 拉取函数、重试上限、取消信号与周期回调。
 * @returns 进入终态后的最新任务对象；超过最大轮询次数且始终失败时返回 null。
 */
export async function pollTaskUntilTerminal<T extends { state: string }>(
  options: TaskPollingOptions<T>,
): Promise<T | null> {
  const maxAttempts = options.maxAttempts ?? 45
  let latest: T | null = null

  for (let i = 0; i < maxAttempts && !options.signal?.aborted; i++) {
    if (i > 0) {
      await new Promise((resolve) => setTimeout(resolve, 2000))
    }
    try {
      if (options.signal?.aborted) return null
      latest = await options.fetchTask()
      if (options.signal?.aborted) return null
      options.onTick?.(latest)
      if (terminalStates.has(latest.state)) {
        return latest
      }
    } catch {}
  }
  return latest
}

/**
 * 解析任务流的 JSON 对象，忽略格式损坏的单条消息。
 * @param event - 进度或结果事件。
 * @returns 已解码的事件载荷。
 */
function eventData(event: Event): Record<string, unknown> | null {
  try {
    const data: unknown = JSON.parse((event as MessageEvent<string>).data)
    return typeof data === 'object' && data !== null && !Array.isArray(data)
      ? (data as Record<string, unknown>)
      : null
  } catch {
    return null
  }
}

/**
 * 订阅任务的进度和正文增量，按执行版本隔离草稿，用事件编号去重。
 * @param taskId - 待监听的任务编号。
 * @param handlers - 更新进度、正文、终态和异常的回调。
 * @returns 停止接收任务事件的清理函数。
 */
export function openTaskEventStream(taskId: string, handlers: TaskStreamHandlers): () => void {
  const source = new EventSource(`/api/tasks/${encodeURIComponent(taskId)}/events`)
  let closed = false
  let runVersion = 0
  let lastDeltaId = 0
  let draft = ''

  /** 关闭订阅并释放已缓冲的正文。 */
  function close(): void {
    closed = true
    draft = ''
    source.close()
  }

  /**
   * 切换执行版本时清空前一次尝试的草稿，丢弃迟到的旧版本事件。
   * @param version - 事件携带的执行版本。
   * @returns 当前订阅是否可以接收本条事件。
   */
  function selectRun(version: unknown): boolean {
    if (
      closed ||
      typeof version !== 'number' ||
      !Number.isSafeInteger(version) ||
      version < 0 ||
      version < runVersion
    )
      return false
    if (runVersion !== version) {
      runVersion = version
      draft = ''
      lastDeltaId = 0
      handlers.onResult?.('')
    }
    return true
  }

  /**
   * 将阶段消息交给当前执行的订阅者。
   * @param event - 原生 SSE 阶段消息。
   */
  source.onmessage = (event) => {
    const row = eventData(event)
    if (!row || !selectRun(row.run_version) || typeof row.stage !== 'string') return
    handlers.onMessage({
      id: typeof row.id === 'number' ? row.id : undefined,
      stage: row.stage,
      progress: typeof row.progress === 'number' ? row.progress : undefined,
      status: typeof row.status === 'string' ? row.status : undefined,
    })
  }
  source.addEventListener('result_delta', (event) => {
    const row = eventData(event)
    if (
      !row ||
      !selectRun(row.run_version) ||
      typeof row.text !== 'string' ||
      typeof row.id !== 'number' ||
      !Number.isSafeInteger(row.id) ||
      row.id <= lastDeltaId
    )
      return
    lastDeltaId = row.id
    draft += row.text
    handlers.onResult?.(draft)
  })
  source.addEventListener('result_reset', (event) => {
    const row = eventData(event)
    if (!row || !selectRun(row.run_version)) return
    if (typeof row.id === 'number') {
      if (row.id <= lastDeltaId) return
      lastDeltaId = row.id
    }
    draft = ''
    handlers.onResult?.('')
  })
  source.addEventListener('result_resume', (event) => {
    const row = eventData(event)
    if (!closed && draft && row?.run_version === runVersion) handlers.onResult?.(draft)
  })
  source.addEventListener('done', (event) => {
    const row = eventData(event)
    if (row?.id !== taskId || !selectRun(row.run_version)) return
    close()
    handlers.onResult?.('')
    handlers.onDone?.()
  })
  /** 断线时隐藏草稿，保留续传所需的缓冲区。 */
  source.onerror = () => {
    if (closed) return
    handlers.onResult?.('')
    if (source.readyState === EventSource.CLOSED) {
      close()
      handlers.onError?.()
    }
  }
  return close
}
