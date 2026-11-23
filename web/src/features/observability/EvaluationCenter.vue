<template>
  <EvaluationRunInspector
    v-if="selected"
    :key="selected.run_id"
    :initial-run="selected"
    :api="api"
    :owner-filter="isAdmin ? owner : null"
    @back="selected = null"
    @inspect="(taskId, traceId) => emit('inspect', taskId, traceId)"
  />
  <section v-else class="panel">
    <div class="section-heading">
      <div>
        <h3>{{ isAdmin ? '企业评测中心' : '我的评测中心' }}</h3>
        <small class="evaluation-meta">
          {{
            evalKind === 'agent'
              ? '多智能体协同调研与工具轨迹评测'
              : '企业知识文档 RAG 检索、对抗与行为评测'
          }}
        </small>
      </div>
      <div class="evaluation-head-actions">
        <el-radio-group v-model="evalKind" size="small" aria-label="切换评测体系">
          <el-radio-button value="agent">AGENT 评测</el-radio-button>
          <el-radio-button value="rag">RAG 评测</el-radio-button>
        </el-radio-group>
        <el-button v-if="isAdmin" type="primary" plain @click="profileDialog = true">
          登记技术栈 / Prompt 版本
        </el-button>
        <el-button v-if="isAdmin" type="primary" @click="openTriggerDialog()">
          选择数据集发起评测
        </el-button>
        <el-button :loading="busy" @click="reload">刷新</el-button>
      </div>
    </div>

    <div class="evaluation-subsection">
      <h4>测试数据集（按版本目录 V1 / V2 管理）</h4>
      <el-table
        v-if="datasets.length"
        :data="datasets"
        row-key="dataset_id"
        class="evaluation-datasets"
        empty-text="当前评测体系还没有登记数据集"
      >
        <el-table-column label="数据集" min-width="220">
          <template #default="{ row }">
            <strong>{{ row.name }}</strong>
            <small class="evaluation-meta">{{ row.dataset_id }} · {{ row.version }}</small>
          </template>
        </el-table-column>
        <el-table-column label="来源" min-width="110">
          <template #default="{ row }">{{ evaluationModeLabel(row.source) }}</template>
        </el-table-column>
        <el-table-column label="用例数" width="85" prop="case_count" />
        <el-table-column label="批次数" width="85" prop="batch_count" />
        <el-table-column label="平均通过率" width="110">
          <template #default="{ row }">{{ formatEvaluationPercent(row.avg_pass_rate) }}</template>
        </el-table-column>
        <el-table-column label="最近批次" min-width="135">
          <template #default="{ row }">{{ traceTime(row.last_run_at) }}</template>
        </el-table-column>
        <el-table-column label="操作" width="175" fixed="right">
          <template #default="{ row }">
            <el-button link type="primary" @click="inspectDataset(row.dataset_id)">
              查看用例
            </el-button>
            <el-button
              link
              :type="selectedDatasetId === row.dataset_id ? 'warning' : 'primary'"
              @click="toggleDatasetFilter(row.dataset_id)"
            >
              {{ selectedDatasetId === row.dataset_id ? '取消筛选' : '筛选批次' }}
            </el-button>
          </template>
        </el-table-column>
      </el-table>
      <p v-else class="evaluation-meta">当前类型暂无已入库数据集。</p>
      <el-alert
        v-if="datasetError"
        type="warning"
        show-icon
        :title="datasetError"
        :closable="false"
      />
    </div>

    <div class="evaluation-subsection">
      <h4>技术栈与 Prompt 调优版本对比（只增不改留痕）</h4>
      <el-table
        v-if="profiles.length"
        :data="profiles"
        row-key="profile_id"
        class="evaluation-datasets"
      >
        <el-table-column label="版本标签" min-width="170">
          <template #default="{ row }">
            <strong>{{ row.version_tag }}</strong>
            <small class="evaluation-meta">{{ row.prompt_sha256.slice(0, 12) }}</small>
          </template>
        </el-table-column>
        <el-table-column label="技术栈" min-width="220">
          <template #default="{ row }">
            <span>{{ formatTechStack(row.tech_stack) }}</span>
            <small class="evaluation-meta">{{ row.notes || '无备注' }}</small>
          </template>
        </el-table-column>
        <el-table-column label="关联批次" width="90" prop="batch_count" />
        <el-table-column label="平均通过率" width="110">
          <template #default="{ row }">{{ formatEvaluationPercent(row.avg_pass_rate) }}</template>
        </el-table-column>
        <el-table-column label="平均延迟" width="100">
          <template #default="{ row }">{{
            formatEvaluationLatency(row.avg_latency_p50_ms)
          }}</template>
        </el-table-column>
        <el-table-column label="操作" width="175" fixed="right">
          <template #default="{ row }">
            <el-button link type="primary" @click="inspectedProfile = row">查看配置</el-button>
            <el-button
              link
              :type="selectedProfileId === row.profile_id ? 'warning' : 'primary'"
              @click="toggleProfileFilter(row.profile_id)"
            >
              {{ selectedProfileId === row.profile_id ? '取消筛选' : '筛选批次' }}
            </el-button>
          </template>
        </el-table-column>
      </el-table>
      <p v-else class="evaluation-meta">当前类型暂未登记技术栈/Prompt 配置版本。</p>
      <el-alert
        v-if="profileError"
        type="warning"
        show-icon
        :title="profileError"
        :closable="false"
      />
    </div>

    <div class="evaluation-filters">
      <TenantOwnerSelect v-if="isAdmin" v-model="owner" :api="api" />
      <el-select v-model="mode" placeholder="全部执行方式" aria-label="按执行方式筛选">
        <el-option value="" label="全部执行方式" />
        <el-option
          v-for="(label, code) in evaluationModes"
          :key="code"
          :value="code"
          :label="label"
        />
      </el-select>
      <el-select v-model="gate" placeholder="全部门禁结果" aria-label="按门禁结果筛选">
        <el-option value="" label="全部门禁结果" />
        <el-option value="true" label="门禁通过" />
        <el-option value="false" label="门禁阻断" />
      </el-select>
    </div>

    <el-alert v-if="error" type="error" show-icon :title="error" :closable="false" />
    <el-table
      v-else
      v-loading="busy"
      :data="rows"
      row-key="run_id"
      empty-text="当前范围没有评测批次"
    >
      <el-table-column label="评测批次" min-width="200">
        <template #default="{ row }">
          <button class="evaluation-link" type="button" @click="selected = row">
            {{ traceTime(row.created_at) }}
          </button>
          <small class="evaluation-meta">{{ row.run_id }}</small>
        </template>
      </el-table-column>
      <el-table-column label="数据集 / 配置版本" min-width="180">
        <template #default="{ row }">
          <span>{{ row.dataset_name || '未登记数据集' }}</span>
          <small class="evaluation-meta">{{ row.profile_version_tag || '未绑定配置版本' }}</small>
        </template>
      </el-table-column>
      <el-table-column label="执行方式" min-width="130">
        <template #default="{ row }">{{ evaluationSource(row) }}</template>
      </el-table-column>
      <el-table-column label="用例结果" min-width="185">
        <template #default="{ row }">
          <strong>{{ row.passed_cases }} / {{ row.total_cases }} 通过</strong>
          <small class="evaluation-meta"
            >{{ row.failed_cases }} 失败 · {{ row.unassessable_cases }} 不可判定</small
          >
        </template>
      </el-table-column>
      <el-table-column label="指标" min-width="170">
        <template #default="{ row }">
          <strong>{{ formatEvaluationPercent(row.pass_rate) }} 通过率</strong>
          <small class="evaluation-meta"
            >均分 {{ formatEvaluationPercent(row.mean_score) }} · P50
            {{ formatEvaluationLatency(row.latency_p50_ms) }}</small
          >
        </template>
      </el-table-column>
      <el-table-column label="发布门禁" width="100">
        <template #default="{ row }">
          <el-tag :type="row.gate_passed ? 'success' : 'danger'" size="small">{{
            row.gate_passed ? '通过' : '阻断'
          }}</el-tag>
        </template>
      </el-table-column>
    </el-table>
    <nav class="evaluation-pager" aria-label="评测批次分页">
      <span>第 {{ page }} 页</span>
      <el-button :disabled="busy || page === 1" @click="turn(false)">上一页</el-button>
      <el-button :disabled="busy || !nextCursor" @click="turn(true)">下一页</el-button>
    </nav>

    <el-drawer
      v-model="datasetDrawer"
      :title="datasetDetail ? `数据集用例：${datasetDetail.dataset.name}` : '数据集用例'"
      size="55%"
    >
      <template v-if="datasetDetail">
        <p class="evaluation-meta">
          版本：{{ datasetDetail.dataset.version }} · 类型：{{ datasetDetail.dataset.eval_kind }} ·
          总数：{{ datasetDetail.dataset.case_count }}
        </p>
        <p v-if="datasetDetail.dataset.description">{{ datasetDetail.dataset.description }}</p>
        <el-table :data="datasetDetail.cases" max-height="520">
          <el-table-column label="用例 ID" width="160">
            <template #default="{ row }">{{ row.id || row.case_id || '-' }}</template>
          </el-table-column>
          <el-table-column label="问题 / 测试输入" min-width="260">
            <template #default="{ row }">{{
              row.question || row.query || row.name || JSON.stringify(row)
            }}</template>
          </el-table-column>
        </el-table>
      </template>
    </el-drawer>

    <el-dialog v-model="profileDialog" title="登记不可变技术栈 / Prompt 版本" width="560px">
      <el-form label-position="top">
        <el-form-item label="版本标签（唯一，如 v2-prompt-strict）">
          <el-input v-model="profileForm.version_tag" placeholder="v1-baseline" />
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

    <el-dialog v-model="triggerDialog" title="选择数据集与配置版本发起评测" width="480px">
      <el-form label-position="top">
        <el-form-item label="选择数据集版本">
          <el-select v-model="triggerForm.dataset_id" style="width: 100%">
            <el-option
              v-for="ds in datasets"
              :key="ds.dataset_id"
              :label="`${ds.name} (${ds.version})`"
              :value="ds.dataset_id"
            />
          </el-select>
        </el-form-item>
        <el-form-item label="选择技术栈 / Prompt 配置版本">
          <el-select v-model="triggerForm.profile_id" style="width: 100%">
            <el-option
              v-for="pf in profiles"
              :key="pf.profile_id"
              :label="`${pf.version_tag} (${pf.prompt_sha256.slice(0, 8)})`"
              :value="pf.profile_id"
            />
          </el-select>
        </el-form-item>
        <el-form-item label="本次评测备注">
          <el-input v-model="triggerForm.note" placeholder="说明本次测试目的" />
        </el-form-item>
        <el-alert v-if="writeError" type="error" :title="writeError" :closable="false" />
      </el-form>
      <template #footer>
        <el-button @click="triggerDialog = false">取消</el-button>
        <el-button type="primary" :loading="writeBusy" @click="submitTrigger">确认发起</el-button>
      </template>
    </el-dialog>

    <el-dialog
      :model-value="Boolean(inspectedProfile)"
      title="技术栈与 Prompt 快照详情"
      width="600px"
      @close="inspectedProfile = null"
    >
      <pre v-if="inspectedProfile" class="evaluation-json">{{
        JSON.stringify(
          {
            version_tag: inspectedProfile.version_tag,
            prompt_sha256: inspectedProfile.prompt_sha256,
            tech_stack: inspectedProfile.tech_stack,
            retrieval_params: inspectedProfile.retrieval_params,
            prompt_snapshots: inspectedProfile.prompt_snapshots,
          },
          null,
          2,
        )
      }}</pre>
    </el-dialog>
  </section>
