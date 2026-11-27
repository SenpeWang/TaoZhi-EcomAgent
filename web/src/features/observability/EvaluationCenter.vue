<template>
  <section class="panel evaluation-center">
    <div class="section-heading evaluation-section-head">
      <h3>{{ centerTitle }}</h3>
      <div class="evaluation-head-actions">
        <el-button :loading="refreshing" @click="refreshAll">刷新</el-button>
      </div>
    </div>

    <div class="evaluation-controls">
      <label class="evaluation-control-item">
        <span>数据集版本</span>
        <el-select
          v-model="datasetVersion"
          placeholder="暂无可选版本"
          :disabled="!datasetVersions.length"
          aria-label="选择数据集版本"
        >
          <el-option
            v-for="version in datasetVersions"
            :key="version"
            :value="version"
            :label="version"
          />
        </el-select>
      </label>
      <label class="evaluation-control-item">
        <span>评测口径</span>
        <el-select v-model="scope" aria-label="选择评测口径">
          <el-option
            v-for="item in scopeOptions"
            :key="item.value"
            :value="item.value"
            :label="item.label"
          />
        </el-select>
      </label>
      <label class="evaluation-control-item">
        <span>执行方式</span>
        <el-select v-model="mode" aria-label="选择执行方式">
          <el-option
            v-for="item in allowedModes"
            :key="item"
            :value="item"
            :label="evaluationModeLabel(item)"
          />
        </el-select>
      </label>
    </div>
    <el-alert
      v-if="catalogError"
      type="error"
      show-icon
      :title="catalogError"
      :closable="false"
      class="evaluation-catalog-alert"
    />

    <el-tabs v-model="activeTab" class="evaluation-tabs">
      <el-tab-pane v-if="view === 'results'" label="概览" name="overview">
        <div class="evaluation-tab evaluation-overview">
          <el-alert
            v-if="trendsError"
            type="warning"
            show-icon
            :title="trendsError"
            :closable="false"
          />
          <el-skeleton v-else-if="!trends" :rows="3" animated />
          <template v-else-if="trendItems.length">
            <div class="evaluation-overview-summary">
              <span class="evaluation-overview-batches">批次 {{ trends.aggregate.batches }}</span>
              <span v-if="aggregateMetric">
                {{ aggregateLabel }}：最新 {{ formatAggregateValue(aggregateStats.latest) }} · 平均
                {{ formatAggregateValue(aggregateStats.average) }} · 最佳
                {{ formatAggregateValue(aggregateStats.best) }}
              </span>
            </div>
            <div class="evaluation-metric-grid">
              <el-tooltip
                v-for="card in overviewMetricCards"
                :key="card.spec.key"
                placement="top"
                :content="metricTooltip(card.spec)"
              >
                <div class="evaluation-metric-card evaluation-metric-card-compact">
                  <span class="evaluation-card-label">{{ shortMetricLabel(card.spec) }}</span>
                  <strong class="evaluation-metric-value">{{
                    formatMetricValue(card.spec, card.latest)
                  }}</strong>
                  <span
                    v-if="card.delta !== null"
                    class="evaluation-metric-delta"
                    :class="metricDeltaTone(card.spec, card.delta)"
                    >较基线 {{ formatMetricDelta(card.spec, card.delta) }}</span
                  >
                </div>
              </el-tooltip>
            </div>
            <el-button
              v-if="metrics.length > coreMetrics.length"
              link
              type="primary"
              class="evaluation-expand-metrics"
              @click="showAllMetrics = !showAllMetrics"
              >{{ showAllMetrics ? '收起指标' : `展开全部指标（${metrics.length} 项）` }}</el-button
            >
            <h4>批次趋势（最近 {{ trendItems.length }} 个批次）</h4>
            <el-table
              :data="trendItems"
              row-key="run_id"
              class="evaluation-datasets evaluation-trend-table"
              size="small"
            >
              <el-table-column label="批次时间" width="155">
                <template #default="{ row }">
                  <button class="evaluation-link" type="button" @click="openRunDetail(row.run_id)">
                    {{ traceTime(row.created_at) }}
                  </button>
                </template>
              </el-table-column>
              <el-table-column label="算法版本" width="115">
                <template #default="{ row }">{{ row.algorithm_version || '未绑定' }}</template>
              </el-table-column>
              <el-table-column label="执行方式" width="100">
                <template #default="{ row }">{{ evaluationModeLabel(row.mode) }}</template>
              </el-table-column>
              <el-table-column
                v-for="spec in tableMetrics"
                :key="spec.key"
                :label="shortMetricLabel(spec)"
                width="100"
              >
                <template #default="{ row }">
                  <el-tooltip :content="metricTooltip(spec)" placement="top">
                    <span>{{ formatMetricValue(spec, row.summary?.[spec.key] ?? null) }}</span>
                  </el-tooltip>
                </template>
              </el-table-column>
              <el-table-column label="门禁" width="70">
                <template #default="{ row }">
                  <el-tag :type="row.gate_passed ? 'success' : 'danger'" size="small">
                    {{ row.gate_passed ? '通过' : '阻断' }}
                  </el-tag>
                </template>
              </el-table-column>
              <el-table-column label="基线" width="70">
                <template #default="{ row }">
                  <el-tag v-if="row.is_baseline" type="warning" size="small">基线</el-tag>
                  <el-tag v-else-if="!row.comparable" type="info" size="small">不可比</el-tag>
                  <span v-else class="evaluation-meta">—</span>
                </template>
              </el-table-column>
              <el-table-column label="操作" width="70" fixed="right">
                <template #default="{ row }">
                  <el-button link type="primary" @click="openRunDetail(row.run_id)">查看</el-button>
                </template>
              </el-table-column>
            </el-table>
          </template>
          <div v-else class="evaluation-empty-card">
            <strong>该版本暂无 {{ evalKindLabel }} 批次</strong>
            <p class="evaluation-meta">
              数据集版本 {{ datasetVersion || '—' }} · 口径 {{ evaluationScopeLabel(scope) }} ·
              执行方式 {{ evaluationModeLabel(mode) }}
            </p>
            <el-button type="primary" @click="emit('launch')">去测评启动</el-button>
          </div>
        </div>
      </el-tab-pane>

      <el-tab-pane v-if="view === 'results'" label="批次" name="runs">
        <div class="evaluation-tab">
          <div class="evaluation-subsection-head">
            <h4>
              评测批次
              <el-tooltip
                content="指标列来自指标目录；同一数据集、口径与执行方式再次运行会保留原批次并递增运行次数。默认只显示核心指标，可切换到全部指标。"
                placement="top"
              >
                <span class="evaluation-help">?</span>
              </el-tooltip>
            </h4>
            <div class="evaluation-filters evaluation-filters-inline">
              <TenantOwnerSelect v-if="isAdmin" v-model="owner" :api="api" />
              <el-select
                v-model="runDatasetId"
                clearable
                placeholder="全部数据集"
                aria-label="按数据集筛选批次"
              >
                <el-option value="" label="全部数据集" />
                <el-option
                  v-for="dataset in kindDatasets"
                  :key="dataset.dataset_id"
                  :value="dataset.dataset_id"
                  :label="`${dataset.name}（${dataset.version}）`"
                />
              </el-select>
              <el-switch v-model="showAllRunMetrics" active-text="显示全部指标" />
              <el-button :loading="runsBusy" @click="refreshRuns">刷新</el-button>
            </div>
          </div>
          <el-alert
            v-if="runsError"
            type="warning"
            show-icon
            :title="runsError"
            :closable="false"
          />
          <div v-else-if="!kindDatasets.length" class="evaluation-hint-card">
            当前评测体系尚未登记数据集，请先到「测评启动」登记算法版本并发起评测。
          </div>
          <div v-else-if="!metrics.length" class="evaluation-hint-card">
            当前口径尚未登记指标，暂无可展示的批次指标列。
          </div>
          <el-table
            v-else
            v-loading="runsBusy"
            :data="runs"
            row-key="run_id"
            class="evaluation-datasets evaluation-runs-table"
            size="small"
            empty-text="当前范围内没有评测批次"
          >
            <el-table-column label="批次时间" width="130">
              <template #default="{ row }">
                <button
                  class="evaluation-link"
                  type="button"
                  :title="row.run_id"
                  @click="openRunDetail(row.run_id)"
                >
                  {{ traceTime(row.created_at) }}
                </button>
              </template>
            </el-table-column>
            <el-table-column label="运行次数" width="86">
              <template #default="{ row }">
                <el-tooltip placement="top">
                  <template #content>
                    <div>批次：{{ row.run_id }}</div>
                    <div>运行备注：{{ row.trigger_note || '—' }}</div>
                    <div>重跑来源：{{ row.parent_run_id || '—' }}</div>
                  </template>
                  <span class="evaluation-run-seq">
                    <el-tag v-if="row.run_seq" effect="plain" size="small"
                      >第 {{ row.run_seq }} 次</el-tag
                    >
                    <span v-else class="evaluation-meta">—</span>
                  </span>
                </el-tooltip>
              </template>
            </el-table-column>
            <el-table-column label="数据集版本" width="110">
              <template #default="{ row }">{{ row.dataset_version || '—' }}</template>
            </el-table-column>
            <el-table-column label="算法版本" width="90">
              <template #default="{ row }">{{ row.profile_version_tag || '未绑定' }}</template>
            </el-table-column>
            <el-table-column label="执行方式" width="90">
              <template #default="{ row }">{{ evaluationModeLabel(row.mode) }}</template>
            </el-table-column>
            <el-table-column
              v-for="spec in tableMetrics"
              :key="spec.key"
              :label="shortMetricLabel(spec)"
              width="90"
            >
              <template #default="{ row }">
                <el-tooltip :content="metricTooltip(spec)" placement="top">
                  <span>{{ formatMetricValue(spec, row.summary?.[spec.key] ?? null) }}</span>
                </el-tooltip>
              </template>
            </el-table-column>
            <el-table-column label="门禁" width="60">
              <template #default="{ row }">
                <el-tag :type="row.gate_passed ? 'success' : 'danger'" size="small">
                  {{ row.gate_passed ? '通过' : '阻断' }}
                </el-tag>
              </template>
            </el-table-column>
            <el-table-column label="基线" width="60">
              <template #default="{ row }">
                <el-tag v-if="row.is_baseline" type="warning" size="small">基线</el-tag>
                <el-tag v-else-if="!row.comparable" type="info" size="small">不可比</el-tag>
                <span v-else class="evaluation-meta">—</span>
              </template>
            </el-table-column>
            <el-table-column label="操作" width="152" fixed="right">
              <template #default="{ row }">
                <el-button link type="primary" @click="openRunDetail(row.run_id)">查看</el-button>
                <el-button link type="primary" @click="downloadRunReport(row.run_id)"
                  >原始报告</el-button
                >
                <el-button
                  v-if="canWriteEval && row.dataset_id && row.mode === 'live'"
                  link
                  type="primary"
                  @click="openRerunDialog(row)"
                  >复跑</el-button
                >
              </template>
            </el-table-column>
          </el-table>
          <nav class="evaluation-pager" aria-label="评测批次分页">
            <span>第 {{ runsPageNo }} 页</span>
            <el-button :disabled="runsBusy || runsPageNo === 1" @click="turnRuns(false)"
              >上一页</el-button
            >
            <el-button :disabled="runsBusy || !runsNext" @click="turnRuns(true)">下一页</el-button>
          </nav>
        </div>
      </el-tab-pane>

      <el-tab-pane v-if="view === 'results'" label="基线" name="baselines">
        <div class="evaluation-tab">
          <div class="evaluation-subsection-head">
            <h4>
              评测基线
              <el-tooltip
                content="基线按「数据集版本 + 口径 + 执行方式」唯一：同一组合只保留一个 active 基线，概览的环比使用 active 基线。"
                placement="top"
              >
                <span class="evaluation-help">?</span>
              </el-tooltip>
            </h4>
            <div class="evaluation-filters evaluation-filters-inline">
              <el-switch v-model="baselineActiveOnly" active-text="只看 active" />
              <el-button :loading="baselinesBusy" @click="loadBaselines">刷新</el-button>
            </div>
          </div>
          <el-alert
            v-if="baselinesError"
            type="warning"
            show-icon
            :title="baselinesError"
            :closable="false"
          />
          <el-table
            v-else
            v-loading="baselinesBusy"
            :data="baselines"
            row-key="baseline_id"
            class="evaluation-datasets"
            empty-text="当前范围还没有基线，可在批次页把批次设为基线"
          >
            <el-table-column label="状态" width="85">
              <template #default="{ row }">
                <el-tag :type="row.active ? 'success' : 'info'" size="small">{{
                  row.active ? 'active' : '历史'
                }}</el-tag>
              </template>
            </el-table-column>
            <el-table-column label="数据集版本" width="120">
              <template #default="{ row }">{{ row.dataset_version }}</template>
            </el-table-column>
            <el-table-column label="口径" width="95">
              <template #default="{ row }">{{ evaluationScopeLabel(row.scope) }}</template>
            </el-table-column>
            <el-table-column label="执行方式" min-width="125">
              <template #default="{ row }">{{ evaluationModeLabel(row.mode) }}</template>
            </el-table-column>
            <el-table-column label="批次" min-width="190">
              <template #default="{ row }">
                <button class="evaluation-link" type="button" @click="openRunDetail(row.run_id)">
                  {{ traceTime(row.run_created_at) }}
                </button>
                <small class="evaluation-meta">{{ row.run_id }}</small>
              </template>
            </el-table-column>
            <el-table-column :label="baselineMetricLabel" width="125">
              <template #default="{ row }">{{
                formatEvaluationPercent(row.run_pass_rate)
              }}</template>
            </el-table-column>
            <el-table-column label="门禁" width="90">
              <template #default="{ row }">
                <el-tag :type="row.run_gate_passed ? 'success' : 'danger'" size="small">
                  {{ row.run_gate_passed ? '通过' : '阻断' }}
                </el-tag>
              </template>
            </el-table-column>
            <el-table-column label="算法版本" min-width="120">
              <template #default="{ row }">{{ row.algorithm_version || '—' }}</template>
            </el-table-column>
            <el-table-column label="设置人" min-width="110">
              <template #default="{ row }">{{ row.created_by }}</template>
            </el-table-column>
            <el-table-column label="设置时间" min-width="150">
              <template #default="{ row }">{{ traceTime(row.created_at) }}</template>
            </el-table-column>
            <el-table-column label="备注" min-width="160">
              <template #default="{ row }">{{ row.note || '—' }}</template>
            </el-table-column>
          </el-table>
        </div>
      </el-tab-pane>

      <el-tab-pane v-if="view === 'launch'" label="发起评测" name="launch">
        <div class="evaluation-tab">
          <div class="evaluation-subsection-head">
            <h4>
              发起真实评测
              <el-tooltip
                content="评测由 Worker 真实执行；登记算法版本并选择数据集、口径与执行方式后触发，页面只展示任务号与当前进度，结果请到「测评结果」查看。"
                placement="top"
              >
                <span class="evaluation-help">?</span>
              </el-tooltip>
            </h4>
          </div>
          <el-alert
            v-if="!canWriteEval"
            type="info"
            show-icon
            :closable="false"
            title="当前账号可查看评测任务进度，触发/取消评测与登记算法版本需要企业负责人或系统管理员权限。"
          />
          <el-alert v-if="jobError" type="error" show-icon :title="jobError" :closable="false" />
          <p v-if="profilesError" class="evaluation-meta">{{ profilesError }}</p>
          <div v-if="!activeJob" class="evaluation-launch-card">
            <div class="evaluation-launch-copy">
              <strong>{{ evalKindLabel }} 评测</strong>
              <p class="evaluation-meta">
                登记不可变算法版本，选择数据集、口径与真实执行方式后由 Worker 执行。
              </p>
            </div>
            <div v-if="canWriteEval" class="evaluation-launch-actions">
              <el-button @click="openProfileDialog">登记算法版本</el-button>
              <el-button type="primary" @click="openTriggerDialog()">发起评测</el-button>
            </div>
          </div>
          <template v-else>
            <div class="evaluation-job-head">
              <strong>{{ activeJob.job_id }}</strong>
              <el-tag :type="stateTag(activeJob.state)" size="small">{{
                stateText(activeJob.state)
              }}</el-tag>
              <span class="evaluation-meta">{{ activeJob.stage || '等待 Worker 调度' }}</span>
              <el-button v-if="canWriteEval && !jobTerminal" :loading="jobBusy" @click="cancelJob"
                >取消任务</el-button
              >
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
            <el-alert
              v-if="activeJob.error"
              type="error"
              :title="activeJob.error"
              :closable="false"
            />
            <p v-if="jobRunId" class="evaluation-meta">
              评测批次已入库：
              <button class="evaluation-link" type="button" @click="openRunDetail(jobRunId)">
                {{ jobRunId }}
              </button>
            </p>
          </template>
        </div>
      </el-tab-pane>

      <el-tab-pane v-if="view === 'results' && canWriteEval" label="审计" name="audit">
        <div class="evaluation-tab">
          <div class="evaluation-subsection-head">
            <h4>评测审计留痕</h4>
            <div class="evaluation-filters evaluation-filters-inline">
              <el-select v-model="auditAction" placeholder="全部动作" aria-label="按审计动作筛选">
                <el-option value="" label="全部动作" />
                <el-option
                  v-for="(label, code) in evaluationAuditActions"
                  :key="code"
                  :value="code"
                  :label="label"
                />
              </el-select>
              <el-button :loading="auditBusy" @click="loadAuditLogs">刷新</el-button>
            </div>
          </div>
          <el-alert
            v-if="auditError"
            type="error"
            show-icon
            :title="auditError"
            :closable="false"
          />
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
            <span>第 {{ auditCursors.length }} 页</span>
            <el-button :disabled="auditBusy || auditCursors.length === 1" @click="turnAudit(false)"
              >上一页</el-button
            >
            <el-button :disabled="auditBusy || !auditNext" @click="turnAudit(true)"
              >下一页</el-button
            >
          </nav>
        </div>
      </el-tab-pane>
    </el-tabs>

    <el-drawer
      v-model="runDetailOpen"
      :title="runDetail ? `批次详情 · ${traceTime(runDetail.run.created_at)}` : '批次详情'"
      size="76%"
    >
      <el-alert
        v-if="runDetailError"
        type="error"
        show-icon
        :title="runDetailError"
        :closable="false"
      />
      <el-skeleton v-else-if="runDetailBusy && !runDetail" :rows="5" animated />
      <template v-else-if="runDetail">
        <dl class="evaluation-facts">
          <div>
            <dt>数据集版本</dt>
            <dd>{{ runDetail.run.dataset_version || '—' }}</dd>
          </div>
          <div>
            <dt>算法版本</dt>
            <dd>{{ runDetail.run.profile_version_tag || '未绑定' }}</dd>
          </div>
          <div>
            <dt>运行次数</dt>
            <dd>{{ runDetail.run.run_seq ? `第 ${runDetail.run.run_seq} 次` : '—' }}</dd>
          </div>
          <div>
            <dt>执行方式与口径</dt>
            <dd>
              {{ evaluationModeLabel(runDetail.run.mode) }} ·
              {{ evaluationScopeLabel(runDetail.run.scope) }}
            </dd>
          </div>
          <div>
            <dt>门禁</dt>
            <dd>
              <el-tag :type="runDetail.run.gate_passed ? 'success' : 'danger'" size="small">
                {{ runDetail.run.gate_passed ? '通过' : '阻断' }}
              </el-tag>
            </dd>
          </div>
          <div>
            <dt>基线</dt>
            <dd>
              <el-tag v-if="runDetail.run.is_baseline" type="warning" size="small">当前基线</el-tag>
              <span v-else class="evaluation-meta">—</span>
            </dd>
          </div>
          <div>
            <dt>发起人</dt>
            <dd>{{ runDetail.run.owner_name || runDetail.run.owner_id }}</dd>
          </div>
          <div v-if="runDetail.run.trigger_note">
            <dt>运行备注</dt>
            <dd>{{ runDetail.run.trigger_note }}</dd>
          </div>
          <div v-if="runDetail.run.parent_run_id">
            <dt>重跑来源</dt>
            <dd class="evaluation-code">{{ runDetail.run.parent_run_id }}</dd>
          </div>
        </dl>
        <div class="evaluation-detail-actions">
          <span class="evaluation-meta"
            >用例 {{ runDetail.run.passed_cases }} / {{ runDetail.run.total_cases }} 通过 · 失败
            {{ runDetail.run.failed_cases }} · 不可判定 {{ runDetail.run.unassessable_cases }}</span
          >
          <div class="evaluation-detail-action-buttons">
            <el-button
              :disabled="!runDetail.run.report_available"
              :title="runDetail.run.report_available ? '下载原始报告 JSON' : '该批次未保存原始报告'"
              @click="downloadRunReport(runDetail.run.run_id)"
              >下载原始报告</el-button
            >
            <el-button
              v-if="view === 'results' && canWriteEval && runDetail.run.dataset_id"
              @click="openBaselineDialog(runDetail.run)"
              >设为基线</el-button
            >
            <el-button
              v-if="
                view === 'results' &&
                canWriteEval &&
                runDetail.run.dataset_id &&
                runDetail.run.mode === 'live'
              "
              @click="openRerunDialog(runDetail.run)"
              >复跑</el-button
            >
          </div>
        </div>
        <div v-if="runDetail.dataset_snapshot" class="evaluation-snapshot-line">
          <span>
            数据集快照 {{ runDetail.dataset_snapshot.dataset_version }} · 用例
            {{ runDetail.dataset_snapshot.case_count }}
          </span>
          <el-button link type="primary" @click="openSnapshotCases(runDetail.run.run_id)"
            >查看原始用例</el-button
          >
        </div>
        <p v-else class="evaluation-meta">
          该批次没有数据集内容快照（历史批次未登记快照），无法查看本次运行使用的原始用例。
        </p>
        <div class="evaluation-detail-metrics-head">
          <h4>核心指标</h4>
          <el-button
            v-if="runMetricCards.length > detailMetricCards.length || showAllDetailMetrics"
            link
            type="primary"
            @click="showAllDetailMetrics = !showAllDetailMetrics"
            >{{
              showAllDetailMetrics ? '收起指标' : `展开全部指标（${runMetricCards.length} 项）`
            }}</el-button
          >
        </div>
        <div class="evaluation-metric-grid evaluation-metric-grid-detail">
          <el-tooltip
            v-for="card in detailMetricCards"
            :key="card.spec.key"
            placement="top"
            :content="metricTooltip(card.spec)"
          >
            <div class="evaluation-metric-card evaluation-metric-card-compact">
              <span class="evaluation-card-label">{{ shortMetricLabel(card.spec) }}</span>
              <strong class="evaluation-metric-value">{{
                formatMetricValue(card.spec, card.value)
              }}</strong>
            </div>
          </el-tooltip>
        </div>
        <el-collapse v-model="detailTechOpen" class="evaluation-tech-collapse">
          <el-collapse-item title="技术信息" name="tech">
            <dl class="evaluation-facts evaluation-facts-tech">
              <div>
                <dt>批次编号</dt>
                <dd class="evaluation-code">{{ runDetail.run.run_id }}</dd>
              </div>
              <div>
                <dt>快照编号</dt>
                <dd class="evaluation-code">{{ runDetail.run.dataset_snapshot_id || '—' }}</dd>
              </div>
              <div>
                <dt>快照 bundle sha256</dt>
                <dd class="evaluation-code">
                  {{ runDetail.dataset_snapshot?.bundle_sha256 || '—' }}
                </dd>
              </div>
              <div>
                <dt>数据集摘要</dt>
                <dd class="evaluation-code">{{ runDetail.run.dataset_sha256 }}</dd>
              </div>
              <div>
                <dt>判定器摘要</dt>
                <dd class="evaluation-code">{{ runDetail.run.judges_sha256 }}</dd>
              </div>
              <div>
                <dt>发起人账号</dt>
                <dd class="evaluation-code">{{ runDetail.run.owner_id }}</dd>
              </div>
            </dl>
          </el-collapse-item>
        </el-collapse>
        <h4>逐例结果</h4>
        <el-alert
          v-if="runCaseError"
          type="error"
          show-icon
          :title="runCaseError"
          :closable="false"
        />
        <el-table
          v-else
          v-loading="runCaseBusy"
          :data="runCaseRows"
          row-key="case_id"
          size="small"
          empty-text="该批次没有用例结果"
        >
          <el-table-column label="用例" min-width="230">
            <template #default="{ row }">
              <button class="evaluation-link" type="button" @click="openCaseJson(row.case_id)">
                {{ row.name }}
              </button>
              <small class="evaluation-meta evaluation-case-id" :title="row.case_id">{{
                row.case_id
              }}</small>
            </template>
          </el-table-column>
          <el-table-column label="分组" width="100">
            <template #default="{ row }">{{
              evaluationSuites[row.suite] || row.suite || '—'
            }}</template>
          </el-table-column>
          <el-table-column label="得分" width="70">
            <template #default="{ row }">{{
              typeof row.score === 'number' ? row.score.toFixed(2) : '—'
            }}</template>
          </el-table-column>
          <el-table-column label="判定" width="110">
            <template #default="{ row }">
              <el-tag :type="evaluationTags[row.status as EvaluationStatus]" size="small">{{
                evaluationStatuses[row.status as EvaluationStatus]
              }}</el-tag>
              <small v-if="row.hard_failure" class="evaluation-meta">硬性违规</small>
            </template>
          </el-table-column>
          <el-table-column label="操作" width="86" fixed="right">
            <template #default="{ row }">
              <el-button
                link
                type="primary"
                class="evaluation-json-btn"
                @click="openCaseJson(row.case_id)"
              >
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8">
                  <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z" />
                  <path d="M14 2v6h6" />
                </svg>
                JSON
              </el-button>
            </template>
          </el-table-column>
        </el-table>
        <nav class="evaluation-pager" aria-label="批次用例分页">
          <span>第 {{ runCaseCursors.length }} 页</span>
          <el-button
            :disabled="runCaseBusy || runCaseCursors.length === 1"
            @click="turnRunCase(false)"
            >上一页</el-button
          >
          <el-button :disabled="runCaseBusy || !runCaseNext" @click="turnRunCase(true)"
            >下一页</el-button
          >
        </nav>
      </template>
    </el-drawer>

    <el-dialog
      v-model="caseJsonDialog"
      title="用例原始判定 JSON"
      width="min(900px, 94vw)"
      :z-index="3000"
    >
      <p class="evaluation-meta">
        用例 {{ caseJsonCaseId }} · 以下为服务端返回的 expected / observed / verdicts
        原始字段，未裁剪； 末尾附该批次运行时固化的数据集原始用例。
      </p>
      <el-button
        v-if="caseJsonTrace"
        type="primary"
        plain
        class="evaluation-case-trace"
        @click="inspectCaseTrace"
        >查看关联执行轨迹</el-button
      >
      <pre class="evaluation-json">{{ caseJsonText }}</pre>
      <h4>数据集原始用例</h4>
      <p class="evaluation-meta">
        用例摘要 sha256 {{ caseJsonDatasetSha256 || '—' }} ·
        以下为该次运行数据集快照中保存的完整原始用例，未裁剪、未改名。
      </p>
      <pre class="evaluation-json">{{ caseJsonDatasetText }}</pre>
    </el-dialog>

    <el-drawer v-model="snapshotDrawer" title="数据集快照原始用例" size="72%">
      <el-alert
        v-if="snapshotError"
        type="error"
        show-icon
        :title="snapshotError"
        :closable="false"
      />
      <template v-else>
        <div v-if="snapshotMeta" class="evaluation-filters">
          <el-tag effect="plain" size="small">版本 {{ snapshotMeta.dataset_version }}</el-tag>
          <el-tag type="info" effect="plain" size="small"
            >bundle {{ shortSha(snapshotMeta.bundle_sha256) }}</el-tag
          >
          <el-tag type="success" effect="plain" size="small"
            >用例 {{ snapshotMeta.case_count }}</el-tag
          >
          <span class="evaluation-meta">快照 {{ snapshotMeta.snapshot_id }}</span>
          <el-button :loading="snapshotBusy" @click="loadSnapshotCases">刷新</el-button>
        </div>
        <p class="evaluation-meta">
          以下为批次 <code>{{ snapshotRunId }}</code>
          运行时固化的数据集原始用例（按套件与序号排序）；每条可查看完整 case_payload
          JSON，不读取当前数据集。
        </p>
        <el-table
          v-loading="snapshotBusy"
          :data="snapshotCases"
          row-key="case_id"
          max-height="520"
          empty-text="该快照没有登记原始用例"
        >
          <el-table-column label="套件" width="150">
            <template #default="{ row }">{{
              evaluationSuites[row.suite] || row.suite || '—'
            }}</template>
          </el-table-column>
          <el-table-column label="用例 ID" width="170" prop="case_id" />
          <el-table-column label="序号" width="80" prop="ordinal" />
          <el-table-column label="用例摘要" width="150">
            <template #default="{ row }">{{ shortSha(row.case_sha256) }}</template>
          </el-table-column>
          <el-table-column label="操作" width="86" fixed="right">
            <template #default="{ row }">
              <el-button
                link
                type="primary"
                class="evaluation-json-btn"
                @click="openSnapshotCaseJson(row)"
              >
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8">
                  <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z" />
                  <path d="M14 2v6h6" />
                </svg>
                JSON
              </el-button>
            </template>
          </el-table-column>
        </el-table>
        <nav class="evaluation-pager" aria-label="数据集快照用例分页">
          <span>第 {{ snapshotCursors.length }} 页 / 共 {{ snapshotTotal }} 条</span>
          <el-button
            :disabled="snapshotBusy || snapshotCursors.length === 1"
            @click="turnSnapshotCase(false)"
            >上一页</el-button
          >
          <el-button :disabled="snapshotBusy || !snapshotNext" @click="turnSnapshotCase(true)"
            >下一页</el-button
          >
        </nav>
      </template>
    </el-drawer>

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

    <el-dialog
      v-model="rawCaseDialog"
      :title="rawCaseTitle ? `原始用例 JSON：${rawCaseTitle}` : '原始用例 JSON'"
      width="min(880px, 94vw)"
    >
      <p class="evaluation-meta">以下为该批次快照中保存的完整原始用例字段，未裁剪、未改名。</p>
      <pre class="evaluation-json">{{ rawCaseJson }}</pre>
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
  evaluationModes,
  evaluationScopes,
  evaluationScopeLabel,
  evaluationModeLabel,
  evaluationStatuses,
  evaluationSuites,
  evaluationTags,
  formatEvaluationPercent,
  formatMetricDelta,
  formatMetricValue,
  metricDeltaTone,
  type EvaluationAuditAction,
  type EvaluationAuditLog,
  type EvaluationBaseline,
  type EvaluationCase,
  type EvaluationCaseDetail,
  type EvaluationDataset,
  type EvaluationDatasetSnapshot,
  type EvaluationJob,
  type EvaluationJobState,
  type EvaluationKind,
  type EvaluationMetricCatalog,
  type EvaluationMetricSchema,
  type EvaluationMetricSpec,
  type EvaluationMode,
  type EvaluationProfile,
  type EvaluationRun,
  type EvaluationRunDetail,
  type EvaluationRunSnapshotCase,
  type EvaluationRunSnapshotPage,
  type EvaluationScope,
  type EvaluationStatus,
  type EvaluationTriggerReceipt,
  type EvaluationTrends,
  type TriggerEvaluationPayload,
} from './evaluationTypes'
import TenantOwnerSelect from './TenantOwnerSelect.vue'
import './evaluations.css'

