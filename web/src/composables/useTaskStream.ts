/** 统一任务进度 SSE 事件流订阅载荷 */
export interface TaskStreamEventRow {
  id?: number
  stage?: string
  progress?: number
  status?: string
}

/** 统一任务 SSE 订阅回调契约 */
export interface TaskStreamHandlers {
  onMessage: (row: TaskStreamEventRow) => void
  onDone?: (payload: { id?: string }) => void
  onError?: () => void
}

/** 终态任务轮询配置与回调契约 */
export interface TaskPollingOptions<T extends { state: string }> {
  fetchTask: () => Promise<T>
  terminalStates?: readonly string[]
  maxAttempts?: number
  intervalMs?: number
  onTick?: (task: T) => void
  onError?: (err: unknown) => void
}

/**
 * 在 SSE 断连或降级时按固定间隔轮询任务直至进入终态。
 * @param options - 拉取函数、终态集合、重试上限与周期回调配置。
 * @returns 进入终态后的最新任务对象；超过最大轮询次数且始终失败时返回 null。
 */
export async function pollTaskUntilTerminal<T extends { state: string }>(
  options: TaskPollingOptions<T>,
): Promise<T | null> {
  const terminalStates = options.terminalStates ?? [
    'completed',
    'review',
    'failed',
    'cancelled',
    'rejected',
  ]
  const maxAttempts = options.maxAttempts ?? 45
  const intervalMs = options.intervalMs ?? 2000
  let latest: T | null = null

  for (let i = 0; i < maxAttempts; i++) {
    if (i > 0) {
      await new Promise((resolve) => setTimeout(resolve, intervalMs))
    }
    try {
      latest = await options.fetchTask()
      options.onTick?.(latest)
      if (terminalStates.includes(latest.state)) {
        return latest
      }
    } catch (err: unknown) {
      options.onError?.(err)
    }
  }
  return latest
}

/**
 * 订阅指定任务的 Server-Sent Events 进度流，并提供显式关闭句柄。
 * @param taskId - 待监听任务唯一标识。
 * @param handlers - 消息、终态与异常降级回调函数集。
 * @returns 可立即关闭底层 EventSource 的清理函数。
 */
export function openTaskEventStream(taskId: string, handlers: TaskStreamHandlers): () => void {
  const source = new EventSource(`/api/tasks/${encodeURIComponent(taskId)}/events`)
  let closed = false

  /**
   * 关闭底层 EventSource 连接并确保幂等。
   */
  const close = (): void => {
    if (closed) return
    closed = true
    source.close()
  }

  /**
   * 解析并转发阶段进展 JSON 消息。
   * @param ev - SSE 原生消息事件。
   */
  source.onmessage = (ev: MessageEvent): void => {
    try {
      const row = JSON.parse(String(ev.data)) as TaskStreamEventRow
      handlers.onMessage(row)
    } catch {
      return
    }
  }

  /**
   * 监听服务端终态 done 信标并关闭连接。
   * @param ev - 终态自定义事件。
   */
  source.addEventListener('done', (ev: Event): void => {
    close()
    let parsed: { id?: string } = {}
    try {
      parsed = JSON.parse(String((ev as MessageEvent).data || '{}')) as { id?: string }
    } catch {
      parsed = {}
    }
    handlers.onDone?.(parsed)
  })

  /**
   * 监听通道断开，交给上层回落轮询。
   */
  source.onerror = (): void => {
    close()
    handlers.onError?.()
  }

  return close
}
