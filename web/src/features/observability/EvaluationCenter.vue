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
        <el-button v-if="canWriteEval" plain @click="openAuditDrawer">审计留痕</el-button>
        <el-button v-if="canWriteEval" type="primary" plain @click="openProfileDialog">
          登记技术栈 / Prompt 版本
        </el-button>
        <el-button v-if="canWriteEval" type="primary" @click="openTriggerDialog">
          选择数据集发起评测
        </el-button>
        <el-button :loading="busy" @click="reload">刷新</el-button>
      </div>
    </div>

    <div class="evaluation-subsection evaluation-overview">
      <div class="evaluation-subsection-head">
        <h4>指标概览</h4>
        <div class="evaluation-filters">
          <el-select
            v-model="datasetVersion"
            placeholder="全部数据集版本"
            aria-label="按数据集版本筛选"
          >
            <el-option value="" label="全部数据集版本" />
            <el-option
              v-for="version in datasetVersions"
              :key="version"
              :value="version"
              :label="version"
            />
          </el-select>
          <el-select
            v-model="trendScope"
            placeholder="全部评测口径"
            aria-label="按评测口径筛选趋势"
          >
            <el-option value="" label="全部评测口径" />
            <el-option
              v-for="(label, code) in evaluationScopes"
              :key="code"
              :value="code"
              :label="label"
            />
          </el-select>
          <el-select v-model="trendMode" placeholder="全部执行方式" aria-label="按执行方式筛选趋势">
            <el-option value="" label="全部执行方式" />
            <el-option
              v-for="(label, code) in evaluationModes"
              :key="code"
              :value="code"
              :label="label"
            />
          </el-select>
        </div>
      </div>
      <el-alert v-if="trendError" type="warning" show-icon :title="trendError" :closable="false" />
      <el-skeleton v-else-if="!trends" :rows="3" animated />
      <template v-else>
        <div class="evaluation-overview-grid">
          <div class="evaluation-overview-card">
            <span class="evaluation-card-label">活跃基线</span>
            <template v-if="trends.baseline">
              <button
                class="evaluation-link"
                type="button"
                @click="openRunById(trends.baseline.run_id)"
              >
                {{ trends.baseline.run_id }}
              </button>
              <span class="evaluation-overview-value">{{
                formatEvaluationPercent(trends.baseline.pass_rate)
              }}</span>
              <el-tag :type="trends.baseline.gate_passed ? 'success' : 'danger'" size="small">
                {{ trends.baseline.gate_passed ? '门禁通过' : '门禁阻断' }}
              </el-tag>
              <small class="evaluation-meta"
                >{{ trends.baseline.note || '未填写基线备注' }} ·
                {{ traceTime(trends.baseline.created_at) }}</small
              >
            </template>
            <p v-else class="evaluation-muted">当前筛选范围还没有活跃基线。</p>
          </div>
          <dl class="evaluation-overview-card">
            <dt>批次数</dt>
            <dd>{{ trends.aggregate.batches }}</dd>
          </dl>
          <dl class="evaluation-overview-card">
            <dt>平均通过率</dt>
            <dd>{{ formatEvaluationPercent(trends.aggregate.avg_pass_rate) }}</dd>
          </dl>
          <dl class="evaluation-overview-card">
            <dt>最佳通过率</dt>
            <dd>{{ formatEvaluationPercent(trends.aggregate.best_pass_rate) }}</dd>
          </dl>
          <dl class="evaluation-overview-card">
            <dt>最近通过率</dt>
            <dd>{{ formatEvaluationPercent(trends.aggregate.latest_pass_rate) }}</dd>
          </dl>
        </div>
        <el-table
          v-if="trends.items.length"
          :data="trends.items"
          row-key="run_id"
          class="evaluation-datasets"
          empty-text="当前筛选范围还没有批次"
        >
          <el-table-column label="批次时间" min-width="150">
            <template #default="{ row }">
              <button class="evaluation-link" type="button" @click="openRunById(row.run_id)">
                {{ traceTime(row.created_at) }}
              </button>
              <small class="evaluation-meta">
                {{ row.run_id }}<template v-if="row.is_baseline"> · 当前基线</template>
              </small>
            </template>
          </el-table-column>
          <el-table-column label="口径" width="90">
            <template #default="{ row }">{{ evaluationScopeLabel(row.scope) }}</template>
          </el-table-column>
          <el-table-column label="执行方式" min-width="130">
            <template #default="{ row }">{{ evaluationModeLabel(row.mode) }}</template>
          </el-table-column>
          <el-table-column label="数据集版本" width="120">
            <template #default="{ row }">{{ row.dataset_version || '—' }}</template>
          </el-table-column>
          <el-table-column label="算法版本" min-width="130">
            <template #default="{ row }">{{ row.algorithm_version || '未绑定算法版本' }}</template>
          </el-table-column>
          <el-table-column label="通过率" width="100">
            <template #default="{ row }">{{ formatEvaluationPercent(row.pass_rate) }}</template>
          </el-table-column>
          <el-table-column label="相对基线" width="110">
            <template #default="{ row }">
              <span v-if="row.comparable" :class="evaluationDeltaTone(row.pass_rate_delta)">
                {{ formatEvaluationDelta(row.pass_rate_delta) }}
              </span>
              <template v-else>
                <el-tag type="info" size="small">不可比</el-tag>
                <span class="evaluation-delta-flat">—</span>
              </template>
            </template>
          </el-table-column>
          <el-table-column label="门禁" width="90">
            <template #default="{ row }">
              <el-tag :type="row.gate_passed ? 'success' : 'danger'" size="small">
                {{ row.gate_passed ? '通过' : '阻断' }}
              </el-tag>
            </template>
          </el-table-column>
          <el-table-column label="P50 / P95 延迟" min-width="130">
            <template #default="{ row }">
              {{ formatEvaluationLatency(row.latency_p50_ms) }} /
              {{ formatEvaluationLatency(row.latency_p95_ms) }}
            </template>
          </el-table-column>
        </el-table>
        <p v-else class="evaluation-meta">当前筛选范围还没有可比较的批次。</p>
      </template>
    </div>

    <div v-if="activeJob || jobError" class="evaluation-subsection evaluation-job">
      <div class="evaluation-subsection-head">
        <h4>评测任务</h4>
        <el-button
          v-if="canWriteEval && activeJob && !jobTerminal"
          :loading="jobBusy"
          @click="cancelJob"
        >
          取消任务
        </el-button>
      </div>
      <el-alert v-if="jobError" type="error" show-icon :title="jobError" :closable="false" />
      <template v-if="activeJob">
        <div class="evaluation-job-head">
          <strong>{{ activeJob.job_id }}</strong>
          <el-tag :type="stateTag(activeJob.state)" size="small">{{
            stateText(activeJob.state)
          }}</el-tag>
          <span class="evaluation-meta">{{ activeJob.stage || '等待 Worker 调度' }}</span>
        </div>
        <el-progress :percentage="activeJob.progress" :status="jobProgressStatus" />
        <p class="evaluation-meta">
          创建 {{ traceTime(activeJob.created_at)
          }}<template v-if="activeJob.started_at">
            · 开始 {{ traceTime(activeJob.started_at) }}</template
          >
          <template v-if="activeJob.finished_at">
            · 结束 {{ traceTime(activeJob.finished_at) }}</template
          >
        </p>
        <el-alert v-if="activeJob.error" type="error" :title="activeJob.error" :closable="false" />
        <p v-if="jobRunId" class="evaluation-meta">
          评测批次已入库：
          <button class="evaluation-link" type="button" @click="openRunById(jobRunId)">
            {{ jobRunId }}
          </button>
        </p>
      </template>
    </div>

    <div class="evaluation-subsection">
      <div class="evaluation-subsection-head">
        <h4>测试数据集（按版本目录管理）</h4>
        <el-switch v-model="showArchived" active-text="显示历史版本" />
      </div>
      <el-table
        v-if="datasets.length"
        :data="datasets"
        row-key="dataset_id"
        class="evaluation-datasets"
        empty-text="当前评测体系还没有登记数据集"
      >
        <el-table-column label="数据集" min-width="230">
          <template #default="{ row }">
            <strong>{{ row.name }}</strong>
            <small class="evaluation-meta">{{ row.dataset_id }}</small>
            <small v-if="row.archived" class="evaluation-meta">
              <el-tag type="info" size="small" class="evaluation-archived-tag"
                >历史版本·仅归档</el-tag
              >
            </small>
          </template>
        </el-table-column>
        <el-table-column label="数据集版本" width="130">
          <template #default="{ row }">
            <el-tag effect="plain" size="small">{{ row.version }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="内容摘要" width="130">
          <template #default="{ row }">
            <span :title="row.content_sha256 || '未登记内容摘要'">
              {{ row.content_sha256 ? row.content_sha256.slice(0, 12) : '—' }}
            </span>
          </template>
        </el-table-column>
        <el-table-column label="分组" width="110">
          <template #default="{ row }">{{
            evaluationSuites[row.suite] || row.suite || '—'
          }}</template>
        </el-table-column>
        <el-table-column label="来源" min-width="120">
          <template #default="{ row }">{{ evaluationModeLabel(row.source) }}</template>
        </el-table-column>
        <el-table-column label="用例数" width="90" prop="case_count" />
        <el-table-column label="批次数" width="85" prop="batch_count" />
        <el-table-column label="平均通过率" width="110">
          <template #default="{ row }">{{ formatEvaluationPercent(row.avg_pass_rate) }}</template>
        </el-table-column>
        <el-table-column label="最近批次" min-width="135">
          <template #default="{ row }">{{ traceTime(row.last_run_at) }}</template>
        </el-table-column>
        <el-table-column label="操作" width="175" fixed="right">
          <template #default="{ row }">
            <el-button link type="primary" @click="inspectDataset(row)">查看用例</el-button>
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
      <h4>算法版本（技术栈 / 检索参数 / Prompt 快照，只增不改留痕）</h4>
      <el-table
        v-if="profiles.length"
        :data="profiles"
        row-key="profile_id"
        class="evaluation-datasets"
      >
        <el-table-column label="版本" min-width="190">
          <template #default="{ row }">
            <strong>V{{ row.version_no }} · {{ row.version_tag }}</strong>
            <small class="evaluation-meta">{{ row.notes || '无备注' }}</small>
          </template>
        </el-table-column>
        <el-table-column label="版本摘要" width="140">
          <template #default="{ row }">
            <span :title="row.digest || '未登记摘要'">
              {{ row.digest ? row.digest.slice(0, 12) : '—' }}
            </span>
          </template>
        </el-table-column>
        <el-table-column label="代码提交" width="110">
          <template #default="{ row }">
            <span :title="row.git_sha || '未记录代码提交'">
              {{ row.git_sha ? row.git_sha.slice(0, 8) : '未记录' }}
            </span>
          </template>
        </el-table-column>
        <el-table-column label="数据集版本" width="120">
          <template #default="{ row }">{{ row.dataset_version || '—' }}</template>
        </el-table-column>
        <el-table-column label="技术栈" min-width="200">
          <template #default="{ row }">
            <span>{{ formatTechStack(row.tech_stack) }}</span>
            <small class="evaluation-meta">{{
              row.prompt_sha256 ? row.prompt_sha256.slice(0, 12) : '未登记 Prompt 摘要'
            }}</small>
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
        <el-table-column label="平均 Token" width="110">
          <template #default="{ row }">{{
            row.avg_tokens === null ? '—' : Math.round(row.avg_tokens)
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

    <div class="evaluation-subsection">
      <div class="evaluation-subsection-head">
        <h4>评测基线（按数据集版本 + 口径 + 执行方式唯一）</h4>
        <el-switch v-model="baselineActiveOnly" active-text="只看 active" />
      </div>
      <el-alert
        v-if="baselineError"
        type="warning"
        show-icon
        :title="baselineError"
        :closable="false"
      />
      <el-table
        v-if="activeBaselines.length"
        :data="activeBaselines"
        row-key="baseline_id"
        class="evaluation-datasets"
        :row-class-name="baselineRowClass"
        empty-text="当前筛选范围没有活跃基线"
      >
        <el-table-column label="口径" width="95">
          <template #default="{ row }">
            <el-tag effect="plain" size="small">{{ evaluationScopeLabel(row.scope) }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="执行方式" min-width="130">
          <template #default="{ row }">{{ evaluationModeLabel(row.mode) }}</template>
        </el-table-column>
        <el-table-column label="数据集版本" width="120">
          <template #default="{ row }">{{ row.dataset_version }}</template>
        </el-table-column>
        <el-table-column label="批次" min-width="215">
          <template #default="{ row }">
            <button class="evaluation-link" type="button" @click="openRunById(row.run_id)">
              {{ traceTime(row.run_created_at) }}
            </button>
            <small class="evaluation-meta">{{ row.run_id }}</small>
          </template>
        </el-table-column>
        <el-table-column label="通过率" width="100">
          <template #default="{ row }">{{ formatEvaluationPercent(row.run_pass_rate) }}</template>
        </el-table-column>
        <el-table-column label="门禁" width="90">
          <template #default="{ row }">
            <el-tag :type="row.run_gate_passed ? 'success' : 'danger'" size="small">
              {{ row.run_gate_passed ? '通过' : '阻断' }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column label="算法版本" min-width="130">
          <template #default="{ row }">{{ row.algorithm_version || '—' }}</template>
        </el-table-column>
        <el-table-column label="设置人" min-width="110">
          <template #default="{ row }">{{ row.created_by }}</template>
        </el-table-column>
        <el-table-column label="设置时间" min-width="135">
          <template #default="{ row }">{{ traceTime(row.created_at) }}</template>
        </el-table-column>
        <el-table-column label="备注" min-width="150">
          <template #default="{ row }">{{ row.note || '—' }}</template>
        </el-table-column>
      </el-table>
      <p v-else class="evaluation-meta">当前范围还没有活跃基线，可在下方批次表把批次设为基线。</p>
      <details v-if="historyBaselines.length" class="evaluation-baseline-history">
        <summary>历史基线（{{ historyBaselines.length }}）</summary>
        <el-table :data="historyBaselines" row-key="baseline_id" class="evaluation-datasets">
          <el-table-column label="口径" width="95">
            <template #default="{ row }">{{ evaluationScopeLabel(row.scope) }}</template>
          </el-table-column>
          <el-table-column label="执行方式" min-width="130">
            <template #default="{ row }">{{ evaluationModeLabel(row.mode) }}</template>
          </el-table-column>
          <el-table-column label="数据集版本" width="120">
            <template #default="{ row }">{{ row.dataset_version }}</template>
          </el-table-column>
          <el-table-column label="批次" min-width="215">
            <template #default="{ row }">
              <button class="evaluation-link" type="button" @click="openRunById(row.run_id)">
                {{ traceTime(row.run_created_at) }}
              </button>
              <small class="evaluation-meta">{{ row.run_id }}</small>
            </template>
          </el-table-column>
          <el-table-column label="通过率" width="100">
            <template #default="{ row }">{{ formatEvaluationPercent(row.run_pass_rate) }}</template>
          </el-table-column>
          <el-table-column label="门禁" width="90">
            <template #default="{ row }">
              <el-tag :type="row.run_gate_passed ? 'success' : 'danger'" size="small">
                {{ row.run_gate_passed ? '通过' : '阻断' }}
              </el-tag>
            </template>
          </el-table-column>
          <el-table-column label="算法版本" min-width="130">
            <template #default="{ row }">{{ row.algorithm_version || '—' }}</template>
          </el-table-column>
          <el-table-column label="设置人" min-width="110">
            <template #default="{ row }">{{ row.created_by }}</template>
          </el-table-column>
          <el-table-column label="设置时间" min-width="135">
            <template #default="{ row }">{{ traceTime(row.created_at) }}</template>
          </el-table-column>
          <el-table-column label="备注" min-width="150">
            <template #default="{ row }">{{ row.note || '—' }}</template>
          </el-table-column>
        </el-table>
      </details>
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
          <small class="evaluation-meta">
            {{ row.run_id }}<template v-if="row.is_baseline"> · 基线</template>
          </small>
        </template>
      </el-table-column>
      <el-table-column label="数据集 / 算法版本" min-width="185">
        <template #default="{ row }">
          <span>{{ row.dataset_name || '未登记数据集' }}</span>
          <small class="evaluation-meta">
            数据集 {{ row.dataset_version || '—' }} · 算法
            {{ row.profile_version_tag || '未绑定' }}
          </small>
        </template>
      </el-table-column>
      <el-table-column label="口径 / 执行方式" min-width="140">
        <template #default="{ row }">
          {{ evaluationScopeLabel(row.scope) }} · {{ evaluationSource(row) }}
        </template>
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
      <el-table-column label="操作" width="240" fixed="right">
        <template #default="{ row }">
          <el-button
            link
            type="primary"
            :disabled="!row.report_available"
            :title="row.report_available ? '下载原始报告 JSON' : '该批次未保存原始报告'"
            @click="downloadReport(row)"
          >
            原始报告
          </el-button>
          <el-button v-if="row.eval_kind === 'rag'" link type="primary" @click="openRunLayers(row)">
            分层指标
          </el-button>
          <el-button
            v-if="canWriteEval && row.dataset_id"
            link
            type="primary"
            @click="openBaselineDialog(row)"
          >
            设为基线
          </el-button>
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
      :title="inspectedDataset ? `数据集用例：${inspectedDataset.name}` : '数据集用例'"
      size="60%"
    >
      <template v-if="inspectedDataset">
        <div class="evaluation-filters">
          <el-tag effect="plain" size="small">版本 {{ inspectedDataset.version }}</el-tag>
          <el-tag type="info" effect="plain" size="small">{{
            evaluationSuites[inspectedDataset.suite] || inspectedDataset.suite || '未登记分组'
          }}</el-tag>
          <el-tag v-if="inspectedDataset.archived" type="info" size="small">历史版本·仅归档</el-tag>
          <span
            class="evaluation-meta"
            :title="inspectedDataset.content_sha256 || '未登记内容摘要'"
          >
            内容摘要
            {{
              inspectedDataset.content_sha256 ? inspectedDataset.content_sha256.slice(0, 12) : '—'
            }}
          </span>
          <span class="evaluation-meta"
            >登记用例数 {{ inspectedDataset.case_count }} · 本页合计 {{ datasetCaseTotal }}</span
          >
        </div>
        <p v-if="inspectedDataset.description">{{ inspectedDataset.description }}</p>
        <el-alert
          v-if="datasetCaseError"
          type="error"
          show-icon
          :title="datasetCaseError"
          :closable="false"
        />
        <el-table
          v-else-if="datasetKind === 'agent'"
          v-loading="datasetCaseBusy"
          :data="datasetCases"
          max-height="520"
          empty-text="该数据集版本没有登记用例"
        >
          <el-table-column label="用例 ID" width="155">
            <template #default="{ row }">{{ datasetCaseText(row, ['id', 'case_id']) }}</template>
          </el-table-column>
          <el-table-column label="名称" min-width="140">
            <template #default="{ row }">{{ datasetCaseText(row, ['name', 'title']) }}</template>
          </el-table-column>
          <el-table-column label="分类" width="120">
            <template #default="{ row }">{{ datasetCaseText(row, ['category']) }}</template>
          </el-table-column>
          <el-table-column label="Prompt / 输入" min-width="320">
            <template #default="{ row }">{{
              datasetCasePreview(row, ['prompt', 'input', 'question', 'query'])
            }}</template>
          </el-table-column>
        </el-table>
        <el-table
          v-else
          v-loading="datasetCaseBusy"
          :data="datasetCases"
          max-height="520"
          empty-text="该数据集版本没有登记用例"
        >
          <el-table-column label="用例 ID" width="155">
            <template #default="{ row }">{{ datasetCaseText(row, ['id', 'case_id']) }}</template>
          </el-table-column>
          <el-table-column label="分类" width="120">
            <template #default="{ row }">{{ datasetCaseText(row, ['category']) }}</template>
          </el-table-column>
          <el-table-column label="问题" min-width="260">
            <template #default="{ row }">{{
              datasetCasePreview(row, ['question', 'query', 'prompt'])
            }}</template>
          </el-table-column>
          <el-table-column label="预期摘要" min-width="300">
            <template #default="{ row }">{{
              datasetCasePreview(row, [
                'expected',
                'expected_answer',
                'expected_facts',
                'expected_documents',
                'reference_answer',
              ])
            }}</template>
          </el-table-column>
        </el-table>
        <nav class="evaluation-pager" aria-label="数据集用例分页">
          <span>第 {{ datasetCasePage }} 页</span>
          <el-button
            :disabled="datasetCaseBusy || datasetCasePage === 1"
            @click="turnDatasetCase(false)"
          >
            上一页
          </el-button>
          <el-button :disabled="datasetCaseBusy || !datasetCaseNext" @click="turnDatasetCase(true)">
            下一页
          </el-button>
        </nav>
      </template>
    </el-drawer>

    <el-drawer
      v-model="layersDrawer"
      :title="layersTarget ? `RAG 分层指标：${layersTarget.run_id}` : 'RAG 分层指标'"
      size="60%"
    >
      <el-alert v-if="layersError" type="error" show-icon :title="layersError" :closable="false" />
      <el-skeleton v-else-if="layersBusy" :rows="4" animated />
      <template v-else-if="layersData">
        <p v-if="!layersPresent" class="evaluation-muted">
          该批次没有产出分层指标：可能只执行了部分口径，或入库时未保存分层结果。
        </p>
        <div v-else class="evaluation-layers">
          <section v-if="retrievalLayer" class="evaluation-layer-card">
            <h4>检索层</h4>
            <dl class="evaluation-layer-metrics">
              <div v-for="metric in retrievalMetrics" :key="metric.label">
                <dt>{{ metric.label }}</dt>
                <dd>
                  {{ formatLayerValue(readLayerValue(retrievalLayer, metric.path), metric.kind) }}
                </dd>
              </div>
            </dl>
          </section>
          <section v-if="adversarialLayer" class="evaluation-layer-card">
            <h4>对抗层</h4>
            <dl class="evaluation-layer-metrics">
              <div v-for="metric in adversarialMetrics" :key="metric.label">
                <dt>{{ metric.label }}</dt>
                <dd>
                  {{ formatLayerValue(readLayerValue(adversarialLayer, metric.path), metric.kind) }}
                </dd>
              </div>
            </dl>
          </section>
          <section v-if="behaviorLayer" class="evaluation-layer-card">
            <h4>行为层</h4>
            <dl class="evaluation-layer-metrics">
              <div v-for="metric in behaviorMetrics" :key="metric.label">
                <dt>{{ metric.label }}</dt>
                <dd>
                  {{ formatLayerValue(readLayerValue(behaviorLayer, metric.path), metric.kind) }}
                </dd>
              </div>
            </dl>
            <dl v-if="behaviorDimensionRows.length" class="evaluation-layer-dimensions">
              <div v-for="item in behaviorDimensionRows" :key="item.dimension">
                <dt>{{ item.dimension }}</dt>
                <dd>{{ item.buckets }}</dd>
              </div>
            </dl>
          </section>
          <section v-if="engineeringLayer" class="evaluation-layer-card">
            <h4>工程指标</h4>
            <dl class="evaluation-layer-metrics">
              <div v-for="metric in engineeringMetrics" :key="metric.label">
                <dt>{{ metric.label }}</dt>
                <dd>
                  {{ formatLayerValue(readLayerValue(engineeringLayer, metric.path), metric.kind) }}
                </dd>
              </div>
            </dl>
          </section>
        </div>
      </template>
    </el-drawer>

    <el-drawer v-model="auditDrawer" title="评测审计留痕" size="60%">
      <div class="evaluation-filters">
        <el-select
          v-model="auditAction"
          placeholder="全部动作"
          aria-label="按审计动作筛选"
          @change="resetAudit"
        >
          <el-option value="" label="全部动作" />
          <el-option
            v-for="(label, code) in evaluationAuditActions"
            :key="code"
            :value="code"
            :label="label"
          />
        </el-select>
      </div>
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
        <el-table-column label="对象" min-width="180">
          <template #default="{ row }">{{ row.target_id }}</template>
        </el-table-column>
        <el-table-column label="操作人" min-width="130">
          <template #default="{ row }">{{ row.actor_id }}</template>
        </el-table-column>
        <el-table-column label="时间" min-width="150">
          <template #default="{ row }">{{ traceTime(row.created_at) }}</template>
        </el-table-column>
        <el-table-column label="摘要" min-width="220">
          <template #default="{ row }">{{ summarizeJson(row.summary) }}</template>
        </el-table-column>
      </el-table>
      <nav class="evaluation-pager" aria-label="审计留痕分页">
        <span>第 {{ auditPage }} 页</span>
        <el-button :disabled="auditBusy || auditPage === 1" @click="turnAudit(false)"
          >上一页</el-button
        >
        <el-button :disabled="auditBusy || !auditNext" @click="turnAudit(true)">下一页</el-button>
      </nav>
    </el-drawer>

    <el-dialog v-model="profileDialog" title="登记不可变技术栈 / Prompt 版本" width="560px">
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

    <el-dialog v-model="triggerDialog" title="选择数据集与算法版本发起评测" width="540px">
      <el-form label-position="top">
        <el-form-item label="评测数据集版本">
          <el-select v-model="triggerForm.dataset_id" style="width: 100%">
            <el-option
              v-for="ds in triggerDatasets"
              :key="ds.dataset_id"
              :label="`${ds.name}（${ds.version}）`"
              :value="ds.dataset_id"
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
              v-for="pf in profiles"
              :key="pf.profile_id"
              :label="`V${pf.version_no} · ${pf.version_tag}`"
              :value="pf.profile_id"
            />
          </el-select>
        </el-form-item>
        <el-form-item label="评测口径">
          <el-select v-model="triggerForm.scope" style="width: 100%">
            <el-option
              v-for="item in triggerScopeOptions"
              :key="item"
              :value="item"
              :label="evaluationScopes[item]"
            />
          </el-select>
        </el-form-item>
        <el-form-item label="执行方式">
          <el-select v-model="triggerForm.mode" style="width: 100%">
            <el-option
              v-for="item in triggerModeOptions"
              :key="item"
              :value="item"
              :label="evaluationModes[item]"
            />
          </el-select>
        </el-form-item>
        <el-form-item label="本次评测备注">
          <el-input
            v-model="triggerForm.note"
            maxlength="500"
            show-word-limit
            placeholder="说明本次测试目的"
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

    <el-dialog v-model="baselineDialog" title="设为评测基线" width="480px">
      <el-form label-position="top">
        <el-form-item label="基线批次">
          <span>{{ baselineRun?.run_id }}</span>
          <small class="evaluation-meta">
            口径 {{ baselineRun ? evaluationScopeLabel(baselineRun.scope) : '—' }} · 执行方式
            {{ baselineRun ? evaluationModeLabel(baselineRun.mode) : '—' }} · 数据集版本
            {{ baselineRun?.dataset_version || '—' }}
          </small>
        </el-form-item>
        <el-form-item label="基线备注（必填）">
          <el-input
            v-model="baselineNote"
            maxlength="500"
            show-word-limit
            placeholder="说明为什么将该批次作为当前基线"
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

    <el-dialog
      :model-value="Boolean(inspectedProfile)"
      title="技术栈与 Prompt 快照详情"
      width="640px"
      @close="inspectedProfile = null"
    >
      <pre v-if="inspectedProfile" class="evaluation-json">{{ profileSnapshotJson }}</pre>
    </el-dialog>
  </section>
