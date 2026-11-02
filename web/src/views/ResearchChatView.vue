<script setup lang="ts">
import { ref, computed, onMounted, onUnmounted, nextTick } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import type { User } from '../types'
import robotUrl from '../assets/robot.svg'

interface ToolCallTrace {
  name: string
  description: string
  data_count?: number
}

interface SubagentState {
  id?: string
  role: string
  type: string
  state: string
  elapsed?: number
  tools?: ToolCallTrace[]
  scratch_files?: string[]
  brief?: string
}

interface MemoryFact {
  subject: string
  predicate: string
  object: string
  confidence?: number
}

interface ResearchTaskResult {
  report?: string
  reply?: string
  outline?: string[]
  subagents?: SubagentState[]
  turn_type?: string
  context_summary?: string
  context_tokens?: number
  is_compacted?: boolean
  memory_facts?: MemoryFact[]
  keyword?: string
  elapsed?: number
}

interface ResearchTurn {
  id: string
  question: string
  created_at: string
  state: 'queued' | 'running' | 'completed' | 'failed'
  stage: string
  progress: number
  thinkingLogs: string[]
  thinkingSeconds?: number
  result?: ResearchTaskResult
}

interface ConversationItem {
  conversation_id: string
  title: string
  turns: number
  started_at: string
  last_active: string
  latest_state: string
}

interface ConversationTurnDto {
  id: string
  question: string
  created_at: string
  state: 'queued' | 'running' | 'completed' | 'failed'
  stage: string
  progress: number
  elapsed?: number
  thinking_logs?: string[]
  result?: ResearchTaskResult
}

interface ConversationDetailResponse {
  conversation_id: string
  keyword: string
  turns: ConversationTurnDto[]
  context_summary: string
  context_tokens: number
  is_compacted: boolean
  memory_facts: MemoryFact[]
}

interface FormattedThinkingStep {
  type: 'agent' | 'turn' | 'tool' | 'data' | 'security' | 'steering' | 'done' | 'thought' | 'plain'
  label: string
  icon: string
  content: string
}

const props = defineProps<{
  user?: User | null
  api: <T>(path: string, method?: string, payload?: unknown) => Promise<T>
}>()

const STORAGE_ACTIVE_KEY = 'ecom_active_research_conv'

const conversations = ref<ConversationItem[]>([])
const activeConvId = ref<string>('')
const currentKeyword = ref<string>('')
const turns = ref<ResearchTurn[]>([])
const inputPrompt = ref<string>('')
const submitting = ref<boolean>(false)
const activeRunningTaskId = ref<string>('')
const thinkingCollapsed = ref<Record<string, boolean>>({})

// 实时计时器
const liveTimerSeconds = ref<number>(0)
let timerHandle: number | null = null
let activeEventSource: EventSource | null = null

const chatScrollRef = ref<HTMLDivElement | null>(null)
const textareaRef = ref<HTMLTextAreaElement | null>(null)

const presetKeywords = ['智能保温杯', '磁吸支架手机壳', '便携式充电宝', '防窥钢化膜']

const currentTurnCount = computed(() => turns.value.length)

/**
 * 将原始步骤日志解析为结构化标签与内容。
 * @param log - 原始日志字符串
 * @returns 格式化后的步骤对象
 */
function parseStep(log: string): FormattedThinkingStep {
  if (log.startsWith('[agent_start]')) {
    return { type: 'agent', label: '智能体启动', icon: '🤖', content: log.slice(13).trim() }
  }
  if (log.startsWith('[turn_start]')) {
    return { type: 'turn', label: '轮次迭代', icon: '🔄', content: log.slice(12).trim() }
  }
  if (log.startsWith('[tool_execution_start]')) {
    return { type: 'tool', label: '调度工具', icon: '🛠️', content: log.slice(22).trim() }
  }
  if (log.startsWith('[tool_execution_end]')) {
    return { type: 'data', label: '数据返回', icon: '📊', content: log.slice(20).trim() }
  }
  if (log.startsWith('[tool_result]')) {
    return { type: 'security', label: '结果清洗', icon: '🛡️', content: log.slice(13).trim() }
  }
  if (log.includes('用户中途追加干预指令') || log.startsWith('[steering]')) {
    return {
      type: 'steering',
      label: '动态纠偏',
      icon: '⚡',
      content: log.replace(/^\[steering\]\s*/, '').trim(),
    }
  }
  if (log.startsWith('[agent_end]')) {
    return { type: 'done', label: '阶段交付', icon: '📦', content: log.slice(11).trim() }
  }
  if (log.startsWith('思考\n') || log.startsWith('💭 ')) {
    return {
      type: 'thought',
      label: '模型思考',
      icon: '💭',
      content: log
        .replace(/^思考\n/, '')
        .replace(/^💭\s*/, '')
        .trim(),
    }
  }
  return { type: 'plain', label: '过程状态', icon: '🔹', content: log }
}