const TREND_LIMIT = 30
const RUN_PAGE_SIZE = 25
const RUN_CASE_PAGE_SIZE = 30
const SNAPSHOT_CASE_PAGE_SIZE = 20
const AUDIT_PAGE_SIZE = 30
const JOB_POLL_MS = 3000
const JOB_TERMINAL_STATES = new Set<EvaluationJobState>([
  'completed',
  'cancelled',
  'failed',
  'rejected',
  'legacy',
])

interface MetricCard {
  spec: EvaluationMetricSpec
  latest: number | null
  baseline: number | null
  delta: number | null
}

interface RunMetricCard {
  spec: EvaluationMetricSpec
  value: number | null
}

const props = defineProps<{
  isAdmin: boolean
  canWriteEval: boolean
  api: ApiClient
  view: 'launch' | 'results'
  kind: EvaluationKind
  focusRunId?: string | null
}>()
const emit = defineEmits<{
  (e: 'inspect', taskId: string, traceId: string): void
  (e: 'launch'): void
  (e: 'focus-consumed'): void
}>()

const CORE_METRIC_COUNT = 5

const evalKind = computed<EvaluationKind>(() => props.kind)
const evalKindLabel = computed(() => (props.kind === 'agent' ? 'Agent' : 'RAG'))
const centerTitle = computed(() => `${evalKindLabel.value} 评测`)
const activeTab = ref(props.view === 'launch' ? 'launch' : 'overview')
const owner = ref('all')
const scope = ref<EvaluationScope>('full')
const mode = ref<EvaluationMode>('live')
const datasetVersion = ref('')
const refreshing = ref(false)
const showAllMetrics = ref(false)
const showAllRunMetrics = ref(false)
const showAllDetailMetrics = ref(false)
const detailTechOpen = ref<string[]>([])