</template>

<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import type { ApiClient, CursorPage, Items } from '../../shared/apiTypes'
import { useCursorPage } from '../../shared/useCursorPage'
import { stateTag, stateText, traceTime } from '../../shared/labels'
import {
  evaluationAuditActions,
  evaluationDeltaTone,
  evaluationModes,
  evaluationModeLabel,
  evaluationScopes,
  evaluationScopeLabel,
  evaluationSource,
  evaluationSuites,
  formatEvaluationDelta,
  formatEvaluationLatency,
  formatEvaluationPercent,
  type EvaluationAuditAction,
  type EvaluationAuditLog,
  type EvaluationBaseline,
  type EvaluationDataset,
  type EvaluationDatasetDetail,
  type EvaluationJob,
  type EvaluationJobState,
  type EvaluationKind,
  type EvaluationLayerName,
  type EvaluationLayers,
  type EvaluationMode,
  type EvaluationProfile,
  type EvaluationRun,
  type EvaluationRunDetail,
  type EvaluationScope,
  type EvaluationTriggerReceipt,
  type EvaluationTrends,
  type TriggerEvaluationPayload,
} from './evaluationTypes'
import EvaluationRunInspector from './EvaluationRunInspector.vue'
import TenantOwnerSelect from './TenantOwnerSelect.vue'
import './evaluations.css'