/**
 * 启动思考计时器，每 100ms 刷新一次已思考秒数。
 */
function startThinkingTimer(): void {
  stopThinkingTimer()
  liveTimerSeconds.value = 0
  timerHandle = window.setInterval(() => {
    liveTimerSeconds.value = Number((liveTimerSeconds.value + 0.1).toFixed(1))
  }, 100)
}

/**
 * 停止思考计时器并清理定时器句柄。
 */
function stopThinkingTimer(): void {
  if (timerHandle !== null) {
    clearInterval(timerHandle)
    timerHandle = null
  }
}

/**
 * 将聊天消息列表平滑滚动到底部最新内容。
 */
function scrollToBottom(): void {
  void nextTick(() => {
    if (chatScrollRef.value) {
      chatScrollRef.value.scrollTop = chatScrollRef.value.scrollHeight
    }
  })
}

/**
 * 切换指定轮次思考过程的展开/折叠状态。
 * @param turnId - 轮次任务标识
 */
function toggleThinking(turnId: string): void {
  thinkingCollapsed.value[turnId] = !thinkingCollapsed.value[turnId]
}

/**
 * 拉取历史调研会话列表。
 * @returns 异步完成后无返回值
 */
async function loadConversations(): Promise<void> {
  try {
    const res = await props.api<{ items: ConversationItem[] }>('/research/conversations')
    conversations.value = res.items || []
    const savedId = localStorage.getItem(STORAGE_ACTIVE_KEY)
    if (savedId && conversations.value.some((c) => c.conversation_id === savedId)) {
      await selectConversation(savedId)
    } else if (conversations.value.length > 0) {
      await selectConversation(conversations.value[0].conversation_id)
    } else {
      startNewConversation()
    }
  } catch {
    // 首次无数据时静默忽略
  }
}

/**
 * 切换并加载选中会话的多轮对话记录。
 * @param convId - 目标会话标识
 * @returns 异步完成后无返回值
 */
async function selectConversation(convId: string): Promise<void> {
  if (activeEventSource) {
    activeEventSource.close()
    activeEventSource = null
  }
  stopThinkingTimer()
  activeConvId.value = convId
  localStorage.setItem(STORAGE_ACTIVE_KEY, convId)
  try {
    const res = await props.api<ConversationDetailResponse>(`/research/conversation/${convId}`)
    turns.value = (res.turns || []).map((t) => ({
      id: t.id,
      question: t.question,
      created_at: t.created_at,
      state: t.state,
      stage: t.stage,
      progress: t.progress,
      thinkingLogs: t.thinking_logs || (t.stage ? [t.stage] : []),
      thinkingSeconds: t.result?.elapsed || t.elapsed || 0,
      result: t.result,
    }))
    currentKeyword.value = res.keyword || ''
    scrollToBottom()
  } catch {
    ElMessage.error('加载会话失败')
  }
}

/**
 * 新建一个调研会话并清空当前对话区。
 * @param initKeyword - 可选初始关键词
 */
function startNewConversation(initKeyword?: string): void {
  if (activeEventSource) {
    activeEventSource.close()
    activeEventSource = null
  }
  stopThinkingTimer()
  const newId = 'conv_' + Date.now().toString(36)
  activeConvId.value = newId
  localStorage.setItem(STORAGE_ACTIVE_KEY, newId)
  currentKeyword.value = initKeyword || ''
  turns.value = []
  inputPrompt.value = initKeyword || ''
  void nextTick(() => {
    textareaRef.value?.focus()
  })
}

/**
 * 删除指定的调研会话。
 * @param convId - 待删除会话标识
 * @param e - 鼠标点击事件
 * @returns 异步完成后无返回值
 */
async function deleteConversation(convId: string, e: MouseEvent): Promise<void> {
  e.stopPropagation()
  try {
    await ElMessageBox.confirm('确定要删除此调研对话吗？删除后将无法恢复。', '删除对话', {
      confirmButtonText: '确定删除',
      cancelButtonText: '取消',
      type: 'warning',
    })
    await props.api(`/research/conversation/${convId}`, 'DELETE')
    ElMessage.success('对话已删除')
    conversations.value = conversations.value.filter((c) => c.conversation_id !== convId)
    if (activeConvId.value === convId) {
      if (conversations.value.length > 0) {
        await selectConversation(conversations.value[0].conversation_id)
      } else {
        startNewConversation()
      }
    }
  } catch {
    // 取消删除
  }
}