const catalog = ref<EvaluationMetricCatalog | null>(null)
const catalogError = ref('')

const datasets = ref<EvaluationDataset[]>([])
const datasetsBusy = ref(false)
const datasetsError = ref('')
const trends = ref<EvaluationTrends | null>(null)
const trendsBusy = ref(false)
const trendsError = ref('')

const runDatasetId = ref('')

const baselines = ref<EvaluationBaseline[]>([])
const baselinesBusy = ref(false)
const baselinesError = ref('')
const baselineActiveOnly = ref(true)

const profiles = ref<EvaluationProfile[]>([])
const profilesError = ref('')

const auditAction = ref<EvaluationAuditAction | ''>('')
const auditLogs = ref<EvaluationAuditLog[]>([])
const auditCursors = ref<string[]>([''])
const auditNext = ref<string | null>(null)
const auditBusy = ref(false)
const auditError = ref('')

const rawCaseDialog = ref(false)
const rawCaseJson = ref('')
const rawCaseTitle = ref('')
const runDetailOpen = ref(false)
const runDetailId = ref('')
const runDetail = ref<EvaluationRunDetail | null>(null)
const runDetailBusy = ref(false)
const runDetailError = ref('')
const runCaseRows = ref<EvaluationCase[]>([])
const runCaseCursors = ref<string[]>([''])
const runCaseNext = ref<string | null>(null)
const runCaseBusy = ref(false)
const runCaseError = ref('')
const caseJsonDialog = ref(false)
const caseJsonText = ref('')
const caseJsonCaseId = ref('')
const caseJsonTrace = ref<{ task_id: string; trace_id: string } | null>(null)
const caseJsonDatasetText = ref('')
const caseJsonDatasetSha256 = ref<string | null>(null)