</template>

<script setup lang="ts">
import { computed, onMounted, reactive, ref, watch } from 'vue'
import type { ApiClient, Items } from '../../shared/apiTypes'
import { useCursorPage } from '../../shared/useCursorPage'
import {
  evaluationModes,
  evaluationModeLabel,
  evaluationSource,
  formatEvaluationLatency,
  formatEvaluationPercent,
  type EvaluationDataset,
  type EvaluationDatasetDetail,
  type EvaluationKind,
  type EvaluationMode,
  type EvaluationProfile,
  type EvaluationRun,
  type EvaluationTriggerReceipt,
} from './evaluationTypes'
import { traceTime } from '../../shared/labels'
import EvaluationRunInspector from './EvaluationRunInspector.vue'
import TenantOwnerSelect from './TenantOwnerSelect.vue'
import './evaluations.css'

const props = defineProps<{ isAdmin: boolean; api: ApiClient }>()
const emit = defineEmits<{ (e: 'inspect', taskId: string, traceId: string): void }>()
const evalKind = ref<EvaluationKind>('agent')
const selected = ref<EvaluationRun | null>(null)
const owner = ref('all')
const mode = ref<EvaluationMode | ''>('')
const gate = ref('')
const selectedDatasetId = ref('')
const selectedProfileId = ref('')
const datasets = ref<EvaluationDataset[]>([])
const profiles = ref<EvaluationProfile[]>([])
const datasetError = ref('')
const profileError = ref('')
const datasetDrawer = ref(false)
const datasetDetail = ref<EvaluationDatasetDetail | null>(null)
const inspectedProfile = ref<EvaluationProfile | null>(null)
const profileDialog = ref(false)
const triggerDialog = ref(false)
const writeBusy = ref(false)
const writeError = ref('')
const profileForm = reactive({
  version_tag: '',
  tech_stack_text: '{"model":"sensenova-6.8-flash","orchestration":"langgraph"}',
  retrieval_params_text: '{}',
  prompt_snapshots_text: '{"system":""}',
  notes: '',
})
const triggerForm = reactive({ dataset_id: '', profile_id: '', note: '' })

