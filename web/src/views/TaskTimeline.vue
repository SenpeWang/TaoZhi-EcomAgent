<script setup lang="ts">
import { computed, ref } from 'vue'
import { visibleTaskResult } from '../knowledgeAnswers'
import { ElMessage } from 'element-plus'
import type { Task } from '../types'
import { formatStage, levelText, stateTag, stateText } from '../labels'
import MarkdownReport from './MarkdownReport.vue'
import KnowledgeAnswer from './KnowledgeAnswer.vue'

const props = defineProps<{
  modelValue: boolean
  task: Task | null
  draft: string
  canInspect: boolean
}>()

const emit = defineEmits<{
  (e: 'update:modelValue', value: boolean): void
  (e: 'inspect', taskId: string): void
  (e: 'steer', taskId: string, message: string): void
  (e: 'retry', taskId: string): void
  (e: 'cancel', taskId: string): void
  (e: 'review', taskId: string, approve: boolean): void
  (e: 'openDoc', docId: string): void
}>()

const steerInput = ref('')
const result = computed(() => visibleTaskResult(props.task))

/**
 * 复制当前调研研报到剪贴板。
 */
async function copyReport(): Promise<void> {
  if (!result.value?.report) return
  try {
    await navigator.clipboard.writeText(result.value.report)
    ElMessage.success('已复制调研研报 Markdown')
  } catch {
    ElMessage.warning('复制失败，可选中报告正文手动复制')
  }
}

/**
 * 提交运行时动态干预指引。
 */
function onSteer() {
  if (!props.task || !steerInput.value.trim()) return
  emit('steer', props.task.id, steerInput.value.trim())
  steerInput.value = ''
}
</script>

