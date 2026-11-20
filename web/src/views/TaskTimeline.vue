<script setup lang="ts">
import { ref } from 'vue'
import { ElMessage } from 'element-plus'
import type { Task } from '../types'
import { formatStage, levelText, stateTag, stateText } from '../labels'
import MarkdownReport from './MarkdownReport.vue'

const props = defineProps<{
  modelValue: boolean
  task: Task | null
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

/**
 * 格式化任务状态描述。
 * @param s - 状态码。
 * @returns 中文描述。
 */
function state(s?: string): string {
  return stateText(s)
}

/**
 * 格式化密级描述。
 * @param l - 密级数值。
 * @returns 中文描述。
 */
function level(l?: number): string {
  return levelText(l)
}

/**
 * 复制当前调研研报到剪贴板。
 */
function copyReport() {
  if (!props.task?.result?.report) return
  navigator.clipboard.writeText(props.task.result.report)
  ElMessage.success('已复制调研研报 Markdown')
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
      <h3>{{ task.question }}</h3>
      <div class="detail-meta">
        <el-tag>{{ state(task.state) }}</el-tag>
        <span>{{ formatStage(task.stage) }}</span>
        <span>{{ level(task.input_level) }}</span>
        <el-button v-if="canInspect" link type="primary" @click="emit('inspect', task.id)"
          >查看执行轨迹</el-button
        >
      </div>
      <el-progress
        :percentage="task.progress"
        :status="task.state === 'completed' ? 'success' : undefined"
      />

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

      <template v-if="task.result">
        <template v-if="task.kind === 'research'">
          <div class="research-header-row">
            <h3>选品调研报告</h3>
            <el-button size="small" type="primary" plain @click="copyReport">
              复制报告全文
            </el-button>
          </div>
          <div class="detail-meta">
            <el-tag
              v-for="pl in task.result.platforms || []"
              :key="pl.name"
              :type="pl.state === 'ok' ? 'success' : 'info'"
              effect="plain"
            >
              {{ pl.label }} · {{ pl.state_label }}
            </el-tag>
            <el-tag v-if="task.result.elapsed" type="warning" effect="plain">
              用时 {{ task.result.elapsed }}s
            </el-tag>
            <el-tag v-if="task.result.model_calls" type="info" effect="plain">
              模型调用 {{ task.result.model_calls }} 次
            </el-tag>
          </div>

          <div v-if="task.result.subagents?.length" class="subagents-roster-box">
            <div class="roster-header">
              <strong>调研执行记录（{{ task.result.subagents.length }} 次）</strong>
            </div>
            <div class="subagents-card-grid">
              <div
                v-for="researchAgent in task.result.subagents"
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

          <div v-if="task.result.outline?.length" class="research-outline-bar">
            <span class="outline-label">报告大纲：</span>
            <el-tag
              v-for="(ch, idx) in task.result.outline"
              :key="idx"
              size="small"
              effect="light"
              class="chapter-pill"
            >
              {{ ch }}
            </el-tag>
          </div>

          <div v-if="task.result.artifacts?.length" class="artifacts-bar">
            <span class="artifact-label">附件输出：</span>
            <el-tag
              v-for="(art, idx) in task.result.artifacts"
              :key="idx"
              size="small"
              type="info"
              class="artifact-pill"
            >
              {{ art.split('/').pop() }}
            </el-tag>
          </div>

          <div class="answer-body research-markdown-view">
            <MarkdownReport
              :content="task.result.report || ''"
              :id-prefix="'task-' + task.id"
              compact
            />
          </div>
        </template>

        <template v-else>
          <h3>检索结果</h3>
          <div class="detail-meta">
            <el-tag
              v-for="specialist in task.result.specialists || []"
              :key="specialist.name"
              effect="plain"
            >
              {{ specialist.display_name }} ·
              {{ specialist.count ? '匹配 ' + specialist.count + ' 条依据' : '暂无匹配依据' }}
            </el-tag>
          </div>
          <div class="answer-body answer-markdown-view">
            <MarkdownReport
              :content="task.result.answer || ''"
              :id-prefix="'ans-' + task.id"
              :show-outline="false"
              compact
            />
          </div>
          <div v-if="task.result.missing?.length" class="missing-box">
            <strong>待补充确认</strong>
            <p v-for="m in task.result.missing" :key="m">{{ m }}</p>
          </div>
          <h3 v-if="task.result.citations?.length">参考依据来源</h3>
          <el-collapse>
            <el-collapse-item
              v-for="(c, i) in task.result.citations"
              :key="c.chunk_id"
              :title="'[' + (Number(i) + 1) + '] ' + c.title + ' · V' + c.version"
            >
              <pre class="quote-body">{{ c.quote }}</pre>
              <el-button text type="primary" @click="emit('openDoc', c.document_id)">
                查看原文文档
              </el-button>
            </el-collapse-item>
          </el-collapse>
        </template>
      </template>

      <el-empty v-else-if="task.state === 'review'" description="待负责人审核后发布结果" />

      <div v-if="task.state === 'running'" class="mid-flight-steering-box" style="margin-top: 18px">
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
