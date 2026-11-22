<template>
  <section class="panel">
    <div class="section-heading evaluation-baseline-header">
      <h3>基线对比</h3>
      <el-select
        v-model="baseline"
        aria-label="选择对照批次"
        placeholder="随报告保存的对比"
        clearable
        @visible-change="(visible: boolean) => visible && !loadedChoices && loadChoices()"
      >
        <el-option label="随报告保存的对比" value="" />
        <el-option
          v-for="item in candidates"
          :key="item.run_id"
          :value="item.run_id"
          :label="
            evaluationSource(item) +
            ' · ' +
            traceTime(item.created_at) +
            ' · ' +
            item.owner_name +
            ' · ' +
            item.run_id
          "
        />
        <template #footer>
          <span v-if="choiceBusy" role="status">正在加载可比批次…</span>
          <el-button v-else-if="choiceError" text type="primary" @click="loadChoices"
            >读取失败，重试</el-button
          >
          <el-button v-else-if="nextCursor" text type="primary" @click="loadChoices"
            >继续查找可比批次</el-button
          >
          <span v-else-if="!candidates.length">当前范围没有相同判定器版本的其他批次</span>
          <span v-else>已加载全部可比批次</span>
        </template>
      </el-select>
    </div>
    <div v-if="error" class="evaluation-error">
      <el-alert type="error" :title="error" :closable="false" /><el-button @click="loadComparison"
        >重试</el-button
      >
    </div>
    <el-skeleton v-else-if="busy" :rows="2" animated />
    <p v-else-if="!comparison" class="evaluation-muted">
      当前没有可查看的基线对比，可选择其他已入库批次。
    </p>
    <template v-else>
      <p class="evaluation-muted">
        标准集配对 {{ comparison.paired_counts.standard }} 例 · 挑战集配对
        {{ comparison.paired_counts.challenge }} 例 · 排除 {{ comparison.excluded_count }} 例
      </p>
      <el-alert
        v-if="!comparison.comparable"
        type="warning"
        title="本次对照缺少可比样本"
        :closable="false"
      />
      <div v-for="group in changeGroups" :key="group.key">
        <div v-for="kind in changes" :key="kind.key">
          <details v-if="comparison.changes[group.key][kind.key].length" class="evaluation-change">
            <summary>
              {{ group.label }} · {{ kind.label }}（{{
                comparison.changes[group.key][kind.key].length
              }}）
            </summary>
            <div class="evaluation-case-links">
              <button
                v-for="id in comparison.changes[group.key][kind.key]"
                :key="id"
                class="evaluation-link"
                type="button"
                @click="emit('selectCase', id)"
              >
                {{ id }}
              </button>
            </div>
          </details>
        </div>
      </div>
      <details v-if="comparison.excluded_count" class="evaluation-change">
        <summary>排除的用例（{{ comparison.excluded_count }}）</summary>
        <dl class="evaluation-excluded">
          <template v-for="item in exclusions" :key="item.key">
            <div v-if="comparison.excluded[item.key].length">
              <dt>{{ item.label }}</dt>
              <dd>{{ comparison.excluded[item.key].join('、') }}</dd>
            </div>
          </template>
        </dl>
      </details>
    </template>
  </section>
</template>

<script setup lang="ts">
import { onScopeDispose, ref, watch } from 'vue'
import type { ApiReader, CursorPage } from '../../shared/apiTypes'
import {
  evaluationRunPath,
  evaluationSource,
  isBaselineCandidate,
  type EvaluationComparison,
  type EvaluationRun,
} from './evaluationTypes'
import { traceTime } from '../../shared/labels'

const props = defineProps<{
  run: EvaluationRun
  api: ApiReader
  ownerFilter: string | null
  storedComparison: EvaluationComparison | null
}>()
const emit = defineEmits<{ (e: 'selectCase', id: string): void }>()
const changeGroups = [
  { key: 'standard', label: '标准集' },
  { key: 'challenge', label: '挑战集' },
] as const
const changes = [
  { key: 'regressed', label: '退化' },
  { key: 'fixed', label: '修复' },
  { key: 'stable_failed', label: '持续失败' },
  { key: 'stable_passed', label: '持续通过' },
] as const
const exclusions = [
  { key: 'new', label: '新增用例' },
  { key: 'baseline_only', label: '仅基线包含' },
  { key: 'definition_changed', label: '定义已变更' },
  { key: 'unassessable', label: '不可判定' },
] as const

const baseline = ref('')
const comparison = ref<EvaluationComparison | null>(null)
const error = ref('')
const busy = ref(false)
let sequence = 0
const candidates = ref<EvaluationRun[]>([])
const nextCursor = ref<string | null>(null)
const choiceBusy = ref(false)
const choiceError = ref(false)
const loadedChoices = ref(false)

/** 从当前账号可见的批次中查找同一执行模式及判定器版本的基线。 */
async function loadChoices(): Promise<void> {
  if (choiceBusy.value || (loadedChoices.value && !nextCursor.value)) return
  choiceBusy.value = true
  choiceError.value = false
  const query = new URLSearchParams({ mode: props.run.mode, limit: '50' })
  if (props.ownerFilter) query.set('owner_id', props.ownerFilter)
  if (nextCursor.value) query.set('cursor', nextCursor.value)
  try {
    const page = await props.api<CursorPage<EvaluationRun>>('/evaluations/runs?' + query)
    candidates.value.push(...page.items.filter((item) => isBaselineCandidate(props.run, item)))
    nextCursor.value = page.next_cursor
    loadedChoices.value = true
  } catch {
    choiceError.value = true
  } finally {
    choiceBusy.value = false
  }
}

/** 加载所选基线或报告附带的比较结果，快速切换时只保留最后一次响应。 */
async function loadComparison(): Promise<void> {
  const version = ++sequence
  error.value = ''
  busy.value = true
  comparison.value = null
  const url = evaluationRunPath(props.run.run_id)
  try {
    const value = baseline.value
      ? await props.api<EvaluationComparison>(
          url + '/comparison?' + new URLSearchParams({ baseline_id: baseline.value }),
        )
      : props.storedComparison
    if (version === sequence) comparison.value = value
  } catch (cause) {
    if (version === sequence)
      error.value = cause instanceof Error ? cause.message : '读取基线对比失败'
  } finally {
    if (version === sequence) busy.value = false
  }
}
watch(baseline, loadComparison, { immediate: true })
onScopeDispose(() => {
  sequence++
})
</script>