/**
 * 建立 SSE 连接，实时接收思考推演事件。
 * @param taskId - 任务 ID
 * @param targetTurn - 当前轮次本地响应式状态引用
 * @returns 异步完成后无返回值
 */
async function streamTaskEvents(taskId: string, targetTurn: ResearchTurn): Promise<void> {
  startThinkingTimer()
  if (activeEventSource) {
    activeEventSource.close()
    activeEventSource = null
  }

  return new Promise<void>((resolve) => {
    const es = new EventSource(`/api/tasks/${taskId}/events`)
    activeEventSource = es

    /**
     * 处理服务端推来的流式阶段与思考日志事件。
     * @param e - 消息事件对象
     */
    function handleMessage(e: MessageEvent): void {
      try {
        const payload = JSON.parse(e.data as string) as {
          stage?: string
          progress?: number
          status?: string
        }
        if (payload.stage) {
          targetTurn.stage = payload.stage
          if (!targetTurn.thinkingLogs.includes(payload.stage)) {
            targetTurn.thinkingLogs.push(payload.stage)
          }
        }
        if (typeof payload.progress === 'number') {
          targetTurn.progress = payload.progress
        }
        scrollToBottom()
      } catch {
        // 忽略心跳解析错误
      }
    }
    es.onmessage = handleMessage

    /**
     * 处理智能调研完成事件，拉取终态研报。
     */
    function handleDone(): void {
      es.close()
      activeEventSource = null
      const elapsed = liveTimerSeconds.value
      stopThinkingTimer()
      targetTurn.thinkingSeconds = elapsed > 0 ? elapsed : targetTurn.thinkingSeconds
      void finalizeTaskResult(taskId, targetTurn)
      resolve()
    }
    es.addEventListener('done', handleDone)

    /**
     * 处理连接异常或意外断开，切换为轮询兜底。
     */
    function handleError(): void {
      es.close()
      activeEventSource = null
      void fallbackPoll(taskId, targetTurn)
      resolve()
    }
    es.onerror = handleError
  })
}

/**
 * 任务完成终态处理，拉取最终研报与工件。
 * @param taskId - 任务 ID
 * @param targetTurn - 目标轮次对象
 */
async function finalizeTaskResult(taskId: string, targetTurn: ResearchTurn): Promise<void> {
  try {
    const task = await props.api<ResearchTurn & { elapsed?: number }>(`/tasks/${taskId}`)
    targetTurn.state = task.state
    targetTurn.stage = task.stage
    targetTurn.progress = task.progress
    targetTurn.result = task.result

    if (task.result?.keyword && !currentKeyword.value) currentKeyword.value = task.result.keyword
    if (task.result?.elapsed) {
      targetTurn.thinkingSeconds = task.result.elapsed
    } else if (task.elapsed) {
      targetTurn.thinkingSeconds = task.elapsed
    } else if (liveTimerSeconds.value > 0) {
      targetTurn.thinkingSeconds = liveTimerSeconds.value
    }

    await loadConversations()
    scrollToBottom()
  } catch {
    ElMessage.error('获取结果失败')
  }
}

/**
 * 轮询兜底函数，保障网络抖动下结果能够成功获取。
 * @param taskId - 任务 ID
 * @param targetTurn - 目标轮次对象
 */
async function fallbackPoll(taskId: string, targetTurn: ResearchTurn): Promise<void> {
  for (let i = 0; i < 45; i++) {
    await new Promise((r) => setTimeout(r, 2000))
    try {
      const task = await props.api<ResearchTurn & { elapsed?: number }>(`/tasks/${taskId}`)
      targetTurn.state = task.state
      targetTurn.stage = task.stage
      targetTurn.progress = task.progress
      if (task.stage && !targetTurn.thinkingLogs.includes(task.stage)) {
        targetTurn.thinkingLogs.push(task.stage)
      }
      scrollToBottom()
      if (task.state === 'completed') {
        stopThinkingTimer()
        if (task.result?.elapsed) {
          targetTurn.thinkingSeconds = task.result.elapsed
        } else if (liveTimerSeconds.value > 0) {
          targetTurn.thinkingSeconds = liveTimerSeconds.value
        }
        await finalizeTaskResult(taskId, targetTurn)
        break
      }
      if (task.state === 'failed') {
        stopThinkingTimer()
        ElMessage.error(task.stage || '任务异常')
        break
      }
    } catch {
      // 忽略单次网络抖动
    }
  }
}

/**
 * 发送用户提问或追问。
 * @returns 异步完成后无返回值
 */
/**
 * 运行期向正在执行的智能体追加中途干预指令 (Steering)。
 */
