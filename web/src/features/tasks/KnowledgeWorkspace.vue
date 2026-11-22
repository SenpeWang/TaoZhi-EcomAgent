<script setup lang="ts">
import { computed, nextTick, reactive, ref, watch } from 'vue'
import { visibleTaskResult } from './knowledgeAnswers'
import { formatStage, levelText, stateTag, stateText, traceTime } from '../../shared/labels'
import type { KnowledgeQuestion, Task } from './types'
import type { Organization, User } from '../administration/types'
import KnowledgeAnswer from './KnowledgeAnswer.vue'
import MarkdownReport from '../../shared/MarkdownReport.vue'
import './knowledge.css'

const props = defineProps<{
  task: Task | null
  tasks: Task[]
  draft: string
  busy: boolean
  loadingAnswer?: boolean
  user: User
  organizations: Organization[]
  inputLevel: number
  historyIconPaths: readonly string[]
}>()
const emit = defineEmits<{
  submit: [question: KnowledgeQuestion]
  select: [id: string]
  reset: []
  inspect: [id: string]
  openDoc: [id: string]
  browse: []
  retry: [id: string]
  cancel: [id: string]
  review: [id: string, approved: boolean]
}>()
const form = reactive<KnowledgeQuestion>({
  question: '',
  node_id: '',
  require_review: false,
  allow_external: !props.user.is_boss,
  allow_public_reference: true,
  sku_id: '',
  order_id: '',
})
const textarea = ref<HTMLTextAreaElement>()
const heading = ref<HTMLElement>()
const filter = ref('')
const refining = ref(false)
const history = computed(() =>
  props.tasks.filter(
    (item) =>
      item.owner_id === props.user.id &&
      item.kind === 'ask' &&
      (!filter.value.trim() || (!item.restricted && item.question.includes(filter.value.trim()))),
  ),
)
const result = computed(() => visibleTaskResult(props.task))
const canRefine = computed(
  () =>
    props.task?.kind === 'ask' &&
    props.task.owner_id === props.user.id &&
    props.task.state === 'completed' &&
    Boolean(
      result.value?.answer_basis === 'insufficient_evidence' || result.value?.missing?.length,
    ),
)
const running = computed(() => props.task && ['queued', 'running'].includes(props.task.state))
const examples = ['本店的退货运费由谁承担？', '什么是 USB PD？']
const waitingMessage: Record<string, string> = {
  review: '答复正在等待负责人审核。审核通过后可以查看。',
  cancelled: '这次提问已取消。',
  rejected: '这份答复已被驳回，可以补充资料后重新提问。',
  failed: '这次提问未完成，可以重新运行或查看执行轨迹。',
}

watch(
  () => props.task?.id,
  async (id, previous) => {
    await nextTick()
    if (id) heading.value?.focus()
    else if (previous) {
      textarea.value?.focus()
      if (refining.value)
        textarea.value?.setSelectionRange(form.question.length, form.question.length)
    }
  },
)

/**
 * 将示例放入编辑框，允许补充业务条件后再发送。
 * @param question - 可编辑的示例问题。
 */
function chooseExample(question: string): void {
  form.question = question
  textarea.value?.focus()
}

/**
 * 新建独立提问，补充条件时只复制当前可见的原问题。
 * @param refine - 是否预填需要补充依据的问题，并收紧默认资料范围。
 */
function newQuestion(refine: boolean): void {
  if (refine && !canRefine.value) return
  refining.value = refine
  form.question = refine && props.task ? props.task.question : ''
  form.sku_id = ''
  form.order_id = ''
  if (refine) {
    form.node_id = ''
    form.allow_public_reference = false
    form.allow_external = false
  }
  emit('reset')
}

/** 提交去除首尾空白的问题与独立的资料、模型授权选项。 */
function submit(): void {
  const question = form.question.trim()
  if (props.busy || !question) return
  emit('submit', { ...form, question, sku_id: form.sku_id.trim(), order_id: form.order_id.trim() })
}
</script>