const endpoint = computed(() => {
  const query = new URLSearchParams({ limit: '25', eval_kind: evalKind.value })
  if (props.isAdmin) query.set('owner_id', owner.value)
  if (mode.value) query.set('mode', mode.value)
  if (gate.value) query.set('gate_passed', gate.value)
  if (selectedDatasetId.value) query.set('dataset_id', selectedDatasetId.value)
  if (selectedProfileId.value) query.set('profile_id', selectedProfileId.value)
  return '/evaluations/runs?' + query
})
const { rows, busy, error, page, nextCursor, refresh, reset, turn } = useCursorPage<EvaluationRun>(
  props.api,
  endpoint,
)

/**
 * 将技术栈 JSON 摘要为单行文本。
 *
 * @param stack 技术栈配置对象
 * @returns 摘要字符串
 */
function formatTechStack(stack: Record<string, unknown>): string {
  return (
    Object.entries(stack)
      .map(([k, v]) => `${k}=${String(v)}`)
      .join(' · ') || '-'
  )
}

/** 读取当前评测类型下的数据集列表。 */
async function loadDatasets(): Promise<void> {
  datasetError.value = ''
  try {
    datasets.value = (
      await props.api<Items<EvaluationDataset>>(`/evaluations/datasets?eval_kind=${evalKind.value}`)
    ).items
  } catch (cause) {
    datasetError.value = cause instanceof Error ? cause.message : '读取数据集失败'
  }
}