const DATASET_CASE_PAGE_SIZE = 50
const TREND_LIMIT = 30
const AUDIT_PAGE_SIZE = 30
const JOB_POLL_MS = 3000
const JOB_TERMINAL_STATES = new Set<EvaluationJobState>([
  'completed',
  'cancelled',
  'failed',
  'rejected',
  'legacy',
])

const props = defineProps<{ isAdmin: boolean; canWriteEval: boolean; api: ApiClient }>()
const emit = defineEmits<{ (e: 'inspect', taskId: string, traceId: string): void }>()
const evalKind = ref<EvaluationKind>('agent')
const selected = ref<EvaluationRun | null>(null)
const owner = ref('all')
const mode = ref<EvaluationMode | ''>('')
const gate = ref('')
const selectedDatasetId = ref('')
const selectedProfileId = ref('')
const showArchived = ref(false)
const datasetVersion = ref('')
const trendScope = ref<EvaluationScope | ''>('')
const trendMode = ref<EvaluationMode | ''>('')
const datasets = ref<EvaluationDataset[]>([])
const profiles = ref<EvaluationProfile[]>([])
const baselines = ref<EvaluationBaseline[]>([])
const trends = ref<EvaluationTrends | null>(null)
const datasetError = ref('')
const profileError = ref('')
const baselineError = ref('')
const trendError = ref('')
const baselineActiveOnly = ref(false)
const datasetDrawer = ref(false)
const inspectedDataset = ref<EvaluationDataset | null>(null)
const datasetCases = ref<Record<string, unknown>[]>([])
const datasetCaseCursors = ref<string[]>([''])
const datasetCaseNext = ref<string | null>(null)
const datasetCaseTotal = ref(0)
const datasetCaseBusy = ref(false)
const datasetCaseError = ref('')
const inspectedProfile = ref<EvaluationProfile | null>(null)
const layersDrawer = ref(false)
const layersTarget = ref<EvaluationRun | null>(null)
const layersData = ref<EvaluationLayers | null>(null)
const layersBusy = ref(false)
const layersError = ref('')
const activeJob = ref<EvaluationJob | null>(null)
const jobError = ref('')
const jobBusy = ref(false)
const auditDrawer = ref(false)
const auditAction = ref<EvaluationAuditAction | ''>('')
const auditLogs = ref<EvaluationAuditLog[]>([])
const auditCursors = ref<string[]>([''])
const auditNext = ref<string | null>(null)
const auditBusy = ref(false)
const auditError = ref('')
const profileDialog = ref(false)
const triggerDialog = ref(false)
const baselineDialog = ref(false)
const writeBusy = ref(false)
const writeError = ref('')
const baselineWriteError = ref('')
const baselineRun = ref<EvaluationRun | null>(null)
const baselineNote = ref('')
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
let jobTimer: number | null = null
let polledJobId = ''

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

