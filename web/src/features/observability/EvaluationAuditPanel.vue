<template>
  <el-alert v-if="auditError" type="error" show-icon :title="auditError" :closable="false" />
  <el-table
    v-else
    v-loading="auditBusy"
    :data="auditLogs"
    row-key="audit_id"
    empty-text="当前筛选范围没有审计记录"
  >
    <el-table-column type="expand">
      <template #default="{ row }">
        <pre class="evaluation-json">{{ JSON.stringify(row.summary, null, 2) }}</pre>
      </template>
    </el-table-column>
    <el-table-column label="动作" width="120">
      <template #default="{ row }">
        <el-tag size="small">{{ auditActionLabel(row.action) }}</el-tag>
      </template>
    </el-table-column>
    <el-table-column label="对象" min-width="190">
      <template #default="{ row }">{{ row.target_id }}</template>
    </el-table-column>
    <el-table-column label="操作人" min-width="130">
      <template #default="{ row }">{{ row.actor_id }}</template>
    </el-table-column>
    <el-table-column label="时间" min-width="150">
      <template #default="{ row }">{{ traceTime(row.created_at) }}</template>
    </el-table-column>
    <el-table-column label="摘要" min-width="240">
      <template #default="{ row }">{{ summarizeJson(row.summary) }}</template>
    </el-table-column>
  </el-table>
  <nav class="evaluation-pager" aria-label="审计留痕分页">
    <span>第 {{ auditPage }} 页</span>
    <el-button :disabled="auditBusy || auditPage === 1" @click="emit('turn', false)"
      >上一页</el-button
    >
    <el-button :disabled="auditBusy || !auditNext" @click="emit('turn', true)">下一页</el-button>
  </nav>
</template>

<script setup lang="ts">
import { type EvaluationAuditLog } from './evaluationTypes'
import { auditActionLabel, summarizeJson } from './evaluationHelpers'
import { traceTime } from '../../shared/labels'

defineProps<{
  auditLogs: EvaluationAuditLog[]
  auditBusy: boolean
  auditError: string
  auditNext: string | null
  auditPage: number
}>()
const emit = defineEmits<{ (e: 'turn', forward: boolean): void }>()
</script>