const snapshotDrawer = ref(false)
const snapshotRunId = ref('')
const snapshotMeta = ref<EvaluationDatasetSnapshot | null>(null)
const snapshotCases = ref<EvaluationRunSnapshotCase[]>([])
const snapshotCursors = ref<string[]>([''])
const snapshotNext = ref<string | null>(null)
const snapshotTotal = ref(0)
const snapshotBusy = ref(false)
const snapshotError = ref('')

const activeJob = ref<EvaluationJob | null>(null)
const jobError = ref('')
const jobBusy = ref(false)

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

let trendRequest = 0
let datasetRequest = 0
let baselineRequest = 0
let profileRequest = 0
let runDetailRequest = 0
let runCaseRequest = 0
let snapshotRequest = 0
let jobTimer: number | null = null
let polledJobId = ''

const kindDatasets = computed(() =>
  datasets.value.filter((item) => item.eval_kind === evalKind.value),
)
const activeDatasets = computed(() => kindDatasets.value.filter((item) => !item.archived))
const datasetVersions = computed(() => {
  const versions = new Set(activeDatasets.value.map((item) => item.version))
  return [...versions].sort((a, b) => b.localeCompare(a))
})
const selectedVersionDatasets = computed(() =>
  activeDatasets.value.filter((item) => item.version === datasetVersion.value),
)
const primaryDataset = computed(
  () => selectedVersionDatasets.value[0] ?? activeDatasets.value[0] ?? null,
)
const allowedModes = computed<EvaluationMode[]>(() => datasetExecutionModes(primaryDataset.value))
const scopeOptions = computed<{ value: EvaluationScope; label: string }[]>(() => {
  const kind = catalog.value?.kinds[evalKind.value]
  if (kind) {
    return Object.entries(kind.scopes).map(([value, item]) => ({
      value: value as EvaluationScope,
      label: evaluationScopes[value as EvaluationScope] ?? item.label,
    }))
  }
  const fallback: EvaluationScope[] =
    evalKind.value === 'rag' ? ['full', 'quick', 'adversarial', 'retrieval', 'behavior'] : ['full']
  return fallback.map((value) => ({ value, label: evaluationScopes[value] }))
})
const schema = computed<EvaluationMetricSchema | null>(() => {
  const fromCatalog = catalog.value?.kinds[evalKind.value]?.scopes[scope.value]
  if (fromCatalog) return fromCatalog
  const fromTrends = trends.value?.metric_schema
  if (fromTrends && fromTrends.eval_kind === evalKind.value && fromTrends.scope === scope.value) {
    return fromTrends
  }
  return null
})
const metrics = computed<EvaluationMetricSpec[]>(() => schema.value?.metrics ?? [])