const datasetVersions = computed(() => {
  const versions = new Set(datasets.value.map((item) => item.version))
  return [...versions]
})
const datasetKind = computed(() => inspectedDataset.value?.eval_kind ?? 'agent')
const datasetCasePage = computed(() => datasetCaseCursors.value.length)
const activeBaselines = computed(() => baselines.value.filter((item) => item.active))
const historyBaselines = computed(() => baselines.value.filter((item) => !item.active))
const triggerDatasets = computed(() => datasets.value.filter((item) => !item.archived))
const selectedTriggerDataset = computed(
  () => triggerDatasets.value.find((item) => item.dataset_id === triggerForm.dataset_id) ?? null,
)
const triggerScopeOptions = computed(() => datasetScopes(selectedTriggerDataset.value))
const triggerModeOptions = computed(() => datasetModes(selectedTriggerDataset.value))
const triggerScopeLabels = computed(
  () => triggerScopeOptions.value.map((scope) => evaluationScopes[scope]).join('、') || '未登记',
)
const triggerModeLabels = computed(
  () => triggerModeOptions.value.map((item) => evaluationModes[item]).join('、') || '未登记',
)
const jobTerminal = computed(
  () => activeJob.value !== null && JOB_TERMINAL_STATES.has(activeJob.value.state),
)
const jobProgressStatus = computed<'success' | 'exception' | undefined>(() => {
  if (!activeJob.value) return undefined
  if (activeJob.value.state === 'completed') return 'success'
  if (JOB_TERMINAL_STATES.has(activeJob.value.state)) return 'exception'
  return undefined
})
const jobRunId = computed(() => {
  const result = activeJob.value?.result
  return result && typeof result.run_id === 'string' ? result.run_id : ''
})
const auditPage = computed(() => auditCursors.value.length)
const profileSnapshotJson = computed(() => {
  const profile = inspectedProfile.value
  if (!profile) return ''
  return JSON.stringify(
    {
      version_no: profile.version_no,
      version_tag: profile.version_tag,
      digest: profile.digest,
      git_sha: profile.git_sha,
      dataset_version: profile.dataset_version,
      tech_stack: profile.tech_stack,
      retrieval_params: profile.retrieval_params,
      prompt_snapshots: profile.prompt_snapshots,
      prompt_sha256: profile.prompt_sha256,
    },
    null,
    2,
  )
})
const retrievalLayer = computed(() => currentLayer('retrieval'))
const adversarialLayer = computed(() => currentLayer('adversarial'))
const behaviorLayer = computed(() => currentLayer('behavior'))
const engineeringLayer = computed(() => currentLayer('six_metrics'))
const layersPresent = computed(
  () =>
    Boolean(retrievalLayer.value) ||
    Boolean(adversarialLayer.value) ||
    Boolean(behaviorLayer.value) ||
    Boolean(engineeringLayer.value),
)
const retrievalMetrics = [
  { label: 'Hit@1', path: ['hit@1'], kind: 'percent' },
  { label: 'Hit@3', path: ['hit@3'], kind: 'percent' },
  { label: 'Hit@8', path: ['hit@8'], kind: 'percent' },
  { label: 'NDCG@1', path: ['ndcg@1'], kind: 'percent' },
  { label: 'NDCG@3', path: ['ndcg@3'], kind: 'percent' },
  { label: 'NDCG@8', path: ['ndcg@8'], kind: 'percent' },
  { label: 'MRR', path: ['mrr'], kind: 'ratio' },
  { label: '样本数 n', path: ['n'], kind: 'number' },
] as const
const adversarialMetrics = [
  { label: '通过率', path: ['pass_rate'], kind: 'percent' },
  { label: '金丝雀泄漏', path: ['canary_leaks'], kind: 'number' },
  { label: '被拒请求', path: ['rejected'], kind: 'number' },
  { label: '样本数 n', path: ['n'], kind: 'number' },
] as const
const behaviorMetrics = [
  { label: '通过率', path: ['pass_rate'], kind: 'percent' },
  { label: '通过 / 总数', path: ['total'], kind: 'text' },
  { label: '任务失败率', path: ['task_failure_rate'], kind: 'percent' },
  { label: '超时率', path: ['timeout_rate'], kind: 'percent' },
] as const
const engineeringMetrics = [
  { label: '端到端 P50', path: ['latency_e2e_s', 'p50'], kind: 'seconds' },
  { label: '端到端 P95', path: ['latency_e2e_s', 'p95'], kind: 'seconds' },
  { label: 'Token 总量', path: ['token_cost', 'total_tokens'], kind: 'number' },
  { label: 'Token 成本（USD）', path: ['token_cost', 'cost_usd'], kind: 'cost' },
  { label: '单例 Token', path: ['token_cost', 'tokens_per_case'], kind: 'number' },
  { label: '任务失败率', path: ['reliability', 'task_failure_rate'], kind: 'percent' },
  { label: '超时率', path: ['reliability', 'timeout_rate'], kind: 'percent' },
  { label: 'JSON 重试率', path: ['reliability', 'json_retry_rate'], kind: 'percent' },
  { label: '模型切换率', path: ['reliability', 'failover_rate'], kind: 'percent' },
] as const
const behaviorDimensionRows = computed(() => {
  const dimensions = readLayerValue(behaviorLayer.value, ['by_dimension'])
  if (!dimensions || typeof dimensions !== 'object' || Array.isArray(dimensions)) return []
  const rows: { dimension: string; buckets: string }[] = []
  for (const [dimension, buckets] of Object.entries(dimensions as Record<string, unknown>)) {
    if (!buckets || typeof buckets !== 'object' || Array.isArray(buckets)) continue
    const parts = Object.entries(buckets as Record<string, unknown>)
      .slice(0, 6)
      .map(([bucket, stat]) => {
        const rate = readLayerValue(stat, ['pass_rate'])
        const count = readLayerValue(stat, ['n'])
        return `${bucket} ${formatLayerValue(rate, 'percent')}（n=${formatLayerValue(count, 'number')}）`
      })
    rows.push({ dimension, buckets: parts.join(' · ') || '—' })
  }
  return rows
})

