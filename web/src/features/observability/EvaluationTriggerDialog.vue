<template>
  <el-dialog v-model="rerunDialog" title="复跑评测批次" width="560px">
    <el-form label-position="top">
      <el-form-item label="复跑来源批次">
        <span>{{ rerunRun ? rerunRun.run_id : '—' }}</span>
        <small class="evaluation-meta">
          数据集版本 {{ rerunRun ? rerunRun.dataset_version || '—' : '—' }} · 算法版本
          {{ rerunRun ? rerunRun.profile_version_tag || '未绑定' : '—' }} · 口径
          {{ rerunRun ? evaluationScopeLabel(rerunRun.scope) : '—' }} · 执行方式
          {{ rerunRun ? evaluationModeLabel(rerunRun.mode) : '—' }}
        </small>
      </el-form-item>
      <p class="evaluation-meta">
        复跑沿用该批次的数据集、口径、执行方式与算法版本，并带上重跑来源；提交后同一数据集、口径与执行方式下将记为第
        {{ rerunNextSeq }} 次运行。
      </p>
      <el-form-item label="运行备注（写入新批次 trigger_note）">
        <el-input
          v-model="rerunNote"
          maxlength="500"
          show-word-limit
          placeholder="说明本次复跑目的，例如：同数据集第二次运行"
        />
      </el-form-item>
      <el-alert v-if="rerunError" type="error" :title="rerunError" :closable="false" />
    </el-form>
    <template #footer>
      <el-button @click="rerunDialog = false">取消</el-button>
      <el-button type="primary" :loading="rerunBusy" @click="submitRerun">确认复跑</el-button>
    </template>
  </el-dialog>

  <el-dialog
    v-model="profileDialog"
    title="登记不可变技术栈 / Prompt 版本"
    width="560px"
    :z-index="3000"
  >
    <el-form label-position="top">
      <el-form-item label="版本标签（唯一；留空按序号自动编号）">
        <el-input v-model="profileForm.version_tag" placeholder="留空自动编号，如 algo-v3" />
      </el-form-item>
      <el-form-item label="技术栈 JSON（模型、编排、检索组件）">
        <el-input v-model="profileForm.tech_stack_text" type="textarea" :rows="3" />
      </el-form-item>
      <el-form-item label="检索与超参数 JSON">
        <el-input v-model="profileForm.retrieval_params_text" type="textarea" :rows="2" />
      </el-form-item>
      <el-form-item label="Prompt 调优快照 JSON">
        <el-input v-model="profileForm.prompt_snapshots_text" type="textarea" :rows="4" />
      </el-form-item>
      <el-form-item label="调优说明">
        <el-input v-model="profileForm.notes" placeholder="记录本次技术栈与 Prompt 改动目的" />
      </el-form-item>
      <el-alert v-if="writeError" type="error" :title="writeError" :closable="false" />
    </el-form>
    <template #footer>
      <el-button @click="profileDialog = false">取消</el-button>
      <el-button type="primary" :loading="writeBusy" @click="submitProfile">确认登记</el-button>
    </template>
  </el-dialog>

  <el-dialog v-model="triggerDialog" title="选择数据集与算法版本发起评测" width="560px">
    <el-form label-position="top">
      <el-form-item label="评测数据集版本">
        <el-select v-model="triggerForm.dataset_id" style="width: 100%">
          <el-option
            v-for="dataset in triggerDatasets"
            :key="dataset.dataset_id"
            :label="`${dataset.name}（${dataset.version}）`"
            :value="dataset.dataset_id"
          />
        </el-select>
        <small class="evaluation-meta"
          >支持口径：{{ triggerScopeLabels }} · 执行方式：{{ triggerModeLabels }}</small
        >
      </el-form-item>
      <el-form-item label="算法版本（可留空自动快照）">
        <el-select
          v-model="triggerForm.profile_id"
          clearable
          value-on-clear=""
          placeholder="留空：自动生成算法版本快照"
          style="width: 100%"
        >
          <el-option label="自动生成算法版本快照" value="" />
          <el-option
            v-for="profile in profiles"
            :key="profile.profile_id"
            :label="`V${profile.version_no} · ${profile.version_tag}`"
            :value="profile.profile_id"
          />
        </el-select>
      </el-form-item>
      <el-form-item label="评测口径（选项来自指标目录）">
        <el-select v-model="triggerForm.scope" style="width: 100%">
          <el-option
            v-for="item in triggerScopeOptions"
            :key="item"
            :value="item"
            :label="evaluationScopes[item]"
          />
        </el-select>
      </el-form-item>
      <el-form-item label="执行方式（按数据集登记能力过滤）">
        <el-select v-model="triggerForm.mode" style="width: 100%">
          <el-option
            v-for="item in triggerModeOptions"
            :key="item"
            :value="item"
            :label="evaluationModes[item]"
          />
        </el-select>
      </el-form-item>
      <el-form-item label="本次运行备注（写入批次 trigger_note）">
        <el-input
          v-model="triggerForm.note"
          maxlength="500"
          show-word-limit
          placeholder="说明本次运行目的，可用于区分同一数据集多次运行"
        />
      </el-form-item>
      <el-form-item>
        <el-checkbox v-model="triggerForm.mark_baseline">评测完成后同时设为基线</el-checkbox>
      </el-form-item>
      <el-alert v-if="writeError" type="error" :title="writeError" :closable="false" />
    </el-form>
    <template #footer>
      <el-button @click="triggerDialog = false">取消</el-button>
      <el-button type="primary" :loading="writeBusy" @click="submitTrigger">确认发起</el-button>
    </template>
  </el-dialog>

  <el-dialog v-model="baselineDialog" title="设为评测基线" width="500px" :z-index="3000">
    <el-form label-position="top">
      <el-form-item label="基线批次">
        <span>{{ baselineRun ? baselineRun.run_id : '—' }}</span>
        <small class="evaluation-meta">
          数据集版本 {{ baselineRun ? baselineRun.dataset_version || '—' : '—' }} · 口径
          {{ baselineRun ? evaluationScopeLabel(baselineRun.scope) : '—' }} · 执行方式
          {{ baselineRun ? evaluationModeLabel(baselineRun.mode) : '—' }}
        </small>
      </el-form-item>
      <el-form-item label="基线备注（必填）">
        <el-input
          v-model="baselineNote"
          maxlength="500"
          show-word-limit
          placeholder="说明为什么将该批次作为该数据集版本 + 口径 + 执行方式的当前基线"
        />
      </el-form-item>
      <el-alert
        v-if="baselineWriteError"
        type="error"
        :title="baselineWriteError"
        :closable="false"
      />
    </el-form>
    <template #footer>
      <el-button @click="baselineDialog = false">取消</el-button>
      <el-button type="primary" :loading="writeBusy" @click="submitBaseline"
        >确认设为基线</el-button
      >
    </template>
  </el-dialog>