async function handleSteer(): Promise<void> {
  const steerText = inputPrompt.value.trim()
  if (!steerText || !activeRunningTaskId.value) return

  try {
    await props.api(`/tasks/${activeRunningTaskId.value}/steer`, 'POST', {
      input: steerText,
    })
    const lastTurn = turns.value[turns.value.length - 1]
    if (lastTurn) {
      lastTurn.thinkingLogs.push(`【用户中途追加干预指令】：${steerText}`)
    }
    ElMessage.success('中途干预指令已发送，正在指引智能体实时转向！')
    inputPrompt.value = ''
    scrollToBottom()
  } catch (err: unknown) {
    const msg = err instanceof Error ? err.message : '发送干预指令失败'
    ElMessage.error(msg)
  }
}

/**
 * 发送用户提问或中途追加干预指令。
 * @returns 异步完成后无返回值
 */
async function handleSend(): Promise<void> {
  const prompt = inputPrompt.value.trim()
  if (!prompt) return

  if (submitting.value && activeRunningTaskId.value) {
    await handleSteer()
    return
  }

  if (submitting.value) return

  const isFirstTurn = turns.value.length === 0
  const kw = isFirstTurn ? currentKeyword.value || prompt : currentKeyword.value
  currentKeyword.value = kw

  // 乐观在左侧历史列表中显示当前会话
  if (isFirstTurn && !conversations.value.some((c) => c.conversation_id === activeConvId.value)) {
    conversations.value.unshift({
      conversation_id: activeConvId.value,
      title: kw,
      turns: 1,
      started_at: new Date().toISOString(),
      last_active: new Date().toISOString(),
      latest_state: 'running',
    })
  }

  const tempTurn: ResearchTurn = {
    id: 'tmp_' + Date.now().toString(),
    question: prompt,
    created_at: new Date().toISOString(),
    state: 'running',
    stage: '正在启动调研协同...',
    progress: 5,
    thinkingLogs: [],
  }
  turns.value.push(tempTurn)
  inputPrompt.value = ''
  submitting.value = true
  scrollToBottom()

  try {
    const res = await props.api<{ id: string }>('/tasks', 'POST', {
      question: prompt,
      kind: 'research',
      conversation_id: activeConvId.value,
      keyword: kw,
      platforms: ['web', 'p1688'],
      input_level: 1,
    })
    tempTurn.id = res.id
    activeRunningTaskId.value = res.id
    await streamTaskEvents(res.id, tempTurn)
  } catch (err: unknown) {
    stopThinkingTimer()
    tempTurn.state = 'failed'
    const msg = err instanceof Error ? err.message : '提交失败'
    tempTurn.stage = msg
    ElMessage.error(msg)
  } finally {
    submitting.value = false
    activeRunningTaskId.value = ''
    scrollToBottom()
  }
}

/**
 * 监听键盘回车事件发送消息。
 * @param e - 键盘事件对象
 */
function handleKeydown(e: KeyboardEvent): void {
  if (e.key === 'Enter' && !e.shiftKey) {
    e.preventDefault()
    void handleSend()
  }
}

/**
 * 选择热门品类预设填入并发送。
 * @param kw - 品类名称
 */
function choosePreset(kw: string): void {
  inputPrompt.value = kw
  void handleSend()
}

/**
 * 复制指定文本内容至剪贴板。
 * @param text - 待复制文本
 * @returns 异步完成后无返回值
 */
async function copyText(text?: string): Promise<void> {
  if (!text) return
  try {
    await navigator.clipboard.writeText(text)
    ElMessage.success('已复制到剪贴板')
  } catch {
    ElMessage.warning('复制失败')
  }
}

onMounted(() => {
  void loadConversations()
})

onUnmounted(() => {
  if (activeEventSource) {
    activeEventSource.close()
    activeEventSource = null
  }
  stopThinkingTimer()
})
</script>