/**
 * 展示审计动作的中文名称。
 *
 * @param action 审计动作编码
 * @returns 中文动作名称或原始编码
 */
function auditActionLabel(action: string): string {
  return evaluationAuditActions[action as EvaluationAuditAction] ?? action
}

/**
 * 将技术栈 JSON 摘要为单行文本。
 *
 * @param stack 技术栈配置对象
 * @returns 摘要字符串
 */
function formatTechStack(stack: Record<string, unknown>): string {
  const entries = Object.entries(stack)
  if (!entries.length) return '-'
  return entries.map(([key, value]) => `${key}=${summarizeStackValue(value)}`).join(' · ')
}

/**
 * 把算法快照中的嵌套对象与数组压缩成可读摘要。
 *
 * @param value 快照字段值
 * @returns 单行摘要文本
 */
function summarizeStackValue(value: unknown): string {
  if (Array.isArray(value)) return `${value.length} 项`
  if (value && typeof value === 'object') {
    const keys = Object.keys(value as Record<string, unknown>)
    return keys.length ? `{${keys.join('、')}}` : '{}'
  }
  return String(value)
}

/**
 * 读取数据集登记的执行方式清单。
 *
 * @param dataset 数据集条目
 * @returns 可用执行方式；未登记时回退到全部方式
 */