/**
 * 按指标目录顺序挑选核心指标：优先比例/得分类，不足时回退原始顺序。
 * @param specs - 当前口径的指标定义。
 * @returns 最多 5 个核心指标定义。
 */
function selectCoreMetrics(specs: EvaluationMetricSpec[]): EvaluationMetricSpec[] {
  const preferred = specs.filter(
    (spec) => spec.unit === 'ratio' || spec.unit === 'percent' || spec.unit === 'score',
  )
  const source = preferred.length ? preferred : specs
  return source.slice(0, CORE_METRIC_COUNT)
}

/**
 * 生成指标卡与表格列的单行中文短标签，括号里的英文与公式交给 tooltip。
 * @param spec - 指标定义。
 * @returns 去掉括号说明后的短标签。
 */
function shortMetricLabel(spec: EvaluationMetricSpec): string {
  return spec.label.replace(/\s*[（(][^（）()]*[）)]\s*/g, '').trim() || spec.label
}

/**
 * 组合指标的悬浮说明：完整名称与口径描述。
 * @param spec - 指标定义。
 * @returns 可放入 el-tooltip 的说明文本。
 */
function metricTooltip(spec: EvaluationMetricSpec): string {
  return spec.description ? `${spec.label}\n${spec.description}` : spec.label
}