<template>
  <el-dialog
    :model-value="modelValue"
    title="任务详情"
    width="860px"
    @update:model-value="emit('update:modelValue', $event)"
  >
    <template v-if="task">
      <h3>{{ task.restricted ? '受限提问' : task.question }}</h3>
      <div class="detail-meta">
        <el-tag>{{ stateText(task.state) }}</el-tag>
        <span>{{ formatStage(task.stage) }}</span>
        <span>{{ levelText(task.input_level) }}</span>
        <el-button v-if="canInspect" link type="primary" @click="emit('inspect', task.id)"
          >查看执行轨迹</el-button
        >
      </div>
      <el-progress
        :percentage="task.progress"
        :status="task.state === 'completed' ? 'success' : undefined"
      />

      <section
        v-if="
          draft && !task.result && !task.restricted && ['running', 'queued'].includes(task.state)
        "
        class="answer-body"
      >
        <h3>{{ task.kind === 'research' ? '正在生成调研报告' : '正在生成回答' }}</h3>
        <MarkdownReport
          :content="draft"
          :id-prefix="'draft-' + task.id"
          :show-outline="false"
          compact
        />
      </section>

      <div v-if="task.events?.length" class="workflow-events-box">
        <div class="events-box-header">
          <strong>任务阶段（{{ task.events.length }} 条）</strong>
          <small>按发生时间排列</small>
        </div>
        <div class="events-timeline-list">
          <div
            v-for="(ev, idx) in task.events"
            :key="ev.id || idx"
            class="event-timeline-row"
            :class="{
              completed: ev.progress === 100 || idx < task.events.length - 1,
              current: idx === task.events.length - 1 && task.state === 'running',
            }"
          >
            <span class="event-step-idx">{{ idx + 1 }}</span>
            <span class="event-stage-text">{{ formatStage(ev.stage) }}</span>
            <el-tag size="small" :type="ev.progress === 100 ? 'success' : 'info'" effect="plain">
              {{ ev.progress }}%
            </el-tag>
          </div>
        </div>
      </div>

      <el-alert v-if="task.error" :title="task.error" type="error" :closable="false" />
      <el-alert
        v-if="task.restricted"
        title="来源权限已变化，请重新查询。"
        type="warning"
        :closable="false"
      />

      <template v-if="result">
        <template v-if="task.kind === 'research'">
          <div class="research-header-row">
            <h3>选品调研报告</h3>
            <el-button size="small" type="primary" plain @click="copyReport">
              复制报告全文
            </el-button>
          </div>
          <div class="detail-meta">
            <el-tag
              v-for="pl in result.platforms || []"
              :key="pl.name"
              :type="pl.state === 'ok' ? 'success' : 'info'"
              effect="plain"
            >
              {{ pl.label }} · {{ pl.state_label }}
            </el-tag>
            <el-tag v-if="result.elapsed" type="warning" effect="plain">
              用时 {{ result.elapsed }}s
            </el-tag>
            <el-tag v-if="result.model_calls" type="info" effect="plain">
              模型调用 {{ result.model_calls }} 次
            </el-tag>
          </div>

          <div v-if="result.subagents?.length" class="subagents-roster-box">
            <div class="roster-header">
              <strong>调研执行记录（{{ result.subagents.length }} 次）</strong>
            </div>
            <div class="subagents-card-grid">
              <div
                v-for="researchAgent in result.subagents"
                :key="researchAgent.id"
                class="subagent-roster-card"
              >
                <div class="subagent-card-top">
                  <span class="subagent-role-name">{{ researchAgent.display_name }}</span>
                  <el-tag size="small" :type="stateTag(researchAgent.status)" effect="light">
                    {{ stateText(researchAgent.status) }}
                  </el-tag>
                </div>
                <div class="subagent-card-bottom">
                  <small :title="researchAgent.id">执行 ID {{ researchAgent.id }}</small>
                  <small v-if="researchAgent.elapsed != null">{{ researchAgent.elapsed }}s</small>
                </div>
              </div>
            </div>
          </div>

          <div v-if="result.outline?.length" class="research-outline-bar">
            <span class="outline-label">报告大纲：</span>
            <el-tag
              v-for="(ch, idx) in result.outline"
              :key="idx"
              size="small"
              effect="light"
              class="chapter-pill"
            >
              {{ ch }}
            </el-tag>
          </div>

          <div v-if="result.artifacts?.length" class="artifacts-bar">
            <span class="artifact-label">附件输出：</span>
            <el-tag
              v-for="(art, idx) in result.artifacts"
              :key="idx"
              size="small"
              type="info"
              class="artifact-pill"
            >
              {{ art.split('/').pop() }}
            </el-tag>
          </div>

          <div class="answer-body research-markdown-view">
            <MarkdownReport :content="result.report || ''" :id-prefix="'task-' + task.id" compact />
          </div>
        </template>

        <KnowledgeAnswer
          v-else
          :task-id="task.id"
          :result="result"
          @open-doc="emit('openDoc', $event)"
        />
      </template>

      <el-empty v-else-if="task.state === 'review'" description="待负责人审核后发布结果" />

      <div
        v-if="task.state === 'running' && task.kind === 'research'"
        class="mid-flight-steering-box"
        style="margin-top: 18px"
      >
        <el-input
          v-model="steerInput"
          placeholder="发送补充要求（例如：优先筛选浙江产地供应商）"
          clearable
          @keyup.enter="onSteer"
        >
          <template #append>
            <el-button type="warning" @click="onSteer">发送补充要求</el-button>
          </template>
        </el-input>
      </div>

      <div class="dialog-actions" style="margin-top: 18px">
        <template v-if="task.state === 'review' && task.can_review">
          <el-button type="primary" @click="emit('review', task.id, true)">通过发布</el-button>
          <el-button type="danger" plain @click="emit('review', task.id, false)">驳回</el-button>
        </template>
        <el-button
          v-if="['failed', 'cancelled'].includes(task.state)"
          type="warning"
          plain
          @click="emit('retry', task.id)"
        >
          重新运行
        </el-button>
        <el-button
          v-if="['queued', 'running', 'review'].includes(task.state) && task.can_cancel"
          @click="emit('cancel', task.id)"
        >
          取消任务
        </el-button>
      </div>
    </template>
  </el-dialog>
</template>
