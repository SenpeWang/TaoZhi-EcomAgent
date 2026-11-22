<script setup lang="ts">
import { computed } from 'vue'
import type { TaskEventItem } from './types'

const props = defineProps<{ events: TaskEventItem[]; highlightLatest?: boolean }>()

const stageLabels = [
  { prefix: '[agent_start]', type: 'agent', label: '任务启动', icon: 'STEP' },
  { prefix: '[agent_handoff]', type: 'agent', label: '任务交接', icon: 'FLOW' },
  { prefix: '[tool_execution_start]', type: 'tool', label: '工具调用', icon: 'TOOL' },
  { prefix: '[tool_execution_end]', type: 'data', label: '采集完成', icon: 'DATA' },
  { prefix: '[system] [模型容灾]', type: 'security', label: '模型容灾', icon: 'ROUTE' },
  { prefix: '[system] [检查点续跑]', type: 'turn', label: '断点恢复', icon: 'CKPT' },
  { prefix: '[system]', type: 'plain', label: '系统事件', icon: 'SYS' },
  { prefix: '[steering]', type: 'steering', label: '指令干预', icon: 'STEER' },
  { prefix: '【用户中途追加干预指令】：', type: 'steering', label: '指令干预', icon: 'STEER' },
  { prefix: '[agent_end]', type: 'done', label: '阶段完成', icon: 'DONE' },
]
const steps = computed(() =>
  props.events.map(({ id, stage }) => {
    const label = stageLabels.find((entry) => stage.startsWith(entry.prefix))
    return label
      ? { id, ...label, content: stage.slice(label.prefix.length).trim() }
      : { id, type: 'plain', label: '执行状态', icon: 'INFO', content: stage }
  }),
)
</script>

<template>
  <div>
    <div
      v-for="(step, index) in steps"
      :key="step.id"
      class="trace-step-entry"
      :class="[step.type, { current: highlightLatest && index === steps.length - 1 }]"
    >
      <div class="trace-step-header">
        <span class="trace-step-icon" aria-hidden="true">{{ step.icon }}</span>
        <span class="trace-step-type">{{ step.label }}</span>
      </div>
      <div class="trace-step-text">{{ step.content }}</div>
    </div>
  </div>
</template>

<style scoped>
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
  font-size: 10px;
  font-weight: 700;
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  padding: 1px 5px;
  border-radius: 4px;
  background: #f1f5f9;
  color: #475569;
  letter-spacing: 0.4px;
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
</style>