const trendItems = computed(() => trends.value?.items ?? [])
const latestTrend = computed(() => trendItems.value.at(-1) ?? null)
const baselineTrend = computed(() => {
  const summary = trends.value?.baseline
  if (!summary) return null
  return (
    trendItems.value.find((item) => item.run_id === summary.run_id) ??
    trendItems.value.find((item) => item.is_baseline) ??
    null
  )
})
const baselineComparable = computed(() => {
  const latest = latestTrend.value
  const baseline = baselineTrend.value
  return Boolean(latest && baseline && latest.comparable)
})
const metricCards = computed<MetricCard[]>(() =>
  metrics.value.map((spec) => {
    const latest = latestTrend.value?.summary?.[spec.key] ?? null
    const baseline = baselineTrend.value?.summary?.[spec.key] ?? null
    const delta =
      baselineComparable.value && latest !== null && baseline !== null ? latest - baseline : null
    return { spec, latest, baseline, delta }
  }),
)
const aggregateMetric = computed<EvaluationMetricSpec | null>(
  () => metrics.value.find((item) => item.unit === 'ratio' || item.unit === 'percent') ?? null,
)
const aggregateStats = computed(() => {
  const spec = aggregateMetric.value
  const values = trendItems.value
    .map((item) => (spec ? (item.summary?.[spec.key] ?? null) : null))
    .filter((value): value is number => value !== null)
  return {
    batches: trends.value?.aggregate.batches ?? trendItems.value.length,
    latest: values.length ? values[values.length - 1] : null,
    average: values.length ? values.reduce((sum, value) => sum + value, 0) / values.length : null,
    best: values.length ? Math.max(...values) : null,
    label: spec?.label ?? '通过率',
  }
})
const runPassMetricKey = computed(() => {
  if (scope.value === 'adversarial' || scope.value === 'quick') return 'adversarial.pass_rate'
  if (scope.value === 'full' || scope.value === 'behavior') return 'behavior.pass_rate'
  return 'task_completion'
})
const baselineMetricLabel = computed(() => {
  const key = runPassMetricKey.value
  return metrics.value.find((item) => item.key === key)?.label ?? '通过率'
})
const coreMetrics = computed<EvaluationMetricSpec[]>(() => selectCoreMetrics(metrics.value))
const coreMetricKeys = computed(() => new Set(coreMetrics.value.map((spec) => spec.key)))
const overviewMetricCards = computed<MetricCard[]>(() =>
  showAllMetrics.value
    ? metricCards.value
    : metricCards.value.filter((card) => coreMetricKeys.value.has(card.spec.key)),
)
const tableMetrics = computed<EvaluationMetricSpec[]>(() =>
  showAllRunMetrics.value ? metrics.value : coreMetrics.value.slice(0, 3),
)
const detailCoreCards = computed<RunMetricCard[]>(() => {
  const core = selectCoreMetrics(runMetricCards.value.map((card) => card.spec))
  const keys = new Set(core.map((spec) => spec.key))
  return runMetricCards.value.filter((card) => keys.has(card.spec.key))
})
const detailMetricCards = computed<RunMetricCard[]>(() =>
  showAllDetailMetrics.value ? runMetricCards.value : detailCoreCards.value,
)
const aggregateLabel = computed(() =>
  aggregateMetric.value ? shortMetricLabel(aggregateMetric.value) : '通过率',
)

const runEndpoint = computed(() => {
  const query = new URLSearchParams({
    limit: String(RUN_PAGE_SIZE),
    eval_kind: evalKind.value,
    scope: scope.value,
    mode: mode.value,
  })
  if (props.isAdmin && owner.value) query.set('owner_id', owner.value)
  if (runDatasetId.value) query.set('dataset_id', runDatasetId.value)
  return '/evaluations/runs?' + query
})
const {
  rows: runs,
  nextCursor: runsNext,
  busy: runsBusy,
  error: runsError,
  page: runsPageNo,
  refresh: refreshRuns,
  reset: resetRuns,
  turn: turnRuns,
} = useCursorPage<EvaluationRun>(props.api, runEndpoint)