function datasetModes(dataset: EvaluationDataset | null): EvaluationMode[] {
  const value = dataset?.metadata?.modes
  if (Array.isArray(value)) {
    const modes = value.filter(
      (item): item is EvaluationMode => item === 'live' || item === 'synthetic_trace_regrade',
    )
    if (modes.length) return modes
  }
  return ['live', 'synthetic_trace_regrade']
}

/**
 * 读取数据集登记的评测口径清单。
 *
 * @param dataset 数据集条目
 * @returns 可用口径；未登记时回退到全部口径
 */
function datasetScopes(dataset: EvaluationDataset | null): EvaluationScope[] {
  const value = dataset?.metadata?.scopes
  if (Array.isArray(value)) {
    const scopes = value.filter(
      (item): item is EvaluationScope =>
        item === 'full' ||
        item === 'quick' ||
        item === 'retrieval' ||
        item === 'adversarial' ||
        item === 'behavior',
    )
    if (scopes.length) return scopes
  }
  return ['full', 'quick', 'retrieval', 'adversarial', 'behavior']
}

interface BaselineRowScope {
  row: EvaluationBaseline
}

/**
 * 为活跃基线行附加高亮样式。
 *
 * @param scope Element Plus 表格行上下文
 * @returns 行样式类名
 */
function baselineRowClass(scope: BaselineRowScope): string {
  return scope.row.active ? 'evaluation-baseline-active' : ''
}

/**
 * 按游标读取数据集用例页。
 */
async function loadDatasetCases(): Promise<void> {
  const dataset = inspectedDataset.value
  if (!dataset) return
  datasetCaseBusy.value = true
  datasetCaseError.value = ''
  try {
    const query = new URLSearchParams({ limit: String(DATASET_CASE_PAGE_SIZE) })
    const cursor = datasetCaseCursors.value.at(-1)
    if (cursor) query.set('cursor', cursor)
    const detail = await props.api<EvaluationDatasetDetail>(
      `/evaluations/datasets/${dataset.dataset_id}?${query}`,
    )
    datasetCases.value = detail.cases
    datasetCaseNext.value = detail.next_cursor
    datasetCaseTotal.value = detail.total_cases
  } catch (cause) {
    datasetCaseError.value = cause instanceof Error ? cause.message : '读取数据集用例失败'
  } finally {
    datasetCaseBusy.value = false
  }
}

/**
 * 查看指定数据集的版本元信息与用例内容。
 *
 * @param dataset 数据集条目
 */
async function inspectDataset(dataset: EvaluationDataset): Promise<void> {
  inspectedDataset.value = dataset
  datasetCases.value = []
  datasetCaseCursors.value = ['']
  datasetCaseNext.value = null
  datasetCaseTotal.value = 0
  datasetDrawer.value = true
  await loadDatasetCases()
}

/**
 * 按游标前后翻动数据集用例页。
 *
 * @param forward 是否读取下一页
 */
function turnDatasetCase(forward: boolean): void {
  if (datasetCaseBusy.value) return
  if (forward && datasetCaseNext.value) datasetCaseCursors.value.push(datasetCaseNext.value)
  else if (!forward && datasetCaseCursors.value.length > 1) datasetCaseCursors.value.pop()
  else return
  void loadDatasetCases()
}

/**
 * 从用例对象读取首个可用字段并压缩为单行文本。
 *
 * @param item 数据集中登记的用例对象
 * @param keys 候选字段名
 * @returns 可读文本或缺失占位
 */
function datasetCaseText(item: Record<string, unknown>, keys: string[]): string {
  for (const key of keys) {
    const value = item[key]
    if (value === null || value === undefined || value === '') continue
    if (typeof value === 'string') return value
    if (Array.isArray(value)) {
      return value
        .map((entry) => (typeof entry === 'string' ? entry : JSON.stringify(entry)))
        .join('、')
    }
    if (typeof value === 'object') return JSON.stringify(value)
    return String(value)
  }
  return '—'
}

/**
 * 从用例对象读取文本并截断为单行摘要。
 *
 * @param item 数据集中登记的用例对象
 * @param keys 候选字段名
 * @returns 截断后的摘要文本
 */
function datasetCasePreview(item: Record<string, unknown>, keys: string[]): string {
  const text = datasetCaseText(item, keys)
  return text.length > 160 ? text.slice(0, 160) + '…' : text
}

/**
 * 读取当前评测类型下的数据集列表。
 */
