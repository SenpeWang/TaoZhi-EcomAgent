<script setup lang="ts">
import { reactive, ref, computed, onMounted, onUnmounted, nextTick } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import type { ResearchSubagent, Task, TaskEventItem } from './types'
import { stateText } from '../../shared/labels'
import robotUrl from '../../assets/robot.svg'
import MarkdownReport from '../../shared/MarkdownReport.vue'
import { openTaskEventStream, pollTaskUntilTerminal } from '../../shared/taskStream'
import type { ApiClient, CreatedResource, Items } from '../../shared/apiTypes'
import ResearchStageList from './ResearchStageList.vue'

interface ResearchSnapshot extends Pick<
  Task,
  'id' | 'question' | 'created_at' | 'state' | 'stage' | 'progress' | 'restricted' | 'result'
> {
  events: TaskEventItem[]
  elapsed?: number
}

interface ResearchTurn extends ResearchSnapshot {
  draft?: string
  streamError?: boolean
}

interface ConversationItem {
  conversation_id: string
  title: string
  turns: number
}

interface ConversationDetailResponse {
  keyword?: string
  turns: ResearchSnapshot[]
}

const emit = defineEmits<{ (e: 'inspect', taskId: string): void }>()
const props = defineProps<{ api: ApiClient }>()

const STORAGE_ACTIVE_KEY = 'ecom_active_research_conv'

const conversations = ref<ConversationItem[]>([])
const activeConvId = ref<string>('')
const currentKeyword = ref<string>('')
const turns = ref<ResearchTurn[]>([])
const loadingConversation = ref(true)
const inputPrompt = ref<string>('')
const activeTurn = computed(() =>
  turns.value.filter((turn) => ['running', 'queued'].includes(turn.state)).at(-1),
)
const submitting = computed(() => Boolean(activeTurn.value))
const activeRunningTaskId = computed(() =>
  activeTurn.value?.id.startsWith('tmp_') ? '' : activeTurn.value?.id,
)
const thinkingCollapsed = ref<Record<string, boolean>>({})

const clock = ref(Date.now())
let timerHandle: number | null = null
let conversationVersion = 0
const subscriptions = new Map<string, AbortController>()

const chatScrollRef = ref<HTMLDivElement | null>(null)
const textareaRef = ref<HTMLTextAreaElement | null>(null)

const presetKeywords = ['智能保温杯', '磁吸支架手机壳', '便携式充电宝', '防窥钢化膜']

const currentTurnCount = computed(() => turns.value.length)

/**
 * 汇总本轮实际参与调研的业务角色，按照首次出现的顺序展示。
 * @param agents - 当前轮次的角色执行记录。
 * @returns 去重后的角色名称。
 */
function participatingRoleNames(agents: ResearchSubagent[]): string[] {
  const names = new Set<string>()
  for (const agent of agents) names.add(agent.display_name)
  return [...names]
}

/**
 * 读取当前轮次的执行时间，重开会话后继续使用服务端的创建时刻。
 * @param turn - 运行中的调研轮次。
 * @returns 已用秒数。
 */
function elapsedSeconds(turn: ResearchTurn): number {
  return Math.max(0, Math.floor((clock.value - Date.parse(turn.created_at)) / 1000))
}

/** 切换会话时停止该视图的所有网络订阅，并使在途响应失效。 */
function leaveConversation(): void {
  conversationVersion++
  for (const controller of subscriptions.values()) controller.abort()
  subscriptions.clear()
}

/**
 * 发送提问或阅读最新内容时跟随更新，向上翻阅时保留当前位置。
 * @param force - 主动打开会话或发起提问时跳至末尾。
 */
function scrollToBottom(force = true): void {
  const pane = chatScrollRef.value
  if (!force && pane && pane.scrollHeight - pane.clientHeight - pane.scrollTop > 80) return
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
  const version = conversationVersion
  try {
    const res = await props.api<Items<ConversationItem>>('/research/conversations')
    if (version !== conversationVersion) return
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
    if (version === conversationVersion) startNewConversation()
  }
}

/**
 * 切换并加载选中会话的多轮对话记录。
 * @param convId - 目标会话标识
 * @returns 异步完成后无返回值
 */