const triggerDatasets = computed(() => activeDatasets.value)
const triggerSelectedDataset = computed(
  () => triggerDatasets.value.find((item) => item.dataset_id === triggerForm.dataset_id) ?? null,
)
const triggerScopeOptions = computed<EvaluationScope[]>(() => {
  const registered = datasetScopeList(triggerSelectedDataset.value)
  const known = new Set(scopeOptions.value.map((item) => item.value))
  const filtered = registered.filter((item) => known.has(item))
  return filtered.length ? filtered : scopeOptions.value.map((item) => item.value)
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
const rerunNextSeq = computed(() => (rerunRun.value?.run_seq ?? 0) + 1)
const runMetricCards = computed<RunMetricCard[]>(() => {
  const detailMetrics = runDetail.value?.metric_schema?.metrics ?? schema.value?.metrics ?? []
  return detailMetrics.map((spec) => ({
    spec,
    value: runDetail.value?.run.summary?.[spec.key] ?? null,
  }))
})

/**
 * 读取指标目录；目录是页面所有指标列与卡片的唯一来源。
 */
async function loadCatalog(): Promise<void> {
  catalogError.value = ''
  try {
    catalog.value = await props.api<EvaluationMetricCatalog>('/evaluations/metrics/catalog')
    ensureSelections()
  } catch (cause) {
    catalogError.value = cause instanceof Error ? cause.message : '读取指标目录失败'
  }
}

/**
 * 读取当前评测体系下可见的数据集版本（含归档，由开关控制展示）。
 */
async function loadDatasets(): Promise<void> {
  const request = ++datasetRequest
  datasetsBusy.value = true
  datasetsError.value = ''
  try {
    const query = new URLSearchParams({
      eval_kind: evalKind.value,
      include_archived: 'true',
    })
    const page = await props.api<Items<EvaluationDataset>>('/evaluations/datasets?' + query)
    if (request !== datasetRequest) return
    datasets.value = page.items
    ensureSelections()
  } catch (cause) {
    if (request === datasetRequest) {
      datasetsError.value = cause instanceof Error ? cause.message : '读取数据集失败'
    }
  } finally {
    if (request === datasetRequest) datasetsBusy.value = false
  }
}

/**
 * 读取当前 kind + 数据集版本 + 口径 + 执行方式的指标趋势与 active 基线摘要。
 */
async function loadTrends(): Promise<void> {
  const request = ++trendRequest
  trendsBusy.value = true
  trendsError.value = ''
  try {
    const query = new URLSearchParams({
      eval_kind: evalKind.value,
      scope: scope.value,
      mode: mode.value,
      limit: String(TREND_LIMIT),
    })
    if (datasetVersion.value) query.set('dataset_version', datasetVersion.value)
    const response = await props.api<EvaluationTrends>('/evaluations/metrics/trends?' + query)
    if (request !== trendRequest) return
    trends.value = response
  } catch (cause) {
    if (request === trendRequest) {
      trendsError.value = cause instanceof Error ? cause.message : '读取指标趋势失败'
    }
  } finally {
    if (request === trendRequest) trendsBusy.value = false
  }
}

/**
 * 读取算法版本列表，供发起评测与任务页选择。
 */
async function loadProfiles(): Promise<void> {
  const request = ++profileRequest
  profilesError.value = ''
  try {
    const query = new URLSearchParams({ eval_kind: evalKind.value })
    if (datasetVersion.value) query.set('dataset_version', datasetVersion.value)
    const page = await props.api<Items<EvaluationProfile>>('/evaluations/profiles?' + query)
    if (request !== profileRequest) return
    profiles.value = page.items
  } catch (cause) {
    if (request === profileRequest) {
      profilesError.value = cause instanceof Error ? cause.message : '读取算法版本失败'
    }
  }
}

/**
 * 读取当前评测体系与数据集版本下的基线（active 与历史）。
 */
async function loadBaselines(): Promise<void> {
  const request = ++baselineRequest
  baselinesBusy.value = true
  baselinesError.value = ''
  try {
    const query = new URLSearchParams({
      eval_kind: evalKind.value,
      include_history: String(!baselineActiveOnly.value),
    })
    if (datasetVersion.value) query.set('dataset_version', datasetVersion.value)
    const page = await props.api<Items<EvaluationBaseline>>('/evaluations/baselines?' + query)
    if (request !== baselineRequest) return
    baselines.value = page.items
  } catch (cause) {
    if (request === baselineRequest) {
      baselinesError.value = cause instanceof Error ? cause.message : '读取基线失败'
    }
  } finally {
    if (request === baselineRequest) baselinesBusy.value = false
  }
}

/**
 * 读取一页评测审计留痕；仅写入权限账号可读取。
 */
async function loadAuditLogs(): Promise<void> {
  if (!props.canWriteEval) return
  auditBusy.value = true
  auditError.value = ''
  try {
    const query = new URLSearchParams({ limit: String(AUDIT_PAGE_SIZE) })
    if (auditAction.value) query.set('action', auditAction.value)
    const cursor = auditCursors.value.at(-1)
    if (cursor) query.set('cursor', cursor)
    const page = await props.api<CursorPage<EvaluationAuditLog>>('/evaluations/audit-logs?' + query)
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
 * @param forward - 是否读取下一页。
 */
function turnAudit(forward: boolean): void {
  if (auditBusy.value) return
  if (forward && auditNext.value) auditCursors.value.push(auditNext.value)
  else if (!forward && auditCursors.value.length > 1) auditCursors.value.pop()
  else return
  void loadAuditLogs()
}

/**
 * 读取数据集可用于评测中心真实结果的执行方式：优先接口的 execution_modes，缺省时从元数据过滤。
 * @param dataset - 数据集条目。
 * @returns 仅包含真实执行的执行方式；无登记时回退为 live。
 */
function datasetExecutionModes(dataset: EvaluationDataset | null): EvaluationMode[] {
  const declaredModes: unknown[] = dataset?.execution_modes?.length
    ? dataset.execution_modes
    : Array.isArray(dataset?.metadata?.modes)
      ? dataset.metadata.modes
      : []
  const modes = declaredModes.filter((item): item is EvaluationMode => item === 'live')
  return modes.length ? modes : ['live']
}

/**
 * 读取数据集登记的评测口径清单。
 * @param dataset - 数据集条目。
 * @returns 已登记的口径；未登记时返回空数组。
 */
function datasetScopeList(dataset: EvaluationDataset | null): EvaluationScope[] {
  const value = dataset?.metadata?.scopes
  if (!Array.isArray(value)) return []
  return value.filter(
    (item): item is EvaluationScope =>
      item === 'full' ||
      item === 'quick' ||
      item === 'retrieval' ||
      item === 'adversarial' ||
      item === 'behavior',
  )
}

/**
 * 按指标目录与数据集登记能力校正版本、口径与执行方式的当前选择。
 */
function ensureSelections(): void {
  const versions = datasetVersions.value
  if (!datasetVersion.value || !versions.includes(datasetVersion.value)) {
    datasetVersion.value = versions[0] ?? ''
  }
  const scopeValues = scopeOptions.value.map((item) => item.value)
  if (!scopeValues.includes(scope.value)) {
    scope.value = scopeValues.includes('full') ? 'full' : (scopeValues[0] ?? 'full')
  }
  const modes = allowedModes.value
  if (!modes.includes(mode.value)) {
    mode.value = modes.includes('live') ? 'live' : (modes[0] ?? 'live')
  }
}

/**
 * 按当前页签装载其专属数据，保持各页签互不阻塞。
 * @param tab - 当前激活的页签名称。
 */
async function ensureTabData(tab: string): Promise<void> {
  if (tab === 'datasets') await loadDatasets()
  else if (tab === 'baselines') await loadBaselines()
  else if (tab === 'launch') await loadProfiles()
  else if (tab === 'audit') await loadAuditLogs()
  else if (tab === 'runs') await refreshRuns()
  else if (tab === 'overview') await loadTrends()
}

/**
 * 同时刷新当前页面职责范围内的数据与顶部控制区所需的目录。
 */
async function refreshAll(): Promise<void> {
  refreshing.value = true
  try {
    const requests: Promise<void>[] = [loadDatasets(), loadProfiles()]
    if (props.view === 'results') {
      requests.push(loadTrends(), refreshRuns(), loadBaselines())
    }
    await Promise.all(requests)
    if (props.view === 'results' && activeTab.value === 'audit' && props.canWriteEval) {
      await loadAuditLogs()
    }
  } finally {
    refreshing.value = false
  }
}

/**
 * 按单位格式化聚合卡片数值，缺少比例指标时回退为百分比。
 * @param value - 聚合统计值。
 * @returns 可读文本或缺失占位。
 */
function formatAggregateValue(value: number | null): string {
  const spec = aggregateMetric.value
  if (spec) return formatMetricValue(spec, value)
  return value === null ? '—' : (value * 100).toFixed(1) + '%'
}

/**
 * 截取 sha256 摘要前 12 位用于表格展示，完整值放在 title 提示中。
 * @param value - 摘要文本。
 * @returns 前 12 位摘要或缺失占位。
 */
function shortSha(value: string | null | undefined): string {
  return value ? value.slice(0, 12) : '—'
}

/**
 * 通过临时 <a> 元素触发浏览器下载并释放对象 URL。
 * @param blob - 文件内容。
 * @param filename - 保存文件名。
 */
function saveBlob(blob: Blob, filename: string): void {
  const url = URL.createObjectURL(blob)
  const link = document.createElement('a')
  link.href = url
  link.download = filename
  document.body.append(link)
  link.click()
  link.remove()
  URL.revokeObjectURL(url)
}

/**
 * 打开批次详情抽屉并装载指标 schema、逐例结果。
 * @param runId - 批次编号。
 */
async function openRunDetail(runId: string): Promise<void> {
  runDetailId.value = runId
  runDetail.value = null
  runDetailError.value = ''
  runDetailOpen.value = true
  runCaseRows.value = []
  runCaseCursors.value = ['']
  runCaseNext.value = null
  runCaseError.value = ''
  showAllDetailMetrics.value = false
  detailTechOpen.value = []
  await Promise.all([loadRunDetail(), loadRunCases()])
}

/**
 * 读取批次详情（含 summary 与 metric_schema）。
 */
async function loadRunDetail(): Promise<void> {
  const request = ++runDetailRequest
  runDetailBusy.value = true
  runDetailError.value = ''
  try {
    const detail = await props.api<EvaluationRunDetail>(
      '/evaluations/runs/' + encodeURIComponent(runDetailId.value),
    )
    if (request === runDetailRequest) runDetail.value = detail
  } catch (cause) {
    if (request === runDetailRequest) {
      runDetailError.value = cause instanceof Error ? cause.message : '读取批次详情失败'
    }
  } finally {
    if (request === runDetailRequest) runDetailBusy.value = false
  }
}

/**
 * 按游标读取批次逐例判定。
 */
async function loadRunCases(): Promise<void> {
  const request = ++runCaseRequest
  runCaseBusy.value = true
  runCaseError.value = ''
  try {
    const query = new URLSearchParams({ limit: String(RUN_CASE_PAGE_SIZE) })
    const cursor = runCaseCursors.value.at(-1)
    if (cursor) query.set('cursor', cursor)
    const page = await props.api<CursorPage<EvaluationCase>>(
      '/evaluations/runs/' + encodeURIComponent(runDetailId.value) + '/cases?' + query,
    )
    if (request !== runCaseRequest) return
    runCaseRows.value = page.items
    runCaseNext.value = page.next_cursor
  } catch (cause) {
    if (request === runCaseRequest) {
      runCaseError.value = cause instanceof Error ? cause.message : '读取逐例结果失败'
    }
  } finally {
    if (request === runCaseRequest) runCaseBusy.value = false
  }
}

/**
 * 按游标前后翻动批次逐例结果页。
 * @param forward - 是否读取下一页。
 */
function turnRunCase(forward: boolean): void {
  if (runCaseBusy.value) return
  if (forward && runCaseNext.value) runCaseCursors.value.push(runCaseNext.value)
  else if (!forward && runCaseCursors.value.length > 1) runCaseCursors.value.pop()
  else return
  void loadRunCases()
}

/**
 * 打开批次数据集快照抽屉并读取第一页原始用例。
 * @param runId - 批次编号。
 */
async function openSnapshotCases(runId: string): Promise<void> {
  snapshotRunId.value = runId
  snapshotMeta.value = null
  snapshotCases.value = []
  snapshotCursors.value = ['']
  snapshotNext.value = null
  snapshotTotal.value = 0
  snapshotError.value = ''
  snapshotDrawer.value = true
  await loadSnapshotCases()
}

/**
 * 按游标读取批次数据集快照中的原始用例页。
 */
async function loadSnapshotCases(): Promise<void> {
  const request = ++snapshotRequest
  snapshotBusy.value = true
  snapshotError.value = ''
  try {
    const query = new URLSearchParams({ limit: String(SNAPSHOT_CASE_PAGE_SIZE) })
    const cursor = snapshotCursors.value.at(-1)
    if (cursor) query.set('cursor', cursor)
    const page = await props.api<EvaluationRunSnapshotPage>(
      '/evaluations/runs/' + encodeURIComponent(snapshotRunId.value) + '/dataset-snapshot?' + query,
    )
    if (request !== snapshotRequest) return
    snapshotMeta.value = page.snapshot
    snapshotCases.value = page.items
    snapshotNext.value = page.next_cursor
    snapshotTotal.value = page.total
  } catch (cause) {
    if (request === snapshotRequest) {
      snapshotError.value = cause instanceof Error ? cause.message : '读取数据集快照用例失败'
    }
  } finally {
    if (request === snapshotRequest) snapshotBusy.value = false
  }
}

/**
 * 按游标前后翻动数据集快照用例页。
 * @param forward - 是否读取下一页。
 */
function turnSnapshotCase(forward: boolean): void {
  if (snapshotBusy.value) return
  if (forward && snapshotNext.value) snapshotCursors.value.push(snapshotNext.value)
  else if (!forward && snapshotCursors.value.length > 1) snapshotCursors.value.pop()
  else return
  void loadSnapshotCases()
}

/**
 * 展示快照用例的完整原始 case_payload JSON，复用数据集原始 JSON 弹窗。
 * @param item - 快照中的原始用例记录。
 */
function openSnapshotCaseJson(item: EvaluationRunSnapshotCase): void {
  rawCaseJson.value = JSON.stringify(item.case_payload, null, 2)
  rawCaseTitle.value = item.suite + ' / ' + item.case_id
  rawCaseDialog.value = true
}

/**
 * 打开用例详情并展示 expected / observed / verdicts 与数据集快照原始用例。
 * @param caseId - 用例编号。
 */
async function openCaseJson(caseId: string): Promise<void> {
  if (!runDetail.value) return
  const runId = runDetail.value.run.run_id
  try {
    const detail = await props.api<EvaluationCaseDetail>(
      '/evaluations/runs/' + encodeURIComponent(runId) + '/cases/' + encodeURIComponent(caseId),
    )
    caseJsonCaseId.value = caseId
    caseJsonText.value = JSON.stringify(
      { expected: detail.expected, observed: detail.observed, verdicts: detail.verdicts },
      null,
      2,
    )
    caseJsonDatasetText.value = detail.dataset_case
      ? JSON.stringify(detail.dataset_case, null, 2)
      : runDetail.value.dataset_snapshot
        ? '该用例未在数据集快照中匹配到原始定义（历史批次结果与快照用例编号不一致）。'
        : '该批次没有数据集内容快照（历史批次未登记快照），无法查看数据集原始用例。'
    caseJsonDatasetSha256.value = detail.dataset_case_sha256
    caseJsonTrace.value = detail.trace_link
    caseJsonDialog.value = true
  } catch (cause) {
    ElMessage.error(cause instanceof Error ? cause.message : '读取用例原始判定失败')
  }
}

/**
 * 关闭用例 JSON 弹窗并跳转关联执行轨迹。
 */
function inspectCaseTrace(): void {
  const link = caseJsonTrace.value
  if (!link) return
  caseJsonDialog.value = false
  emit('inspect', link.task_id, link.trace_id)
}

/**
 * 下载批次原始报告 JSON；未保存原始报告时给出可见提示。
 * @param runId - 批次编号。
 */
async function downloadRunReport(runId: string): Promise<void> {
  try {
    const response = await fetch('/api/evaluations/runs/' + encodeURIComponent(runId) + '/report')
    if (!response.ok) {
      throw new Error(response.status === 404 ? '该批次未保存原始报告' : '下载原始报告失败')
    }
    saveBlob(await response.blob(), `eval-report-${runId}.json`)
  } catch (cause) {
    ElMessage.error(cause instanceof Error ? cause.message : '下载原始报告失败')
  }
}

/**
 * 打开把批次设为基线的弹窗。
 * @param run - 待设为基线的批次。
 */
function openBaselineDialog(run: EvaluationRun): void {
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
    await Promise.all([loadBaselines(), loadTrends(), refreshRuns()])
  } catch (cause) {
    baselineWriteError.value = cause instanceof Error ? cause.message : '设置基线失败'
  } finally {
    writeBusy.value = false
  }
}

/**
 * 解析配置弹窗中的 JSON 对象文本。
 * @param text - 用户输入的 JSON 文本。
 * @param label - 出错时提示的字段名称。
 * @returns 解析后的普通对象。
 * @throws 文本不是合法 JSON 对象时抛中文错误。
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
 * 打开受控评测发起弹窗，并按所选数据集登记能力预选口径与执行方式。
 * @param dataset - 从数据集页直接发起时预选的数据集。
 */
function openTriggerDialog(dataset?: EvaluationDataset): void {
  writeError.value = ''
  const preferred =
    dataset?.dataset_id || runDatasetId.value || triggerDatasets.value[0]?.dataset_id
  triggerForm.dataset_id = preferred || ''
  triggerForm.profile_id = ''
  triggerForm.note = ''
  triggerForm.mark_baseline = false
  const selected = triggerDatasets.value.find((item) => item.dataset_id === triggerForm.dataset_id)
  const scopes = datasetScopeList(selected ?? null)
  const modes = datasetExecutionModes(selected ?? null)
  triggerForm.scope = scopes.includes(scope.value)
    ? scope.value
    : scopes.includes('full')
      ? 'full'
      : (scopes[0] ?? scope.value)
  triggerForm.mode = modes.includes(mode.value) ? mode.value : (modes[0] ?? mode.value)
  triggerDialog.value = true
  void loadProfiles()
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
    presentTriggeredJob(receipt)
    ElMessage.success(
      `已提交评测任务（任务号 ${receipt.job_id}，审计号 ${receipt.audit_id}），由 Worker 执行`,
    )
    await Promise.all([loadDatasets(), loadTrends(), refreshRuns()])
  } catch (cause) {
    writeError.value = cause instanceof Error ? cause.message : '发起评测失败'
  } finally {
    writeBusy.value = false
  }
}

/**
 * 把触发成功的任务挂到当前面板并开始轮询进度。
 * @param receipt - 触发接口返回的任务号与审计号。
 */
function presentTriggeredJob(receipt: EvaluationTriggerReceipt): void {
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
}

/**
 * 打开复跑弹窗，沿用来源批次的数据集、口径、执行方式与算法版本。
 * @param run - 要复跑的来源批次。
 */
function openRerunDialog(run: EvaluationRun): void {
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
    presentTriggeredJob(receipt)
    ElMessage.success(
      `已提交复跑任务（任务号 ${receipt.job_id}），完成后将记为第 ${rerunNextSeq.value} 次运行`,
    )
    await Promise.all([loadDatasets(), loadTrends(), refreshRuns()])
  } catch (cause) {
    rerunError.value = cause instanceof Error ? cause.message : '复跑评测失败'
  } finally {
    rerunBusy.value = false
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
 * @param jobId - 评测任务编号。
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
 * @param job - 已结束的评测任务。
 */
async function finishJob(job: EvaluationJob): Promise<void> {
  if (job.state === 'completed') {
    const result = job.result
    const runId = result && typeof result.run_id === 'string' ? result.run_id : ''
    ElMessage.success(runId ? `评测完成，批次 ${runId} 已入库` : '评测任务已完成')
    await Promise.all([loadDatasets(), loadTrends(), loadBaselines(), refreshRuns()])
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
 * @param jobId - 评测任务编号。
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
 * 展示审计动作的中文名称。
 * @param action - 审计动作编码。
 * @returns 中文动作名称或原始编码。
 */
function auditActionLabel(action: string): string {
  return evaluationAuditActions[action as EvaluationAuditAction] ?? action
}

/**
 * 将审计摘要压缩为单行文本。
 * @param value - 审计摘要对象。
 * @returns 截断后的单行摘要。
 */
function summarizeJson(value: Record<string, unknown>): string {
  const text = JSON.stringify(value)
  return text.length > 120 ? text.slice(0, 120) + '…' : text
}

watch(scope, () => {
  if (props.view === 'results') void loadTrends()
})
watch(mode, () => {
  if (props.view === 'results') {
    void loadTrends()
    void loadBaselines()
  }
})
watch(datasetVersion, () => {
  if (props.view === 'results') {
    void loadTrends()
    void loadBaselines()
  }
  void loadProfiles()
})
watch(runEndpoint, () => {
  if (props.view === 'results') resetRuns()
})
watch(baselineActiveOnly, () => {
  if (props.view === 'results') void loadBaselines()
})
watch(auditAction, () => {
  if (props.view === 'results') resetAudit()
})
watch(activeTab, (tab) => {
  void ensureTabData(tab)
})
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
watch(
  () => props.focusRunId,
  (runId) => {
    if (!runId) return
    void openRunDetail(runId)
    emit('focus-consumed')
  },
  { immediate: true, flush: 'post' },
)

onMounted(async () => {
  await loadCatalog()
  await loadDatasets()
  ensureSelections()
  if (props.view === 'results') await loadTrends()
  else await loadProfiles()
})
onBeforeUnmount(stopJobPolling)
</script>
