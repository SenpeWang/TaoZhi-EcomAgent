<template>
  <el-dialog
    :model-value="true"
    title="用例详情"
    width="min(980px, 96vw)"
    align-center
    @close="emit('close')"
  >
    <div v-if="error" class="evaluation-error">
      <el-alert :title="error" type="error" :closable="false" /><el-button @click="load"
        >重试</el-button
      >
    </div>
    <el-skeleton v-else-if="!detail" :rows="5" animated />
    <template v-else>
      <div class="evaluation-run-title">
        <h3>{{ detail.case.name }}</h3>
        <el-tag :type="evaluationTags[detail.case.status]" size="small">{{
          evaluationStatuses[detail.case.status]
        }}</el-tag>
      </div>
      <p class="evaluation-muted">
        {{ caseId }} · {{ evaluationSource(run) }} ·
        {{ evaluationSuites[detail.case.suite] || detail.case.suite }}
      </p>
      <el-alert
        v-if="detail.case.hard_failure"
        title="该用例触发硬性违规，发布门禁阻断"
        type="error"
        :closable="false"
        show-icon
      />
      <div class="evaluation-evidence">
        <section>
          <h4>预期行为</h4>
          <p v-if="detail.expected === null" class="evaluation-muted">该版本报告未保存预期行为。</p>
          <ul v-else-if="detail.expected.length">
            <li v-for="line in detail.expected" :key="line">{{ line }}</li>
          </ul>
          <p v-else class="evaluation-muted">该用例没有具体行为标注。</p>
        </section>
        <section>
          <h4>{{ run.mode === 'live' ? '实际工具调用' : '回放工具调用' }}</h4>
          <p v-if="detail.observed === null" class="evaluation-muted">该版本报告未记录工具步骤。</p>
          <template v-else>
            <p v-if="detail.steps_complete === false" class="evaluation-muted">
              只采集到了部分工具步骤。
            </p>
            <ul v-if="detail.observed.length" class="evaluation-steps">
              <li v-for="(step, index) in detail.observed" :key="index">
                <strong
                  >{{ step.step === null ? '顺序未知' : '步骤 ' + step.step }} ·
                  {{ step.tool_name }}</strong
                >
                <span
                  >{{
                    step.dispatched === true
                      ? '已派发'
                      : step.dispatched === false
                        ? '未派发'
                        : '派发未知'
                  }}
                  ·
                  {{
                    step.status ? (evaluationToolStatuses[step.status] ?? step.status) : '状态未知'
                  }}<template v-if="step.data_count !== null">
                    · {{ step.data_count }} 条结果</template
                  ></span
                >
                <small v-if="step.parameter_keys?.length" class="evaluation-meta"
                  >参数名 {{ step.parameter_keys.join('、') }}</small
                >
                <el-tag
                  v-if="typeof step.parameters_match === 'boolean'"
                  :type="step.parameters_match ? 'success' : 'danger'"
                  size="small"
                  >{{ step.parameters_match ? '参数符合用例' : '参数与用例不符' }}</el-tag
                >
              </li>
            </ul>
            <p v-else class="evaluation-muted">
              {{
                detail.steps_complete === true ? '本例没有工具调用。' : '没有可展示的工具调用记录。'
              }}
            </p>
          </template>
        </section>
      </div>
      <h4>逐项判定</h4>
      <ul class="evaluation-verdicts">
        <li v-for="item in detail.verdicts" :key="item.judge">
          <div>
            <strong>{{ evaluationJudges[item.judge] ?? item.judge }}</strong>
            <small>{{ evaluationDimensions[item.dimension] ?? item.dimension }}</small>
            <el-tag :type="evaluationTags[item.status]" size="small">{{
              evaluationStatuses[item.status]
            }}</el-tag>
          </div>
          <p>{{ item.reason }}</p>
          <ul v-if="item.issues.length">
            <li v-for="(issue, index) in item.issues" :key="index">
              <template v-if="issue.step !== null">步骤 {{ issue.step }} · </template>
              <template v-if="issue.tool_name">{{ issue.tool_name }} · </template>
              {{ evaluationFailures[issue.failure_type] || issue.failure_type }}
            </li>
          </ul>
        </li>
      </ul>
      <el-button
        v-if="run.mode === 'live' && detail.trace_link"
        type="primary"
        plain
        @click="emit('inspect', detail.trace_link.task_id, detail.trace_link.trace_id)"
        >查看关联执行轨迹</el-button
      >
    </template>
  </el-dialog>
</template>

<script setup lang="ts">
import { onMounted, ref } from 'vue'
import {
  evaluationDimensions,
  evaluationFailures,
  evaluationJudges,
  evaluationSource,
  evaluationRunPath,
  evaluationStatuses,
  evaluationSuites,
  evaluationTags,
  evaluationToolStatuses,
  type EvaluationApi,
  type EvaluationCaseDetail,
  type EvaluationRun,
} from '../evaluations'

const props = defineProps<{ run: EvaluationRun; caseId: string; api: EvaluationApi }>()
const emit = defineEmits<{
  (e: 'close'): void
  (e: 'inspect', taskId: string, traceId: string): void
}>()
const detail = ref<EvaluationCaseDetail | null>(null)
const error = ref('')

/** 读取当前账号有权查看的用例，服务端确定可跳转的业务轨迹。 */
async function load(): Promise<void> {
  error.value = ''
  try {
    detail.value = await props.api<EvaluationCaseDetail>(
      evaluationRunPath(props.run.run_id) + '/cases/' + encodeURIComponent(props.caseId),
    )
  } catch (cause) {
    error.value = cause instanceof Error ? cause.message : '读取用例详情失败'
  }
}
onMounted(load)
</script>