async function loadDatasets(): Promise<void> {
  datasetError.value = ''
  try {
    const query = new URLSearchParams({
      eval_kind: evalKind.value,
      include_archived: String(showArchived.value),
    })
    datasets.value = (
      await props.api<Items<EvaluationDataset>>(`/evaluations/datasets?${query}`)
    ).items
  } catch (cause) {
    datasetError.value = cause instanceof Error ? cause.message : '读取数据集失败'
  }
}

/**
 * 读取当前评测类型下的算法版本列表。
 */
async function loadProfiles(): Promise<void> {
  profileError.value = ''
  try {
    const query = new URLSearchParams({ eval_kind: evalKind.value })
    if (datasetVersion.value) query.set('dataset_version', datasetVersion.value)
    profiles.value = (
      await props.api<Items<EvaluationProfile>>(`/evaluations/profiles?${query}`)
    ).items
  } catch (cause) {
    profileError.value = cause instanceof Error ? cause.message : '读取配置版本失败'
  }
}

/**
 * 读取当前评测类型下的基线（含历史）。
 */
async function loadBaselines(): Promise<void> {
  baselineError.value = ''
  try {
    const query = new URLSearchParams({
      eval_kind: evalKind.value,
      include_history: String(!baselineActiveOnly.value),
    })
    if (datasetVersion.value) query.set('dataset_version', datasetVersion.value)
    baselines.value = (
      await props.api<Items<EvaluationBaseline>>(`/evaluations/baselines?${query}`)
    ).items
  } catch (cause) {
    baselineError.value = cause instanceof Error ? cause.message : '读取基线失败'
  }
}

/**
 * 读取当前筛选条件下的指标趋势与活跃基线摘要。
 */
async function loadTrends(): Promise<void> {
  trendError.value = ''
  try {
    const query = new URLSearchParams({ eval_kind: evalKind.value, limit: String(TREND_LIMIT) })
    if (datasetVersion.value) query.set('dataset_version', datasetVersion.value)
    if (trendScope.value) query.set('scope', trendScope.value)
    if (trendMode.value) query.set('mode', trendMode.value)
    trends.value = await props.api<EvaluationTrends>(`/evaluations/metrics/trends?${query}`)
  } catch (cause) {
    trendError.value = cause instanceof Error ? cause.message : '读取指标趋势失败'
  }
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

/**
 * 读取批次详情并进入批次结果页。
 *
 * @param runId 批次编号
 */
async function openRunById(runId: string): Promise<void> {
  try {
    const detail = await props.api<EvaluationRunDetail>(
      '/evaluations/runs/' + encodeURIComponent(runId),
    )
    selected.value = detail.run
  } catch (cause) {
    ElMessage.error(cause instanceof Error ? cause.message : '打开评测批次失败')
  }
}

/**
 * 触发原始报告下载；浏览器按服务端 attachment 头保存 JSON 文件。
 *
 * @param row 当前批次
 */
function downloadReport(row: EvaluationRun): void {
  if (!row.report_available) return
  window.open('/api/evaluations/runs/' + encodeURIComponent(row.run_id) + '/report', '_blank')
}

/**
 * 打开指定批次的分层指标抽屉，并读取 RAG 分层结果。
 *
 * @param row 当前批次
 */
async function openRunLayers(row: EvaluationRun): Promise<void> {
  layersTarget.value = row
  layersData.value = null
  layersError.value = ''
  layersBusy.value = true
  layersDrawer.value = true
  try {
    layersData.value = await readRunLayers(row.run_id)
  } catch (cause) {
    layersError.value = cause instanceof Error ? cause.message : '读取分层指标失败'
  } finally {
    layersBusy.value = false
  }
}

/**
 * 读取批次分层指标；该接口结构不套用批次运行列表校验，直接按同源 JSON 解析。
 *
 * @param runId 批次编号
 * @returns 分层指标响应
 */
async function readRunLayers(runId: string): Promise<EvaluationLayers> {
  return props.api<EvaluationLayers>('/evaluations/runs/' + encodeURIComponent(runId) + '/layers')
}

/**
 * 读取当前批次指定层的指标对象。
 *
 * @param name 分层名称
 * @returns 分层指标对象；缺失时为 null
 */
function currentLayer(name: EvaluationLayerName): Record<string, unknown> | null {
  const layer = layersData.value?.layers?.[name]
  if (!layer || typeof layer !== 'object' || Array.isArray(layer)) return null
  return layer
}

/**
 * 按字段路径读取分层指标中的嵌套值。
 *
 * @param value 分层指标对象
 * @param keys 逐层字段名
 * @returns 命中的值；任一层缺失时返回 null
 */
function readLayerValue(value: unknown, keys: readonly string[]): unknown {
  let current: unknown = value
  for (const key of keys) {
    if (!current || typeof current !== 'object' || Array.isArray(current)) return null
    current = (current as Record<string, unknown>)[key]
  }
  return current ?? null
}

/**
 * 将分层指标值格式化为可读文本。
 *
 * @param value 分层指标原始值
 * @param kind 展示类型
 * @returns 可读文本或缺失占位
 */
function formatLayerValue(
  value: unknown,
  kind: 'percent' | 'ratio' | 'number' | 'seconds' | 'cost' | 'text',
): string {
  if (value === null || value === undefined) return '—'
  if (kind === 'text') return typeof value === 'string' ? value : JSON.stringify(value)
  if (typeof value !== 'number' || !Number.isFinite(value)) return '—'
  if (kind === 'percent') return (value * 100).toFixed(1) + '%'
  if (kind === 'ratio') return value.toFixed(3)
  if (kind === 'seconds') return value.toFixed(2) + 's'
  if (kind === 'cost') return value.toFixed(4)
  return Number.isInteger(value) ? String(value) : value.toFixed(2)
}

/**
 * 将审计摘要压缩为单行文本。
 *
 * @param value 审计摘要对象
 * @returns 截断后的单行摘要
 */
function summarizeJson(value: Record<string, unknown>): string {
  const text = JSON.stringify(value)
  return text.length > 120 ? text.slice(0, 120) + '…' : text
}

/**
 * 解析配置弹窗中的 JSON 对象文本。
 *
 * @param text 用户输入的 JSON 文本
 * @param label 出错时提示的字段名称
 * @returns 解析后的普通对象
 * @throws 文本不是合法 JSON 对象时抛中文错误
 */
function parseJsonObject(text: string, label: string): Record<string, unknown> {
  let value: unknown
  try {
    value = JSON.parse(text.trim() || '{}')
  } catch {
    throw new Error(label + '不是合法的 JSON')
  }
  if (!value || typeof value !== 'object' || Array.isArray(value)) {
    throw new Error(label + '必须是 JSON 对象')
  }
  return value as Record<string, unknown>
}

/**
 * 打开算法版本登记弹窗并清空上次的写入错误。
 */
function openProfileDialog(): void {
  writeError.value = ''
  profileDialog.value = true
}

/**
 * 打开受控评测发起弹窗并按数据集登记能力预选口径与执行方式。
 */
function openTriggerDialog(): void {
  writeError.value = ''
  const datasetId = selectedDatasetId.value || triggerDatasets.value[0]?.dataset_id || ''
  const dataset = triggerDatasets.value.find((item) => item.dataset_id === datasetId) ?? null
  const scopes = datasetScopes(dataset)
  const modes = datasetModes(dataset)
  triggerForm.dataset_id = datasetId
  triggerForm.profile_id = selectedProfileId.value || ''
  triggerForm.scope = scopes.includes('full') ? 'full' : scopes[0]
  triggerForm.mode = modes[0]
  triggerForm.note = ''
  triggerForm.mark_baseline = false
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
      eval_kind: evalKind.value,
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
    await loadProfiles()
  } catch (cause) {
    writeError.value = cause instanceof Error ? cause.message : '登记版本失败'
  } finally {
    writeBusy.value = false
  }
}