<template>
  <div class="chat-container-layout">
    <!-- ── 左侧极简会话历史列表 ─────────────────────────────────── -->
    <aside class="chat-history-sidebar">
      <div class="sidebar-top">
        <button class="new-conversation-btn" @click="startNewConversation()">
          <span class="btn-icon">＋</span> 新建调研对话
        </button>
      </div>

      <div class="conversations-scroll-list">
        <div class="list-section-title">历史对话</div>
        <div
          v-for="c in conversations"
          :key="c.conversation_id"
          class="conversation-item-btn"
          :class="{ active: c.conversation_id === activeConvId }"
          @click="selectConversation(c.conversation_id)"
        >
          <div class="item-main-info">
            <span class="item-title">{{ c.title || '新对话' }}</span>
            <span class="item-turns">{{ c.turns }} 轮</span>
          </div>
          <button
            class="delete-conv-btn"
            title="删除对话"
            @click="deleteConversation(c.conversation_id, $event)"
          >
            ×
          </button>
        </div>
        <div v-if="conversations.length === 0" class="empty-list-hint">暂无历史对话</div>
      </div>
    </aside>

    <!-- ── 中间标准自然聊天窗口 ────────────────────────────────── -->
    <main class="chat-conversation-stage">
      <!-- 顶部简明标题条 -->
      <header class="conversation-header-bar">
        <div class="header-info">
          <span class="current-topic-name">{{ currentKeyword || '智能调研' }}</span>
          <span v-if="currentTurnCount > 0" class="turn-count-badge"
            >共 {{ currentTurnCount }} 轮问答</span
          >
        </div>
      </header>

      <!-- 消息流滚动区 -->
      <div ref="chatScrollRef" class="messages-scroll-area">
        <div class="messages-center-wrapper">
          <!-- 空白状态欢迎 (居中高清淘智机器人大图标) -->
          <div v-if="turns.length === 0" class="chat-welcome-box">
            <div class="welcome-robot-wrapper">
              <img :src="robotUrl" alt="淘智机器人" class="welcome-robot-large" />
            </div>
            <div class="welcome-title">有什么电商爆品想调研？</div>
            <div class="welcome-subtitle">
              输入品类名称，专职智能体将实时检索 1688 源头工厂出厂价、全网零售价带并交叉验真。
            </div>
            <div class="preset-suggestions">
              <button
                v-for="kw in presetKeywords"
                :key="kw"
                class="preset-suggestion-btn"
                @click="choosePreset(kw)"
              >
                {{ kw }}
              </button>
            </div>
          </div>

          <!-- 问答流 -->
          <div v-for="turn in turns" :key="turn.id" class="message-turn-group">
            <!-- 用户问题 (靠右) -->
            <div class="user-query-container">
              <div class="user-query-bubble">
                {{ turn.question }}
              </div>
            </div>

            <!-- 助手回答 (靠左，标准排版) -->
            <div class="assistant-response-container">
              <img :src="robotUrl" alt="淘智机器人" class="assistant-avatar-img" />
              <div class="assistant-content-block">
                <!-- 1. 运行中流式思考过程提示 -->
                <div
                  v-if="turn.state === 'running' || turn.state === 'queued'"
                  class="active-thinking-block"
                >
                  <div class="active-thinking-status">
                    <span class="thinking-spinner" />
                    <span class="thinking-label">思考中 (已用时 {{ liveTimerSeconds }}s)...</span>
                  </div>
                  <div class="live-steps-stream">
                    <div
                      v-for="(log, idx) in turn.thinkingLogs"
                      :key="idx"
                      class="trace-step-entry"
                      :class="[
                        { current: idx === turn.thinkingLogs.length - 1 },
                        parseStep(log).type,
                      ]"
                    >
                      <div class="trace-step-header">
                        <span class="trace-step-icon">{{ parseStep(log).icon }}</span>
                        <span class="trace-step-type">{{ parseStep(log).label }}</span>
                      </div>
                      <div class="trace-step-text">{{ parseStep(log).content }}</div>
                    </div>
                  </div>
                </div>

                <!-- 2. 完成后思考过程折叠栏 -->
                <div v-else-if="turn.thinkingLogs?.length" class="completed-thinking-accordion">
                  <div class="accordion-header" @click="toggleThinking(turn.id)">
                    <span class="accordion-arrow">{{
                      thinkingCollapsed[turn.id] ? '▾' : '▸'
                    }}</span>
                    <span class="accordion-title">
                      思考过程 (用时 {{ turn.thinkingSeconds || turn.result?.elapsed || 0 }}s)
                    </span>
                  </div>
                  <div v-show="thinkingCollapsed[turn.id]" class="accordion-body">
                    <div
                      v-for="(log, idx) in turn.thinkingLogs"
                      :key="idx"
                      class="trace-step-entry"
                    >
                      <div class="trace-step-header">
                        <span class="trace-step-icon">{{ parseStep(log).icon }}</span>
                        <span class="trace-step-type">{{ parseStep(log).label }}</span>
                      </div>
                      <div class="trace-step-text">{{ parseStep(log).content }}</div>
                    </div>
                  </div>
                </div>

                <!-- 3. 回答正文 (Markdown 渲染) -->
                <div
                  v-if="turn.result?.report || turn.result?.reply"
                  class="response-markdown-body"
                >
                  {{ turn.result?.report || turn.result?.reply }}
                </div>

                <!-- 4. 底部简单操作栏 -->
                <div v-if="turn.result?.report || turn.result?.reply" class="response-actions-row">
                  <button
                    class="action-btn"
                    @click="copyText(turn.result?.report || turn.result?.reply)"
                  >
                    复制全文
                  </button>
                </div>

                <!-- 5. 失败提示 -->
                <div v-if="turn.state === 'failed'" class="error-hint-row">
                  {{ turn.stage || '抱歉，当前调研任务出现异常，请稍后重试。' }}
                </div>
              </div>
            </div>
          </div>
        </div>
      </div>

      <!-- 底部标准居中输入栏 -->
      <footer class="conversation-input-footer">
        <div class="input-center-container">
          <div class="normal-chat-input-box">
            <textarea
              ref="textareaRef"
              v-model="inputPrompt"
              class="pure-textarea"
              :rows="2"
              :placeholder="
                submitting
                  ? '调研正在进行中，可输入中途干预指令（例如：只看1688源头厂、重点对比亚马逊）按 Enter 追加...'
                  : turns.length === 0
                    ? '输入要调研的商品类目或问题...'
                    : '继续针对上述内容追问...'
              "
              @keydown="handleKeydown"
            />
            <button
              :class="['send-circle-btn', { 'steer-mode': submitting }]"
              :disabled="!inputPrompt.trim()"
              :title="submitting ? '追加中途干预指令 (Steering)' : '发送消息'"
              @click="handleSend"
            >
              <svg v-if="!submitting" viewBox="0 0 24 24" class="send-svg-icon">
                <path d="M2.01 21L23 12 2.01 3 2 10l15 2-15 2z" fill="currentColor" />
              </svg>
              <svg v-else viewBox="0 0 24 24" class="send-svg-icon" style="color: #d97706">
                <path
                  d="M12 2C6.48 2 2 6.48 2 12s4.48 10 10 10 10-4.48 10-10S17.52 2 12 2zm1 17.93c-3.95-.49-7-3.85-7-7.93 0-.62.08-1.21.21-1.79L9 15v1c0 1.1.9 2 2 2v1.93zm6.9-2.54c-.26-.81-1-1.39-1.9-1.39h-1v-3c0-.55-.45-1-1-1H8v-2h2c.55 0 1-.45 1-1V7h2c1.1 0 2-.9 2-2v-.41c2.93 1.19 5 4.06 5 7.41 0 2.08-.8 3.97-2.1 5.39z"
                  fill="currentColor"
                />
              </svg>
            </button>
          </div>
          <div class="input-bottom-tips">按 Enter 发送，Shift + Enter 换行</div>
        </div>
      </footer>
    </main>
  </div>