async function selectConversation(convId: string): Promise<void> {
  leaveConversation()
  const version = conversationVersion
  loadingConversation.value = true
  turns.value = []
  currentKeyword.value = ''
  activeConvId.value = convId
  localStorage.setItem(STORAGE_ACTIVE_KEY, convId)
  try {
    const res = await props.api<ConversationDetailResponse>(`/research/conversation/${convId}`)
    if (version !== conversationVersion) return
    turns.value = res.turns
    currentKeyword.value = res.keyword || ''
    for (const turn of turns.value) {
      if (turn.state === 'queued' || turn.state === 'running') streamTaskEvents(turn)
    }
    scrollToBottom()
  } catch {
    if (version === conversationVersion) {
      ElMessage.error('加载会话失败')
      startNewConversation()
    }
  } finally {
    if (version === conversationVersion) loadingConversation.value = false
  }
}

/**
 * 新建一个调研会话并清空当前对话区。
 * @param initKeyword - 可选初始关键词
 */
function startNewConversation(initKeyword?: string): void {
  leaveConversation()
  loadingConversation.value = false
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
    return
  }
}

/**
 * 订阅本轮的正文与执行状态，回到进行中的会话时从头回放。
 * @param turn - 当前会话中的响应式调研轮次。
 */
function streamTaskEvents(turn: ResearchTurn): void {
  turn.streamError = false
  const taskId = turn.id
  const stageIds = new Set(turn.events.map((event) => event.id))
  subscriptions.get(taskId)?.abort()
  const controller = new AbortController()
  subscriptions.set(taskId, controller)
  const { signal } = controller

  /**
   * 合并服务器确认的状态和最终可见结果。
   * @param task - 经过权限过滤的任务快照。
   */
  function applyTask(task: ResearchSnapshot): void {
    if (signal.aborted) return
    turn.question = task.question
    turn.state = task.state
    turn.stage = task.stage
    turn.progress = task.progress
    turn.result = task.result
    turn.events = task.events
    turn.restricted = task.restricted
    if (turn.restricted) turn.draft = ''
    turn.elapsed = task.result?.elapsed ?? task.elapsed ?? elapsedSeconds(turn)
    if (task.result?.keyword) currentKeyword.value = task.result.keyword
    scrollToBottom(false)
  }

  /**
   * 从权限过滤后的任务接口取回最终结果，永久断线时保留轮询兜底。
   * @param poll - 是否需要等到任务终态。
   */
  async function finish(poll: boolean): Promise<void> {
    try {
      /**
       * 读取此轮任务的权限过滤快照。
       * @returns 调研状态及可展示结果。
       */
      const fetchTask = () => props.api<ResearchSnapshot>(`/tasks/${taskId}`)
      const latest = poll
        ? await pollTaskUntilTerminal({ fetchTask, onTick: applyTask, signal })
        : await fetchTask()
      if (signal.aborted) return
      if (!latest) {
        turn.streamError = true
        return
      }
      applyTask(latest)
      if (['queued', 'running'].includes(latest.state)) streamTaskEvents(turn)
    } catch {
      if (!signal.aborted) turn.streamError = true
    } finally {
      if (subscriptions.get(taskId) === controller) subscriptions.delete(taskId)
    }
  }

  const close = openTaskEventStream(taskId, {
    /**
     * 更新当前轮次的阶段记录。
     * @param row - 服务端阶段消息。
     */
    onMessage: (row) => {
      if (row.stage) {
        turn.stage = row.stage
        if (row.id !== undefined && !stageIds.has(row.id)) {
          stageIds.add(row.id)
          turn.events.push({
            id: row.id,
            stage: row.stage,
            status: row.status ?? 'running',
            progress: row.progress ?? 0,
          })
        }
      }
      if (typeof row.progress === 'number') turn.progress = row.progress
      scrollToBottom(false)
    },
    /**
     * 在正文区域展示最新草稿。
     * @param text - 当前可见的增量正文合并结果。
     */
    onResult: (text) => {
      turn.draft = text
      scrollToBottom(false)
    },
    /** 获取终态任务的正式报告。 */
    onDone: () => {
      void finish(false)
    },
    /** 连接永久关闭时改由任务快照恢复。 */
    onError: () => {
      void finish(true)
    },
  })
  signal.addEventListener('abort', close, { once: true })
}

/**
 * 运行期向正在执行的智能体追加中途干预指令 (Steering)。
 */