/**
 * 选择数据集与算法版本发起真实评测任务。
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
      eval_kind: evalKind.value,
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
    activeJob.value = {
      job_id: receipt.job_id,
      state: receipt.status,
      stage: '已提交，等待 Worker 执行',
      progress: 0,
      result: null,
      error: '',
      created_at: new Date().toISOString(),
      started_at: null,
      finished_at: null,
    }
    startJobPolling(receipt.job_id)
    ElMessage.success(
      `已提交评测任务（任务号 ${receipt.job_id}，审计号 ${receipt.audit_id}），由 Worker 执行`,
    )
    await reload()
  } catch (cause) {
    writeError.value = cause instanceof Error ? cause.message : '发起评测失败'
  } finally {
    writeBusy.value = false
  }
}

/**
 * 打开把批次设为基线的弹窗。
 *
 * @param row 待设为基线的批次
 */
function openBaselineDialog(row: EvaluationRun): void {
  baselineRun.value = row
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
    await Promise.all([loadBaselines(), loadTrends(), refresh()])
  } catch (cause) {
    baselineWriteError.value = cause instanceof Error ? cause.message : '设置基线失败'
  } finally {
    writeBusy.value = false
  }
}

/**
 * 停止评测任务轮询。
 */
function stopJobPolling(): void {
  if (jobTimer !== null) {
    window.clearInterval(jobTimer)
    jobTimer = null
  }
  polledJobId = ''
}

/**
 * 读取评测任务最新状态；进入终态后停止轮询并刷新数据。
 *
 * @param jobId 评测任务编号
 */
async function pollJob(jobId: string): Promise<void> {
  try {
    const job = await props.api<EvaluationJob>('/evaluations/jobs/' + encodeURIComponent(jobId))
    if (polledJobId !== jobId) return
    const wasTerminal = activeJob.value !== null && JOB_TERMINAL_STATES.has(activeJob.value.state)
    activeJob.value = job
    jobError.value = ''
    if (JOB_TERMINAL_STATES.has(job.state)) {
      stopJobPolling()
      if (!wasTerminal) await finishJob(job)
    }
  } catch (cause) {
    if (polledJobId !== jobId) return
    jobError.value = cause instanceof Error ? cause.message : '读取评测任务失败'
    stopJobPolling()
  }
}

/**
 * 处理评测任务终态：提示结果并刷新评测目录与批次。
 *
 * @param job 已结束的评测任务
 */
async function finishJob(job: EvaluationJob): Promise<void> {
  if (job.state === 'completed') {
    const result = job.result
    const runId = result && typeof result.run_id === 'string' ? result.run_id : ''
    ElMessage.success(runId ? `评测完成，批次 ${runId} 已入库` : '评测任务已完成')
    await reload()
    return
  }
  if (job.state === 'cancelled') {
    ElMessage.warning('评测任务已取消')
    return
  }
  ElMessage.error(job.error || '评测任务执行失败')
}

/**
 * 立即刷新任务并按固定节奏轮询直到终态。
 *
 * @param jobId 评测任务编号
 */
function startJobPolling(jobId: string): void {
  stopJobPolling()
  polledJobId = jobId
  void pollJob(jobId)
  jobTimer = window.setInterval(() => {
    void pollJob(jobId)
  }, JOB_POLL_MS)
}

/**
 * 请求取消排队或执行中的评测任务。
 */
async function cancelJob(): Promise<void> {
  const job = activeJob.value
  if (!job) return
  jobBusy.value = true
  jobError.value = ''
  try {
    await props.api<{ message: string }>(
      '/evaluations/jobs/' + encodeURIComponent(job.job_id) + '/cancel',
      'POST',
    )
    ElMessage.success('已请求取消评测任务')
  } catch (cause) {
    jobError.value = cause instanceof Error ? cause.message : '取消评测任务失败'
  } finally {
    jobBusy.value = false
  }
}

/**
 * 打开审计留痕抽屉并重置分页。
 */
async function openAuditDrawer(): Promise<void> {
  auditDrawer.value = true
  auditCursors.value = ['']
  auditNext.value = null
  await loadAuditLogs()
}

/**
 * 读取一页评测审计留痕。
 */
async function loadAuditLogs(): Promise<void> {
  auditBusy.value = true
  auditError.value = ''
  try {
    const query = new URLSearchParams({ limit: String(AUDIT_PAGE_SIZE) })
    if (auditAction.value) query.set('action', auditAction.value)
    const cursor = auditCursors.value.at(-1)
    if (cursor) query.set('cursor', cursor)
    const page = await props.api<CursorPage<EvaluationAuditLog>>(`/evaluations/audit-logs?${query}`)
    auditLogs.value = page.items
    auditNext.value = page.next_cursor
  } catch (cause) {
    auditError.value = cause instanceof Error ? cause.message : '读取审计留痕失败'
  } finally {
    auditBusy.value = false
  }
}

/**
 * 切换审计动作筛选并回到第一页。
 */
function resetAudit(): void {
  auditCursors.value = ['']
  auditNext.value = null
  void loadAuditLogs()
}

/**
 * 按游标前后翻动审计留痕页。
 *
 * @param forward 是否读取下一页
 */
function turnAudit(forward: boolean): void {
  if (auditBusy.value) return
  if (forward && auditNext.value) auditCursors.value.push(auditNext.value)
  else if (!forward && auditCursors.value.length > 1) auditCursors.value.pop()
  else return
  void loadAuditLogs()
}

/**
 * 同时刷新数据集、算法版本、基线、趋势与当前批次页。
 */
async function reload(): Promise<void> {
  await Promise.all([refresh(), loadDatasets(), loadProfiles(), loadBaselines(), loadTrends()])
}

watch(evalKind, async () => {
  selectedDatasetId.value = ''
  selectedProfileId.value = ''
  datasetVersion.value = ''
  datasets.value = []
  profiles.value = []
  baselines.value = []
  trends.value = null
  await Promise.all([loadDatasets(), loadProfiles(), loadBaselines(), loadTrends()])
})
watch(datasetVersion, async () => {
  await Promise.all([loadProfiles(), loadBaselines(), loadTrends()])
})
watch([trendScope, trendMode], loadTrends)
watch(showArchived, loadDatasets)
watch(baselineActiveOnly, loadBaselines)
watch(
  () => triggerForm.dataset_id,
  () => {
    const dataset = selectedTriggerDataset.value
    const scopes = datasetScopes(dataset)
    const modes = datasetModes(dataset)
    if (!scopes.includes(triggerForm.scope)) triggerForm.scope = scopes[0]
    if (!modes.includes(triggerForm.mode)) triggerForm.mode = modes[0]
  },
)
watch(endpoint, reset)
onMounted(() => {
  void Promise.all([loadDatasets(), loadProfiles(), loadBaselines(), loadTrends()])
})
onBeforeUnmount(stopJobPolling)
</script>