</template>

<style scoped>
.chat-container-layout {
  display: flex;
  height: calc(100vh - 72px);
  background: #ffffff;
  color: #1e293b;
  overflow: hidden;
}

.send-circle-btn.steer-mode {
  background: #fef3c7;
  border-color: #f59e0b;
}
.send-circle-btn.steer-mode:hover {
  background: #fde68a;
}
.trace-step-entry.steering .trace-step-type {
  color: #d97706;
}
.trace-step-entry.steering .trace-step-text {
  color: #b45309;
  font-weight: 600;
}

.trace-step-header {
  display: flex;
  align-items: center;
  gap: 6px;
  margin-bottom: 3px;
}
.trace-step-icon {
  font-size: 13px;
}
.trace-step-entry.agent .trace-step-type {
  color: #2563eb;
}
.trace-step-entry.turn .trace-step-type {
  color: #475569;
}
.trace-step-entry.tool .trace-step-type {
  color: #7c3aed;
}
.trace-step-entry.data .trace-step-type {
  color: #0284c7;
}
.trace-step-entry.security .trace-step-type {
  color: #059669;
}
.trace-step-entry.done .trace-step-type {
  color: #16a34a;
}

/* ── 左侧简洁历史栏 ─────────────────────────────── */
.chat-history-sidebar {
  width: 240px;
  background: #f8fafc;
  border-right: 1px solid #e2e8f0;
  display: flex;
  flex-direction: column;
}
.sidebar-top {
  padding: 16px;
  border-bottom: 1px solid #eef2f6;
}
.new-conversation-btn {
  width: 100%;
  padding: 9px 14px;
  background: #ffffff;
  border: 1px solid #cbd5e1;
  border-radius: 8px;
  font-size: 13px;
  font-weight: 500;
  color: #1e293b;
  cursor: pointer;
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 6px;
  transition: all 0.15s;
}
.new-conversation-btn:hover {
  background: #f1f5f9;
  border-color: #94a3b8;
}
.btn-icon {
  font-size: 16px;
  line-height: 1;
}