async function handleSteer(): Promise<void> {
  const version = conversationVersion
  const steerText = inputPrompt.value.trim()
  if (!steerText || !activeRunningTaskId.value) return

  try {
    await props.api(`/tasks/${activeRunningTaskId.value}/steer`, 'POST', {
      input: steerText,
    })
    if (version !== conversationVersion) return
    ElMessage.success('已将补充要求发送给调研团队')
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
  if (!prompt || loadingConversation.value || !activeConvId.value) return

  if (submitting.value && activeRunningTaskId.value) {
    await handleSteer()
    return
  }

  if (submitting.value) return

  const version = conversationVersion
  const isFirstTurn = turns.value.length === 0
  const kw = isFirstTurn ? currentKeyword.value || prompt : currentKeyword.value
  currentKeyword.value = kw

  if (isFirstTurn && !conversations.value.some((c) => c.conversation_id === activeConvId.value)) {
    conversations.value.unshift({
      conversation_id: activeConvId.value,
      title: kw,
      turns: 1,
    })
  }

  const tempTurn = reactive<ResearchTurn>({
    id: 'tmp_' + Date.now().toString(),
    question: prompt,
    created_at: new Date().toISOString(),
    state: 'running',
    stage: '正在准备调研任务…',
    progress: 5,
    events: [],
    restricted: false,
  })
  turns.value.push(tempTurn)
  inputPrompt.value = ''
  scrollToBottom()

  try {
    const res = await props.api<CreatedResource>('/tasks', 'POST', {
      question: prompt,
      kind: 'research',
      conversation_id: activeConvId.value,
      keyword: kw,
      platforms: ['web', 'sourcing'],
      input_level: 1,
    })
    if (version !== conversationVersion) return
    tempTurn.id = res.id
    streamTaskEvents(tempTurn)
    const conversation = conversations.value.find(
      (item) => item.conversation_id === activeConvId.value,
    )
    if (conversation) conversation.turns = turns.value.length
  } catch (err: unknown) {
    if (version !== conversationVersion) return
    tempTurn.state = 'failed'
    const msg = err instanceof Error ? err.message : '提交失败'
    tempTurn.stage = msg
    ElMessage.error(msg)
  }
  scrollToBottom()
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

/**
 * 将调研报告原文导出为本地 Markdown 文件下载。
 * @param text - 报告 Markdown 原文
 * @param title - 用于生成文件名的主题词
 */
function downloadReport(text?: string, title?: string): void {
  if (!text) return
  const safeName = (title || 'market_research_report').replace(/[\\/:*?"<>|\s]+/g, '_').slice(0, 40)
  const blob = new Blob([text], { type: 'text/markdown;charset=utf-8' })
  const url = URL.createObjectURL(blob)
  const anchor = document.createElement('a')
  anchor.href = url
  anchor.download = `${safeName}.md`
  anchor.click()
  URL.revokeObjectURL(url)
}

onMounted(() => {
  timerHandle = window.setInterval(() => {
    clock.value = Date.now()
  }, 1000)
  void loadConversations()
})

onUnmounted(() => {
  leaveConversation()
  if (timerHandle !== null) window.clearInterval(timerHandle)
})
</script>

<template>
  <div class="chat-container-layout">
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

    <main class="chat-conversation-stage">
      <header class="conversation-header-bar">
        <div class="header-info">
          <span class="current-topic-name">{{ currentKeyword || '选品调研' }}</span>
          <span v-if="currentTurnCount > 0" class="turn-count-badge"
            >共 {{ currentTurnCount }} 轮问答</span
          >
        </div>
      </header>

      <div ref="chatScrollRef" class="messages-scroll-area">
        <div class="messages-center-wrapper">
          <div v-if="turns.length === 0" class="chat-welcome-box">
            <div class="welcome-robot-wrapper">
              <img :src="robotUrl" alt="淘智机器人" class="welcome-robot-large" />
            </div>
            <h2 class="welcome-title">选品调研</h2>
            <div class="welcome-subtitle">
              输入商品类目或选品问题，比较市场需求、供应商报价与竞品售价。
            </div>
            <div class="preset-suggestions">
              <button
                v-for="kw in presetKeywords"
                :key="kw"
                class="preset-suggestion-btn"
                :disabled="loadingConversation"
                @click="choosePreset(kw)"
              >
                {{ kw }}
              </button>
            </div>
          </div>

          <div v-for="turn in turns" :key="turn.id" class="message-turn-group">
            <div class="user-query-container">
              <div class="user-query-bubble">
                {{ turn.question }}
              </div>
            </div>

            <div class="assistant-response-container">
              <img :src="robotUrl" alt="淘智机器人" class="assistant-avatar-img" />
              <div class="assistant-content-block">
                <el-button
                  v-if="!turn.id.startsWith('tmp_')"
                  link
                  type="primary"
                  @click="emit('inspect', turn.id)"
                  >查看执行轨迹</el-button
                >
                <div
                  v-if="turn.state === 'running' || turn.state === 'queued'"
                  class="active-thinking-block"
                >
                  <div v-if="turn.streamError" class="active-thinking-status">
                    <span>调研连接已中断</span>
                    <el-button link type="primary" @click="streamTaskEvents(turn)"
                      >重新连接</el-button
                    >
                  </div>
                  <div v-else class="active-thinking-status">
                    <span class="thinking-spinner" />
                    <span class="thinking-label"
                      >正在调研 · 已用时 {{ elapsedSeconds(turn) }}s</span
                    >
                  </div>
                  <details v-if="turn.events.length" :open="!turn.draft">
                    <summary>执行步骤（{{ turn.events.length }}）</summary>
                    <ResearchStageList
                      class="live-steps-stream"
                      :events="turn.events"
                      highlight-latest
                    />
                  </details>
                </div>

                <div v-else-if="turn.events.length" class="completed-thinking-accordion">
                  <button
                    class="accordion-header"
                    type="button"
                    :aria-expanded="Boolean(thinkingCollapsed[turn.id])"
                    @click="toggleThinking(turn.id)"
                  >
                    <span class="accordion-arrow">{{
                      thinkingCollapsed[turn.id] ? '▾' : '▸'
                    }}</span>
                    <span class="accordion-title">
                      任务阶段（{{ turn.events.length }} 条 · 耗时
                      {{ turn.elapsed ?? turn.result?.elapsed ?? 0 }}s）
                    </span>
                  </button>
                  <ResearchStageList
                    v-show="thinkingCollapsed[turn.id]"
                    class="accordion-body"
                    :events="turn.events"
                  />
                </div>

                <div v-if="turn.draft || turn.result?.report" class="response-markdown-body">
                  <ul
                    v-if="turn.result?.subagents?.length"
                    class="research-roles"
                    aria-label="参与调研的角色"
                  >
                    <li v-for="role in participatingRoleNames(turn.result.subagents)" :key="role">
                      {{ role }}
                    </li>
                  </ul>
                  <MarkdownReport
                    :content="turn.result?.report || turn.draft || ''"
                    :show-outline="!turn.draft"
                    :id-prefix="'rep-' + turn.id"
                  />
                </div>

                <div v-if="turn.result?.report" class="response-actions-row">
                  <button class="action-btn" @click="copyText(turn.result?.report)">
                    复制全文
                  </button>
                  <button
                    class="action-btn"
                    @click="
                      downloadReport(turn.result?.report, turn.result?.keyword || turn.question)
                    "
                  >
                    导出 .md 报告
                  </button>
                </div>

                <div
                  v-if="['failed', 'cancelled', 'rejected', 'review'].includes(turn.state)"
                  class="error-hint-row"
                >
                  {{ turn.stage || stateText(turn.state) }}
                </div>
              </div>
            </div>
          </div>
        </div>
      </div>

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
                  ? '补充调研要求，例如优先比较 1688 供货报价'
                  : turns.length === 0
                    ? '输入商品类目或调研问题…'
                    : '继续追问本次调研…'
              "
              @keydown="handleKeydown"
            />
            <button
              :class="['send-circle-btn', { 'steer-mode': submitting }]"
              :disabled="loadingConversation || !inputPrompt.trim()"
              :title="submitting ? '发送补充要求' : '发送消息'"
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

.messages-scroll-area {
  flex: 1;
  overflow-y: auto;
  padding: 24px 20px 140px;
}
.messages-center-wrapper {
  max-width: 820px;
  margin: 0 auto;
}

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

.message-turn-group {
  margin-bottom: 32px;
}

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

.active-thinking-block summary {
  cursor: pointer;
  color: var(--tz-ink-secondary);
  font-size: 13px;
}

.live-steps-stream {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.completed-thinking-accordion {
  margin-bottom: 14px;
}
.accordion-header {
  background: transparent;
  border: 0;
  display: inline-flex;
  align-items: center;
  gap: 4px;
  font-size: 12px;
  color: var(--tz-ink-subtle);
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

.research-roles {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  padding: 0;
  margin: 0 0 14px;
  list-style: none;
}
.research-roles li {
  padding: 3px 8px;
  border-radius: 4px;
  background: var(--tz-surface-muted);
  color: var(--tz-ink-secondary);
  font-size: 12px;
}
.response-markdown-body {
  margin-top: 8px;
  padding: 16px 18px;
  background: #ffffff;
  border: 1px solid #e2e8f0;
  border-radius: 10px;
}

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