/** 读取当前评测类型下的不可变技术栈与 Prompt 版本列表。 */
async function loadProfiles(): Promise<void> {
  profileError.value = ''
  try {
    profiles.value = (
      await props.api<Items<EvaluationProfile>>(`/evaluations/profiles?eval_kind=${evalKind.value}`)
    ).items
  } catch (cause) {
    profileError.value = cause instanceof Error ? cause.message : '读取配置版本失败'
  }
}

/**
 * 查看指定数据集的版本元信息与用例内容。
 *
 * @param datasetId 数据集 ID
 */
async function inspectDataset(datasetId: string): Promise<void> {
  datasetDetail.value = await props.api<EvaluationDatasetDetail>(
    `/evaluations/datasets/${datasetId}`,
  )
  datasetDrawer.value = true
}

/**
 * 切换按数据集过滤下方批次列表。
 *
 * @param datasetId 数据集 ID
 */
function toggleDatasetFilter(datasetId: string): void {
  selectedDatasetId.value = selectedDatasetId.value === datasetId ? '' : datasetId
}

/**
 * 切换按技术栈配置版本过滤下方批次列表。
 *
 * @param profileId 技术栈版本 ID
 */
function toggleProfileFilter(profileId: string): void {
  selectedProfileId.value = selectedProfileId.value === profileId ? '' : profileId
}

/** 打开受控评测发起弹窗并预选当前首项。 */
function openTriggerDialog(): void {
  writeError.value = ''
  triggerForm.dataset_id = selectedDatasetId.value || datasets.value[0]?.dataset_id || ''
  triggerForm.profile_id = selectedProfileId.value || profiles.value[0]?.profile_id || ''
  triggerForm.note = ''
  triggerDialog.value = true
}

/** 登记新的不可变技术栈与 Prompt 版本。 */
async function submitProfile(): Promise<void> {
  writeBusy.value = true
  writeError.value = ''
  try {
    await props.api<EvaluationProfile>('/evaluations/profiles', 'POST', {
      eval_kind: evalKind.value,
      version_tag: profileForm.version_tag.trim(),
      tech_stack: JSON.parse(profileForm.tech_stack_text || '{}'),
      retrieval_params: JSON.parse(profileForm.retrieval_params_text || '{}'),
      prompt_snapshots: JSON.parse(profileForm.prompt_snapshots_text || '{}'),
      notes: profileForm.notes.trim(),
    })
    profileDialog.value = false
    await loadProfiles()
  } catch (cause) {
    writeError.value = cause instanceof Error ? cause.message : '登记版本失败'
  } finally {
    writeBusy.value = false
  }
}

/** 选择数据集与配置版本发起受控评测请求。 */
async function submitTrigger(): Promise<void> {
  writeBusy.value = true
  writeError.value = ''
  try {
    await props.api<EvaluationTriggerReceipt>('/evaluations/runs/trigger', 'POST', {
      eval_kind: evalKind.value,
      dataset_id: triggerForm.dataset_id,
      profile_id: triggerForm.profile_id,
      note: triggerForm.note.trim(),
    })
    triggerDialog.value = false
    await reload()
  } catch (cause) {
    writeError.value = cause instanceof Error ? cause.message : '发起评测失败'
  } finally {
    writeBusy.value = false
  }
}

/** 同时刷新数据集、配置版本与当前批次页。 */
async function reload(): Promise<void> {
  await Promise.all([refresh(), loadDatasets(), loadProfiles()])
}

watch(evalKind, async () => {
  selectedDatasetId.value = ''
  selectedProfileId.value = ''
  await Promise.all([loadDatasets(), loadProfiles()])
})
onMounted(() => Promise.all([loadDatasets(), loadProfiles()]))
watch(endpoint, reset)
</script>