.conversations-scroll-list {
  flex: 1;
  overflow-y: auto;
  padding: 12px 10px;
}
.list-section-title {
  font-size: 11px;
  font-weight: 600;
  color: #94a3b8;
  padding: 4px 8px 8px;
  letter-spacing: 0.5px;
}
.conversation-item-btn {
  width: 100%;
  padding: 8px 10px;
  border-radius: 6px;
  background: transparent;
  border: none;
  cursor: pointer;
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 4px;
  text-align: left;
  transition: background 0.15s;
}
.conversation-item-btn:hover {
  background: #eef2f6;
}
.conversation-item-btn.active {
  background: #e2e8f0;
  font-weight: 600;
}
.item-main-info {
  display: flex;
  align-items: center;
  justify-content: space-between;
  flex: 1;
  min-width: 0;
  margin-right: 6px;
}
.item-title {
  font-size: 13px;
  color: #334155;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.item-turns {
  font-size: 11px;
  color: #94a3b8;
  flex-shrink: 0;
  margin-left: 6px;
}
.delete-conv-btn {
  background: transparent;
  border: none;
  color: #94a3b8;
  font-size: 16px;
  line-height: 1;
  padding: 2px 6px;
  border-radius: 4px;
  cursor: pointer;
  opacity: 0.6;
  transition: all 0.15s;
}
.delete-conv-btn:hover {
  opacity: 1;
  color: #ef4444;
  background: #fee2e2;
}
.empty-list-hint {
  font-size: 12px;
  color: #94a3b8;
  text-align: center;
  padding: 24px 8px;
}

/* ── 中间聊天舞台 ──────────────────────────────── */
.chat-conversation-stage {
  flex: 1;
  display: flex;
  flex-direction: column;
  background: #ffffff;
  position: relative;
}

.conversation-header-bar {
  height: 52px;
  padding: 0 28px;
  border-bottom: 1px solid #f1f5f9;
  display: flex;
  align-items: center;
  justify-content: space-between;
}
.current-topic-name {
  font-size: 14.5px;
  font-weight: 600;
  color: #0f172a;
}
.turn-count-badge {
  font-size: 12px;
  color: #64748b;
  margin-left: 10px;
}

/* 消息流 (居中 820px 宽度) */
.messages-scroll-area {
  flex: 1;
  overflow-y: auto;
  padding: 24px 20px 140px;
}
.messages-center-wrapper {
  max-width: 820px;
  margin: 0 auto;
}

/* 空白欢迎区 */
.chat-welcome-box {
  margin: 60px auto 40px;
  text-align: center;
  max-width: 560px;
}
.welcome-robot-wrapper {
  display: flex;
  justify-content: center;
  margin-bottom: 16px;
}
.welcome-robot-large {
  width: 96px;
  height: 96px;
  border-radius: 24px;
  object-fit: cover;
  box-shadow: 0 12px 28px -6px rgba(36, 90, 216, 0.28);
  transition: transform 0.3s cubic-bezier(0.34, 1.56, 0.64, 1);
}
.welcome-robot-large:hover {
  transform: scale(1.08) rotate(2deg);
}

.welcome-title {
  font-size: 22px;
  font-weight: 600;
  color: #0f172a;
  margin-bottom: 8px;
}
.welcome-subtitle {
  font-size: 13.5px;
  color: #64748b;
  line-height: 1.6;
  margin-bottom: 24px;
}
.preset-suggestions {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  justify-content: center;
}
.preset-suggestion-btn {
  padding: 6px 14px;
  background: #f8fafc;
  border: 1px solid #e2e8f0;
  border-radius: 16px;
  font-size: 12.5px;
  color: #475569;
  cursor: pointer;
  transition: all 0.15s;
}
.preset-suggestion-btn:hover {
  background: #f1f5f9;
  border-color: #cbd5e1;
  color: #0f172a;
}

/* 问答对话流 */
.message-turn-group {
  margin-bottom: 32px;
}

/* 用户问题 (靠右) */
.user-query-container {
  display: flex;
  justify-content: flex-end;
  margin-bottom: 20px;
}
.user-query-bubble {
  max-width: 80%;
  background: #0f172a;
  color: #ffffff;
  padding: 10px 16px;
  border-radius: 16px 16px 4px 16px;
  font-size: 14px;
  line-height: 1.6;
  word-break: break-word;
}

/* 助手回答 (靠左，带放大头像) */
.assistant-response-container {
  display: flex;
  align-items: flex-start;
  gap: 14px;
}
.assistant-avatar-img {
  width: 44px;
  height: 44px;
  border-radius: 13px;
  object-fit: cover;
  flex-shrink: 0;
  margin-top: 2px;
  box-shadow: 0 3px 10px rgba(15, 23, 42, 0.12);
  transition: transform 0.2s cubic-bezier(0.34, 1.56, 0.64, 1);
}
.assistant-avatar-img:hover {
  transform: scale(1.08);
}
.assistant-content-block {
  flex: 1;
  min-width: 0;
}