</template>

<script setup lang="ts">
import { computed, reactive, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import type { ApiClient } from '../../shared/apiTypes'
import {
  evaluationModeLabel,
  evaluationModes,
  evaluationScopeLabel,
  evaluationScopes,
  type EvaluationBaseline,
  type EvaluationDataset,
  type EvaluationKind,
  type EvaluationMode,
  type EvaluationProfile,
  type EvaluationRun,
  type EvaluationScope,
  type EvaluationTriggerReceipt,
  type TriggerEvaluationPayload,
} from './evaluationTypes'
import { datasetExecutionModes, datasetScopeList, parseJsonObject } from './evaluationHelpers'

const props = defineProps<{
  api: ApiClient
  evalKind: EvaluationKind
  scope: EvaluationScope
  mode: EvaluationMode
  runDatasetId: string
  profiles: EvaluationProfile[]
  triggerDatasets: EvaluationDataset[]
  scopeOptions: { value: EvaluationScope; label: string }[]
}>()
const emit = defineEmits<{
  (e: 'profilesChanged'): void
  (e: 'triggered', receipt: EvaluationTriggerReceipt): void
  (e: 'baselineSet'): void
}>()

const profileDialog = ref(false)
const triggerDialog = ref(false)
const rerunDialog = ref(false)
const baselineDialog = ref(false)
const writeBusy = ref(false)
const writeError = ref('')
const baselineWriteError = ref('')
const baselineRun = ref<EvaluationRun | null>(null)
const baselineNote = ref('')
const rerunRun = ref<EvaluationRun | null>(null)
const rerunNote = ref('')
const rerunBusy = ref(false)
const rerunError = ref('')
const profileForm = reactive({
  version_tag: '',
  tech_stack_text: '{"model":"sensenova-6.8-flash","orchestration":"langgraph"}',
  retrieval_params_text: '{}',
  prompt_snapshots_text: '{"system":""}',
  notes: '',
})
const triggerForm = reactive({
  dataset_id: '',
  profile_id: '',
  scope: 'full' as EvaluationScope,
  mode: 'live' as EvaluationMode,
  note: '',
  mark_baseline: false,
})

const triggerSelectedDataset = computed(
  () => props.triggerDatasets.find((item) => item.dataset_id === triggerForm.dataset_id) ?? null,
)
const triggerScopeOptions = computed<EvaluationScope[]>(() => {
  const registered = datasetScopeList(triggerSelectedDataset.value)
  const known = new Set(props.scopeOptions.map((item) => item.value))
  const filtered = registered.filter((item) => known.has(item))
  return filtered.length ? filtered : props.scopeOptions.map((item) => item.value)
})
const triggerModeOptions = computed<EvaluationMode[]>(() =>
  datasetExecutionModes(triggerSelectedDataset.value),
)
const triggerScopeLabels = computed(
  () => triggerScopeOptions.value.map((item) => evaluationScopes[item]).join('、') || '未登记',
)
const triggerModeLabels = computed(
  () => triggerModeOptions.value.map((item) => evaluationModes[item]).join('、') || '未登记',
)
const rerunNextSeq = computed(() => (rerunRun.value?.run_seq ?? 0) + 1)

/**
 * 打开算法版本登记弹窗并清空上次的写入错误。
 */
function openProfile(): void {
  writeError.value = ''
  profileDialog.value = true
}

/**
 * 打开受控评测发起弹窗，并按所选数据集登记能力预选口径与执行方式。
 * @param dataset - 从数据集页直接发起时预选的数据集。
 */
function openTrigger(dataset?: EvaluationDataset): void {
  writeError.value = ''
  const preferred =
    dataset?.dataset_id || props.runDatasetId || props.triggerDatasets[0]?.dataset_id
  triggerForm.dataset_id = preferred || ''
  triggerForm.profile_id = ''
  triggerForm.note = ''
  triggerForm.mark_baseline = false
  const selected = props.triggerDatasets.find((item) => item.dataset_id === triggerForm.dataset_id)
  const scopes = datasetScopeList(selected ?? null)
  const modes = datasetExecutionModes(selected ?? null)
  triggerForm.scope = scopes.includes(props.scope)
    ? props.scope
    : scopes.includes('full')
      ? 'full'
      : (scopes[0] ?? props.scope)
  triggerForm.mode = modes.includes(props.mode) ? props.mode : (modes[0] ?? props.mode)
  triggerDialog.value = true
}

/**
 * 登记新的不可变技术栈与 Prompt 版本。
 */
async function submitProfile(): Promise<void> {
  writeBusy.value = true
  writeError.value = ''
  try {
    const created = await props.api<EvaluationProfile>('/evaluations/profiles', 'POST', {
      eval_kind: props.evalKind,
      version_tag: profileForm.version_tag.trim(),
      tech_stack: parseJsonObject(profileForm.tech_stack_text, '技术栈 JSON'),
      retrieval_params: parseJsonObject(profileForm.retrieval_params_text, '检索参数 JSON'),
      prompt_snapshots: parseJsonObject(profileForm.prompt_snapshots_text, 'Prompt 快照 JSON'),
      notes: profileForm.notes.trim(),
    })
    profileDialog.value = false
    ElMessage.success(
      created.created
        ? `已登记算法版本 ${created.version_tag}`
        : `算法快照与已有版本 ${created.version_tag} 一致，已复用`,
    )
    emit('profilesChanged')
  } catch (cause) {
    writeError.value = cause instanceof Error ? cause.message : '登记版本失败'
  } finally {
    writeBusy.value = false
  }
}

/**
 * 选择数据集与算法版本发起真实评测任务，并提示任务号与审计号。
 */
async function submitTrigger(): Promise<void> {
  if (!triggerForm.dataset_id) {
    writeError.value = '请选择评测数据集版本'
    return
  }
  writeBusy.value = true
  writeError.value = ''
  try {
    const payload: TriggerEvaluationPayload = {
      eval_kind: props.evalKind,
      dataset_id: triggerForm.dataset_id,
      profile_id: triggerForm.profile_id,
      scope: triggerForm.scope,
      mode: triggerForm.mode,
      note: triggerForm.note.trim(),
      mark_baseline: triggerForm.mark_baseline,
    }
    const receipt = await props.api<EvaluationTriggerReceipt>(
      '/evaluations/runs/trigger',
      'POST',
      payload,
    )
    triggerDialog.value = false
    emit('triggered', receipt)
    ElMessage.success(
      `已提交评测任务（任务号 ${receipt.job_id}，审计号 ${receipt.audit_id}），由 Worker 执行`,
    )
  } catch (cause) {
    writeError.value = cause instanceof Error ? cause.message : '发起评测失败'
  } finally {
    writeBusy.value = false
  }
}

/**
 * 打开复跑弹窗，沿用来源批次的数据集、口径、执行方式与算法版本。
 * @param run - 要复跑的来源批次。
 */
function openRerun(run: EvaluationRun): void {
  if (!run.dataset_id) return
  rerunRun.value = run
  rerunNote.value = ''
  rerunError.value = ''
  rerunDialog.value = true
}

/**
 * 以来源批次的配置发起复跑，并带上 parent_run_id 与运行备注。
 */
async function submitRerun(): Promise<void> {
  const run = rerunRun.value
  if (!run || !run.dataset_id) {
    rerunError.value = '该批次没有关联数据集，无法复跑'
    return
  }
  rerunBusy.value = true
  rerunError.value = ''
  try {
    const payload: TriggerEvaluationPayload = {
      eval_kind: run.eval_kind,
      dataset_id: run.dataset_id,
      profile_id: run.profile_id ?? '',
      scope: run.scope,
      mode: run.mode,
      note: rerunNote.value.trim(),
      parent_run_id: run.run_id,
      mark_baseline: false,
    }
    const receipt = await props.api<EvaluationTriggerReceipt>(
      '/evaluations/runs/trigger',
      'POST',
      payload,
    )
    rerunDialog.value = false
    emit('triggered', receipt)
    ElMessage.success(
      `已提交复跑任务（任务号 ${receipt.job_id}），完成后将记为第 ${rerunNextSeq.value} 次运行`,
    )
  } catch (cause) {
    rerunError.value = cause instanceof Error ? cause.message : '复跑评测失败'
  } finally {
    rerunBusy.value = false
  }
}

/**
 * 打开把批次设为基线的弹窗。
 * @param run - 待设为基线的批次。
 */
function openBaseline(run: EvaluationRun): void {
  baselineRun.value = run
  baselineNote.value = ''
  baselineWriteError.value = ''
  baselineDialog.value = true
}

/**
 * 把所选批次设为该数据集版本 + 口径 + 执行方式的活跃基线。
 */
async function submitBaseline(): Promise<void> {
  const run = baselineRun.value
  if (!run) return
  if (!baselineNote.value.trim()) {
    baselineWriteError.value = '请填写基线备注，说明该批次被选为基线的依据'
    return
  }
  writeBusy.value = true
  baselineWriteError.value = ''
  try {
    await props.api<EvaluationBaseline>('/evaluations/baselines', 'POST', {
      run_id: run.run_id,
      note: baselineNote.value.trim(),
    })
    baselineDialog.value = false
    ElMessage.success('已把该批次设为当前活跃基线')
    emit('baselineSet')
  } catch (cause) {
    baselineWriteError.value = cause instanceof Error ? cause.message : '设置基线失败'
  } finally {
    writeBusy.value = false
  }
}

watch(
  () => triggerForm.dataset_id,
  () => {
    const selected = triggerSelectedDataset.value
    const scopes = datasetScopeList(selected)
    const modes = datasetExecutionModes(selected)
    if (scopes.length && !scopes.includes(triggerForm.scope)) triggerForm.scope = scopes[0]
    if (modes.length && !modes.includes(triggerForm.mode)) triggerForm.mode = modes[0]
  },
)

defineExpose({ openProfile, openTrigger, openRerun, openBaseline })
</script>