<template>
  <div class="knowledge-layout">
    <section v-if="loadingAnswer" class="panel knowledge-main" aria-busy="true" role="status">
      正在读取已选提问…
    </section>
    <section v-else-if="!task" class="panel knowledge-main" aria-labelledby="ask-heading">
      <header class="knowledge-section-head">
        <h2 id="ask-heading">{{ refining ? '补充条件并新建提问' : '提出问题' }}</h2>
        <span class="level-pill" :class="'level-' + inputLevel">{{ levelText(inputLevel) }}</span>
      </header>
      <form class="knowledge-form" @submit.prevent="submit">
        <p v-if="refining" class="knowledge-meta">
          在原问题中补充型号、渠道或适用时间。提交后会生成一条独立任务记录。
        </p>
        <label for="knowledge-question">咨询内容</label>
        <textarea
          id="knowledge-question"
          ref="textarea"
          v-model="form.question"
          rows="5"
          maxlength="4000"
          required
          placeholder="写下问题，可附上型号、渠道或适用时间。"
          @keydown.ctrl.enter.prevent="textarea?.form?.requestSubmit()"
          @keydown.meta.enter.prevent="textarea?.form?.requestSubmit()"
        />
        <div v-if="!refining" class="knowledge-examples">
          <button
            v-for="example in examples"
            :key="example"
            type="button"
            @click="chooseExample(example)"
          >
            {{ example }}
          </button>
        </div>
        <fieldset class="knowledge-scope">
          <legend>回答依据</legend>
          <label
            ><input
              v-model="form.allow_public_reference"
              name="knowledge-scope"
              type="radio"
              :value="false"
            />仅企业资料</label
          >
          <label
            ><input
              v-model="form.allow_public_reference"
              name="knowledge-scope"
              type="radio"
              :value="true"
            />企业资料与官方公开参考</label
          >
          <p>通用概念可查内置官方参考。企业政策、商品承诺与经营数据须有授权内部依据。</p>
        </fieldset>
        <div class="knowledge-checks">
          <label
            ><input v-model="form.allow_external" type="checkbox" />允许外部 AI
            处理已获外发许可的资料</label
          >
        </div>
        <details class="knowledge-options" :open="refining">
          <summary>任务设置与业务编号</summary>
          <div class="knowledge-fields">
            <label
              >任务归属
              <select v-model="form.node_id">
                <option value="">默认组织</option>
                <option v-for="org in organizations" :key="org.id" :value="org.id">
                  {{ org.name }}
                </option>
              </select>
            </label>
            <label
              >商品编号<input
                v-model="form.sku_id"
                maxlength="64"
                pattern="[A-Za-z0-9_\-]*"
                placeholder="查询库存或现价时填写"
            /></label>
            <label
              >订单编号<input
                v-model="form.order_id"
                maxlength="64"
                pattern="[A-Za-z0-9_\-]*"
                placeholder="查询订单状态时填写"
            /></label>
          </div>
          <div class="knowledge-checks">
            <label
              ><input
                v-model="form.require_review"
                type="checkbox"
              />答复须由负责人审核后发布</label
            >
          </div>
        </details>
        <div class="knowledge-submit">
          <span class="knowledge-meta">{{ form.question.length }} / 4000</span>
          <el-button
            native-type="submit"
            type="primary"
            :disabled="!form.question.trim()"
            :loading="busy"
            >{{ refining ? '提交为新任务' : '提交提问' }}</el-button
          >
        </div>
      </form>
    </section>

    <section v-else class="panel knowledge-main" aria-labelledby="answer-heading">
      <header class="knowledge-section-head">
        <el-tag :type="stateTag(task.state)" effect="light">{{ stateText(task.state) }}</el-tag>
        <el-button text type="primary" @click="newQuestion(false)">新提问</el-button>
      </header>
      <h2 id="answer-heading" ref="heading" tabindex="-1" class="knowledge-question-title">
        {{ task.restricted ? '受限提问' : task.question }}
      </h2>
      <div class="knowledge-byline">
        <span>提交于 {{ traceTime(task.created_at) }}</span>
        <el-button link type="primary" @click="emit('inspect', task.id)">查看执行轨迹</el-button>
      </div>
      <el-alert
        v-if="task.restricted"
        type="warning"
        title="当前无权阅读这次答复及引用资料。"
        :closable="false"
      />
      <template v-else>
        <div v-if="running" class="knowledge-progress" role="status">
          <strong>{{ formatStage(task.stage) }}</strong>
          <el-progress :percentage="task.progress" />
        </div>
        <section
          v-if="running && draft && !task.result"
          class="knowledge-draft"
          aria-label="流式答复"
        >
          <h3>正在生成答复</h3>
          <MarkdownReport
            :content="draft"
            :id-prefix="'knowledge-draft-' + task.id"
            :show-outline="false"
            compact
          />
        </section>
        <KnowledgeAnswer
          v-if="result"
          :key="task.id"
          :task-id="task.id"
          :result="result"
          @open-doc="emit('openDoc', $event)"
        />
        <p v-else-if="!running" class="knowledge-notice" role="status">
          {{ task.error || waitingMessage[task.state] || '当前没有可展示的答复。' }}
        </p>
        <div class="knowledge-actions">
          <el-button v-if="canRefine" type="primary" plain @click="newQuestion(true)"
            >补充条件再问</el-button
          >
          <el-button
            v-if="result?.answer_basis === 'insufficient_evidence'"
            plain
            @click="emit('browse')"
            >查看知识文档</el-button
          >
          <template v-if="task.state === 'review' && task.can_review">
            <el-button type="primary" :loading="busy" @click="emit('review', task.id, true)"
              >通过发布</el-button
            >
            <el-button type="danger" plain :disabled="busy" @click="emit('review', task.id, false)"
              >驳回</el-button
            >
          </template>
          <el-button
            v-if="['cancelled', 'failed'].includes(task.state)"
            plain
            :loading="busy"
            @click="emit('retry', task.id)"
            >重新运行</el-button
          >
          <el-button
            v-if="(running || task.state === 'review') && task.can_cancel"
            text
            :disabled="busy"
            @click="emit('cancel', task.id)"
            >取消任务</el-button
          >
        </div>
      </template>
    </section>

    <aside class="panel knowledge-history" aria-labelledby="knowledge-history-title">
      <header class="knowledge-section-head">
        <h2 id="knowledge-history-title">我的最近提问</h2>
        <svg
          viewBox="0 0 24 24"
          fill="none"
          stroke="currentColor"
          stroke-width="1.7"
          aria-hidden="true"
        >
          <path v-for="path in historyIconPaths" :key="path" :d="path" />
        </svg>
      </header>
      <input
        v-model="filter"
        class="knowledge-history-search"
        aria-label="筛选最近提问"
        type="search"
        placeholder="筛选最近提问"
      />
      <ul v-if="history.length" class="knowledge-history-list">
        <li v-for="item in history" :key="item.id">
          <button
            type="button"
            :aria-current="task?.id === item.id ? 'true' : undefined"
            @click="emit('select', item.id)"
          >
            <strong>{{ item.restricted ? '受限提问' : item.question }}</strong>
            <span
              ><time :datetime="item.created_at">{{ traceTime(item.created_at) }}</time
              ><small>{{
                stateText(task && item.id === task.id ? task.state : item.state)
              }}</small></span
            >
          </button>
        </li>
      </ul>
      <p v-else class="knowledge-notice">
        {{ filter ? '没有匹配的提问。' : '暂无个人提问记录。' }}
      </p>
    </aside>
  </div>
</template>