/* 运行中思考块 */
.active-thinking-block {
  background: #f8fafc;
  border: 1px solid #e2e8f0;
  border-radius: 8px;
  padding: 12px 14px;
  margin-bottom: 14px;
}
.active-thinking-status {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 12.5px;
  color: #64748b;
  font-weight: 500;
  margin-bottom: 10px;
}
.thinking-spinner {
  width: 12px;
  height: 12px;
  border: 2px solid #cbd5e1;
  border-top-color: #475569;
  border-radius: 50%;
  animation: spin 0.8s linear infinite;
}
@keyframes spin {
  to {
    transform: rotate(360deg);
  }
}

/* 思考与运行工具步骤排版 */
.live-steps-stream {
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.trace-step-entry {
  display: flex;
  flex-direction: column;
}
.trace-step-type {
  font-size: 11.5px;
  font-weight: 700;
  color: #64748b;
  letter-spacing: 0.5px;
  margin-bottom: 2px;
}
.trace-step-text {
  font-size: 12.5px;
  color: #334155;
  line-height: 1.5;
  white-space: pre-wrap;
  word-break: break-word;
}
.trace-step-entry.current .trace-step-type {
  color: #0f172a;
}
.trace-step-entry.current .trace-step-text {
  color: #0f172a;
  font-weight: 500;
}

/* 完成后思考折叠条 */
.completed-thinking-accordion {
  margin-bottom: 14px;
}
.accordion-header {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  font-size: 12px;
  color: #94a3b8;
  cursor: pointer;
  user-select: none;
  padding: 3px 6px;
  border-radius: 4px;
  transition: background 0.15s;
}
.accordion-header:hover {
  background: #f1f5f9;
  color: #64748b;
}
.accordion-arrow {
  font-size: 11px;
}
.accordion-body {
  margin-top: 6px;
  padding: 12px 14px;
  background: #f8fafc;
  border-radius: 6px;
  border: 1px solid #f1f5f9;
  display: flex;
  flex-direction: column;
  gap: 8px;
}

/* Markdown 回答正文 */
.response-markdown-body {
  font-size: 14.5px;
  line-height: 1.8;
  color: #1e293b;
  white-space: pre-wrap;
  word-break: break-word;
  margin-top: 8px;
}

/* 操作行 */
.response-actions-row {
  margin-top: 10px;
  display: flex;
  gap: 8px;
}
.action-btn {
  background: transparent;
  border: none;
  color: #94a3b8;
  font-size: 11.5px;
  cursor: pointer;
  padding: 2px 6px;
  border-radius: 4px;
}
.action-btn:hover {
  background: #f1f5f9;
  color: #475569;
}

.error-hint-row {
  margin-top: 8px;
  font-size: 13px;
  color: #ef4444;
}

/* ── 底部居中输入区域 ──────────────────────────── */
.conversation-input-footer {
  position: absolute;
  bottom: 0;
  left: 0;
  right: 0;
  padding: 12px 20px 20px;
  background: linear-gradient(to top, #ffffff 85%, rgba(255, 255, 255, 0) 100%);
}
.input-center-container {
  max-width: 820px;
  margin: 0 auto;
}
.normal-chat-input-box {
  display: flex;
  align-items: flex-end;
  gap: 8px;
  background: #f8fafc;
  border: 1px solid #cbd5e1;
  border-radius: 12px;
  padding: 10px 12px;
  transition:
    border-color 0.15s,
    background 0.15s;
}
.normal-chat-input-box:focus-within {
  border-color: #64748b;
  background: #ffffff;
}
.pure-textarea {
  flex: 1;
  border: none;
  outline: none;
  background: transparent;
  resize: none;
  font-size: 14px;
  line-height: 1.5;
  color: #0f172a;
  font-family: inherit;
}
.send-circle-btn {
  width: 32px;
  height: 32px;
  border-radius: 50%;
  background: #0f172a;
  color: #ffffff;
  border: none;
  cursor: pointer;
  display: flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
  transition: opacity 0.15s;
}
.send-circle-btn:disabled {
  opacity: 0.3;
  cursor: not-allowed;
}
.send-svg-icon {
  width: 14px;
  height: 14px;
  margin-left: 2px;
}
.input-bottom-tips {
  text-align: center;
  font-size: 11px;
  color: #94a3b8;
  margin-top: 6px;
}
</style>
