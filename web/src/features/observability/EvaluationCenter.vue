<template>
  <section class="panel evaluation-center">
    <div class="section-heading">
      <div>
        <h3>{{ centerTitle }}</h3>
        <small class="evaluation-meta">{{ centerSubtitle }}</small>
      </div>
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
      <span class="evaluation-control-summary">{{ schemaSummaryText }}</span>
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
        <div class="evaluation-tab">
          <el-alert
            v-if="trendsError"
            type="warning"
            show-icon
            :title="trendsError"
            :closable="false"
          />
          <el-skeleton v-else-if="!trends" :rows="4" animated />
          <template v-else>
            <div class="evaluation-schema-bar">
              <div>
                <strong>{{ schema ? schema.label : '当前口径没有指标定义' }}</strong>
                <small class="evaluation-meta">
                  dataset 版本 {{ trends.dataset_version || datasetVersion || '—' }} · 执行方式
                  {{ trends.mode ? evaluationModeLabel(trends.mode) : '—' }} · 批次数
                  {{ trends.aggregate.batches }}
                </small>
              </div>
              <el-tag effect="plain" size="small">
                schema v{{ schema ? schema.version : '—' }} · {{ metrics.length }} 项指标
              </el-tag>
            </div>
            <p class="evaluation-meta">{{ baselineHint }}</p>
            <div class="evaluation-metric-grid">
              <div v-for="card in metricCards" :key="card.spec.key" class="evaluation-metric-card">
                <div class="evaluation-metric-card-head">
                  <span class="evaluation-card-label">{{ card.spec.label }}</span>
                  <el-tag effect="plain" size="small">{{ card.spec.group }}</el-tag>
                </div>
                <strong class="evaluation-metric-value">{{
                  formatMetricValue(card.spec, card.latest)
                }}</strong>
                <span class="evaluation-meta">
                  <template v-if="card.delta !== null">
                    环比基线
                    <span :class="metricDeltaTone(card.spec, card.delta)">{{
                      formatMetricDelta(card.spec, card.delta)
                    }}</span>
                  </template>
                  <template v-else
                    >环比基线 —（{{ baselineTrend ? '不可比或缺测' : '无同口径基线' }}）</template
                  >
                </span>
                <small class="evaluation-meta"
                  >active 基线值 {{ formatMetricValue(card.spec, card.baseline) }} ·
                  {{
                    card.spec.direction === 'lower'
                      ? '越低越好'
                      : card.spec.direction === 'neutral'
                        ? '中性指标'
                        : '越高越好'
                  }}</small
                >
                <small v-if="card.spec.description" class="evaluation-meta">{{
                  card.spec.description
                }}</small>
              </div>
            </div>
            <dl class="evaluation-aggregate-grid">
              <div>
                <dt>批次数</dt>
                <dd>{{ aggregateStats.batches }}</dd>
              </div>
              <div>
                <dt>最近 · {{ aggregateStats.label }}</dt>
                <dd>{{ formatAggregateValue(aggregateStats.latest) }}</dd>
              </div>
              <div>
                <dt>平均 · {{ aggregateStats.label }}</dt>
                <dd>{{ formatAggregateValue(aggregateStats.average) }}</dd>
              </div>
              <div>
                <dt>最佳 · {{ aggregateStats.label }}</dt>
                <dd>{{ formatAggregateValue(aggregateStats.best) }}</dd>
              </div>
            </dl>
            <p class="evaluation-meta">
              聚合口径：按当前口径第一个比例指标「{{ aggregateStats.label }}」在最近
              {{ trends.items.length }}
              个批次内的摘要统计；指标列与单位全部来自指标目录，同口径固定同一组列。
            </p>
            <h4>批次趋势（最近 {{ trends.items.length }} 个批次）</h4>
            <el-table
              v-if="trends.items.length"
              :data="trends.items"
              row-key="run_id"
              class="evaluation-datasets"
              empty-text="当前口径还没有批次"
            >
              <el-table-column label="批次时间" min-width="170">
                <template #default="{ row }">
                  <button class="evaluation-link" type="button" @click="openRunDetail(row.run_id)">
                    {{ traceTime(row.created_at) }}
                  </button>
                  <small class="evaluation-meta">{{ row.run_id }}</small>
                </template>
              </el-table-column>
              <el-table-column label="算法版本" min-width="120">
                <template #default="{ row }">{{ row.algorithm_version || '未绑定' }}</template>
              </el-table-column>
              <el-table-column label="执行方式" min-width="130">
                <template #default="{ row }">{{ evaluationModeLabel(row.mode) }}</template>
              </el-table-column>
              <el-table-column
                v-for="spec in metrics"
                :key="spec.key"
                :label="spec.label"
                min-width="108"
              >
                <template #default="{ row }">
                  <el-tooltip v-if="spec.description" :content="spec.description" placement="top">
                    <span>{{ formatMetricValue(spec, row.summary?.[spec.key] ?? null) }}</span>
                  </el-tooltip>
                  <span v-else>{{ formatMetricValue(spec, row.summary?.[spec.key] ?? null) }}</span>
                </template>
              </el-table-column>
              <el-table-column label="门禁" width="90">
                <template #default="{ row }">
                  <el-tag :type="row.gate_passed ? 'success' : 'danger'" size="small">
                    {{ row.gate_passed ? '通过' : '阻断' }}
                  </el-tag>
                </template>
              </el-table-column>
              <el-table-column label="基线" width="90">
                <template #default="{ row }">
                  <el-tag v-if="row.is_baseline" type="warning" size="small">基线</el-tag>
                  <el-tag v-else-if="!row.comparable" type="info" size="small">不可比</el-tag>
                  <span v-else class="evaluation-meta">—</span>
                </template>
              </el-table-column>
              <el-table-column label="操作" width="150" fixed="right">
                <template #default="{ row }">
                  <el-button link type="primary" @click="openRunDetail(row.run_id)">查看</el-button>
                  <el-button link type="primary" @click="downloadRunReport(row.run_id)"
                    >原始报告</el-button
                  >
                </template>
              </el-table-column>
            </el-table>
            <p v-else class="evaluation-muted">当前口径还没有可比较的批次。</p>
          </template>
        </div>
      </el-tab-pane>

      <el-tab-pane v-if="view === 'results'" label="数据集" name="datasets">
        <div class="evaluation-tab">
          <div class="evaluation-subsection-head">
            <h4>评测数据集（按版本目录管理）</h4>
            <div class="evaluation-filters evaluation-filters-inline">
              <el-switch v-model="showArchived" active-text="显示归档版本" />
              <el-button :loading="datasetsBusy" @click="loadDatasets">刷新</el-button>
            </div>
          </div>
          <p class="evaluation-meta">
            数据集内容随 Git 版本化：页面对的仓库路径是
            <code>evals/datasets/&lt;版本&gt;/</code> 下的原始文件，文件 sha256
            与登记一致；用例字段不做裁剪或改名。
          </p>
          <el-alert
            v-if="datasetsError"
            type="warning"
            show-icon
            :title="datasetsError"
            :closable="false"
          />
          <el-table
            v-else
            v-loading="datasetsBusy"
            :data="visibleDatasets"
            row-key="dataset_id"
            class="evaluation-datasets"
            empty-text="当前评测体系还没有登记数据集"
          >
            <el-table-column label="数据集 / 版本" min-width="210">
              <template #default="{ row }">
                <strong>{{ row.name }}</strong>
                <small class="evaluation-meta">{{ row.dataset_id }}</small>
              </template>
            </el-table-column>
            <el-table-column label="版本" width="115">
              <template #default="{ row }">
                <el-tag effect="plain" size="small">{{ row.version }}</el-tag>
              </template>
            </el-table-column>
            <el-table-column label="类型" width="120">
              <template #default="{ row }">{{
                evaluationSuites[row.suite] || row.suite || '—'
              }}</template>
            </el-table-column>
            <el-table-column label="状态" width="90">
              <template #default="{ row }">
                <el-tag v-if="row.archived" type="info" size="small">归档</el-tag>
                <el-tag v-else type="success" size="small">生效</el-tag>
              </template>
            </el-table-column>
            <el-table-column label="用例数" width="85" prop="case_count" />
            <el-table-column label="内容 sha256" width="140">
              <template #default="{ row }">
                <span :title="row.content_sha256 || '未登记内容摘要'">{{
                  shortSha(row.content_sha256)
                }}</span>
              </template>
            </el-table-column>
            <el-table-column label="文件数" width="80">
              <template #default="{ row }">{{ datasetFileEntries(row).length }}</template>
            </el-table-column>
            <el-table-column label="批次数" width="85" prop="batch_count" />
            <el-table-column label="最近批次" min-width="145">
              <template #default="{ row }">{{ traceTime(row.last_run_at) }}</template>
            </el-table-column>
            <el-table-column label="操作" width="230" fixed="right">
              <template #default="{ row }">
                <el-button link type="primary" @click="inspectDataset(row)">查看数据集</el-button>
                <el-button link type="primary" @click="filterRunsByDataset(row)"
                  >筛选批次</el-button
                >
              </template>
            </el-table-column>
          </el-table>
        </div>
      </el-tab-pane>

      <el-tab-pane v-if="view === 'results'" label="批次" name="runs">
        <div class="evaluation-tab">
          <div class="evaluation-subsection-head">
            <h4>评测批次</h4>
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
              <el-button :loading="runsBusy" @click="refreshRuns">刷新</el-button>
            </div>
          </div>
          <p class="evaluation-meta">
            指标列由指标目录动态生成：{{ schema ? schema.label : '—' }}（schema v{{
              schema ? schema.version : '—'
            }}，共 {{ metrics.length }} 项）；同一口径下所有批次共用同一组列。
          </p>
          <el-alert
            v-if="runsError"
            type="warning"
            show-icon
            :title="runsError"
            :closable="false"
          />
          <el-table
            v-else
            v-loading="runsBusy"
            :data="runs"
            row-key="run_id"
            class="evaluation-datasets"
            empty-text="当前范围内没有评测批次"
          >
            <el-table-column label="批次时间" min-width="175">
              <template #default="{ row }">
                <button class="evaluation-link" type="button" @click="openRunDetail(row.run_id)">
                  {{ traceTime(row.created_at) }}
                </button>
                <small class="evaluation-meta">{{ row.run_id }}</small>
              </template>
            </el-table-column>
            <el-table-column label="数据集 / 算法版本" min-width="185">
              <template #default="{ row }">
                <span>{{ row.dataset_version || '未登记数据集版本' }}</span>
                <small class="evaluation-meta">{{
                  row.profile_version_tag || '未绑定算法版本'
                }}</small>
              </template>
            </el-table-column>
            <el-table-column label="执行方式" min-width="125">
              <template #default="{ row }">{{ evaluationModeLabel(row.mode) }}</template>
            </el-table-column>
            <el-table-column
              v-for="spec in metrics"
              :key="spec.key"
              :label="spec.label"
              min-width="108"
            >
              <template #default="{ row }">
                <el-tooltip v-if="spec.description" :content="spec.description" placement="top">
                  <span>{{ formatMetricValue(spec, row.summary?.[spec.key] ?? null) }}</span>
                </el-tooltip>
                <span v-else>{{ formatMetricValue(spec, row.summary?.[spec.key] ?? null) }}</span>
              </template>
            </el-table-column>
            <el-table-column label="门禁" width="90">
              <template #default="{ row }">
                <el-tag :type="row.gate_passed ? 'success' : 'danger'" size="small">
                  {{ row.gate_passed ? '通过' : '阻断' }}
                </el-tag>
              </template>
            </el-table-column>
            <el-table-column label="基线" width="90">
              <template #default="{ row }">
                <el-tag v-if="row.is_baseline" type="warning" size="small">基线</el-tag>
                <el-tag v-else-if="!row.comparable" type="info" size="small">不可比</el-tag>
                <span v-else class="evaluation-meta">—</span>
              </template>
            </el-table-column>
            <el-table-column label="操作" width="230" fixed="right">
              <template #default="{ row }">
                <el-button link type="primary" @click="openRunDetail(row.run_id)"
                  >查看详情</el-button
                >
                <el-button link type="primary" @click="downloadRunReport(row.run_id)"
                  >原始报告</el-button
                >
                <el-button
                  v-if="canWriteEval && row.dataset_id"
                  link
                  type="primary"
                  @click="openBaselineDialog(row)"
                  >设为基线</el-button
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
            <h4>评测基线</h4>
            <div class="evaluation-filters evaluation-filters-inline">
              <el-switch v-model="baselineActiveOnly" active-text="只看 active" />
              <el-button :loading="baselinesBusy" @click="loadBaselines">刷新</el-button>
            </div>
          </div>
          <p class="evaluation-meta">
            基线按「数据集版本 + 口径 + 执行方式」唯一：同一组合只保留一个 active
            基线，被替换的基线保留历史记录；概览页的环比使用 active 基线。
          </p>
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
            <h4>发起真实评测</h4>
            <div v-if="canWriteEval" class="evaluation-filters evaluation-filters-inline">
              <el-button @click="openProfileDialog">登记算法版本</el-button>
              <el-button type="primary" @click="openTriggerDialog()">发起评测</el-button>
            </div>
          </div>
          <p class="evaluation-meta">
            评测由 Worker
            真实执行：登记不可变算法版本，选择数据集、口径与真实执行方式后触发，页面只展示任务号、审计号与当前任务进度；批次、基线与审计留痕请到「测评结果」查看。
          </p>
          <el-alert
            v-if="!canWriteEval"
            type="info"
            show-icon
            :closable="false"
            title="当前账号可查看评测任务进度，触发/取消评测与登记算法版本需要企业负责人或系统管理员权限。"
          />
          <el-alert v-if="jobError" type="error" show-icon :title="jobError" :closable="false" />
          <p v-if="profilesError" class="evaluation-meta">{{ profilesError }}</p>
          <el-empty
            v-if="!activeJob"
            description="当前没有进行中的评测任务，可点击「发起评测」创建。"
            :image-size="60"
          />
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
      v-model="datasetDrawer"
      :title="
        inspectedDataset
          ? `数据集：${inspectedDataset.name}（${inspectedDataset.version}）`
          : '数据集'
      "
      size="72%"
    >
      <template v-if="inspectedDataset">
        <el-alert type="info" :closable="false" show-icon>
          <template #title>
            数据集内容随 Git 版本化：页面对应仓库 evals/datasets/{{ inspectedDataset.version }}/
            下的原始文件，sha256 与登记一致；用例字段完整保留，不做裁剪或改名。
          </template>
        </el-alert>
        <div class="evaluation-filters">
          <el-tag effect="plain" size="small">版本 {{ inspectedDataset.version }}</el-tag>
          <el-tag type="info" effect="plain" size="small">{{
            evaluationSuites[inspectedDataset.suite] || inspectedDataset.suite || '未登记类型'
          }}</el-tag>
          <el-tag v-if="inspectedDataset.archived" type="info" size="small">归档版本</el-tag>
          <span class="evaluation-meta" :title="inspectedDataset.content_sha256">
            内容 sha256
            {{ inspectedDataset.content_sha256 || '—' }}
          </span>
          <span class="evaluation-meta"
            >登记用例数 {{ inspectedDataset.case_count }} · 文件数
            {{ datasetFileList.length }}</span
          >
          <el-button type="primary" :loading="dsRawBusy" @click="downloadDatasetRaw"
            >下载全部原始用例</el-button
          >
          <el-button :loading="datasetCaseBusy" @click="loadDatasetCases">刷新用例</el-button>
        </div>
        <el-alert v-if="dsRawError" type="error" show-icon :title="dsRawError" :closable="false" />
        <el-alert
          v-if="rawExport"
          type="success"
          show-icon
          :closable="false"
          :title="`已导出 ${rawExport.cases} 条原始用例 / ${rawExport.files} 个文件摘要，SHA-256 ${rawExport.sha256}（${rawExport.bytes} 字节）`"
        />
        <h4>文件清单（{{ datasetFileList.length }}）</h4>
        <el-table
          v-if="datasetFileList.length"
          :data="datasetFileList"
          size="small"
          row-key="path"
          max-height="240"
        >
          <el-table-column label="仓库相对路径" min-width="260">
            <template #default="{ row }">
              <code>{{ row.path }}</code>
            </template>
          </el-table-column>
          <el-table-column label="sha256" width="200">
            <template #default="{ row }">
              <span :title="row.sha256">{{ shortSha(row.sha256) }}</span>
            </template>
          </el-table-column>
          <el-table-column label="操作" width="140">
            <template #default="{ row }">
              <el-button link type="primary" @click="viewDatasetFile(row.path)">查看</el-button>
              <el-button link type="primary" @click="downloadDatasetFile(row.path)">下载</el-button>
            </template>
          </el-table-column>
        </el-table>
        <p v-else class="evaluation-muted">该数据集版本没有登记文件清单（历史版本或旧登记）。</p>

        <h4>原始用例（{{ datasetCaseTotal }}）</h4>
        <el-alert
          v-if="datasetCaseError"
          type="error"
          show-icon
          :title="datasetCaseError"
          :closable="false"
        />
        <el-table
          v-else
          v-loading="datasetCaseBusy"
          :data="datasetCases"
          max-height="460"
          empty-text="该数据集版本没有登记用例内容"
        >
          <el-table-column
            v-for="column in datasetCaseColumns"
            :key="column.key"
            :label="column.label"
            :min-width="column.width"
          >
            <template #default="{ row }">
              <span v-if="column.preview">{{ datasetCasePreview(row, column.keys) }}</span>
              <span v-else>{{ datasetCaseText(row, column.keys) }}</span>
            </template>
          </el-table-column>
          <el-table-column label="原始 JSON" width="105" fixed="right">
            <template #default="{ row }">
              <el-button link type="primary" @click="openRawCase(row)">原始 JSON</el-button>
            </template>
          </el-table-column>
        </el-table>
        <nav class="evaluation-pager" aria-label="数据集用例分页">
          <span>第 {{ datasetCaseCursors.length }} 页 / 共 {{ datasetCaseTotal }} 条</span>
          <el-button
            :disabled="datasetCaseBusy || datasetCaseCursors.length === 1"
            @click="turnDatasetCase(false)"
            >上一页</el-button
          >
          <el-button :disabled="datasetCaseBusy || !datasetCaseNext" @click="turnDatasetCase(true)"
            >下一页</el-button
          >
        </nav>
      </template>
    </el-drawer>

    <el-dialog
      v-model="rawCaseDialog"
      :title="rawCaseTitle ? `原始用例 JSON：${rawCaseTitle}` : '原始用例 JSON'"
      width="min(880px, 94vw)"
      :z-index="3000"
    >
      <p class="evaluation-meta">以下为该用例登记在数据集中的完整原始字段，未裁剪、未改名。</p>
      <pre class="evaluation-json">{{ rawCaseJson }}</pre>
    </el-dialog>

    <el-drawer
      v-model="runDetailOpen"
      :title="runDetail ? `批次详情：${runDetail.run.run_id}` : `批次详情：${runDetailId}`"
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
        <div class="evaluation-run-title">
          <strong>{{ traceTime(runDetail.run.created_at) }}</strong>
          <el-tag :type="runDetail.run.gate_passed ? 'success' : 'danger'" size="small">
            {{ runDetail.run.gate_passed ? '门禁通过' : '门禁阻断' }}
          </el-tag>
          <el-tag v-if="runDetail.run.is_baseline" type="warning" size="small">当前基线</el-tag>
          <span
            >{{ evaluationModeLabel(runDetail.run.mode) }} ·
            {{ evaluationScopeLabel(runDetail.run.scope) }}</span
          >
        </div>
        <small class="evaluation-meta"
          >{{ runDetail.run.run_id }} · 数据集版本 {{ runDetail.run.dataset_version || '—' }} ·
          算法版本 {{ runDetail.run.profile_version_tag || '未绑定' }} · 发起人
          {{ runDetail.run.owner_name || runDetail.run.owner_id }}</small
        >
        <div class="evaluation-filters">
          <el-tag effect="plain" size="small"
            >用例 {{ runDetail.run.passed_cases }} / {{ runDetail.run.total_cases }} 通过</el-tag
          >
          <el-tag type="danger" effect="plain" size="small"
            >失败 {{ runDetail.run.failed_cases }}</el-tag
          >
          <el-tag type="info" effect="plain" size="small"
            >不可判定 {{ runDetail.run.unassessable_cases }}</el-tag
          >
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
        </div>
        <p v-if="runDetail.run.metric_schema_version" class="evaluation-meta">
          指标口径 schema v{{ runDetail.run.metric_schema_version }} ·
          {{ runDetailSchema ? runDetailSchema.label : '未定义' }}
        </p>
        <div class="evaluation-metric-grid">
          <div v-for="card in runMetricCards" :key="card.spec.key" class="evaluation-metric-card">
            <div class="evaluation-metric-card-head">
              <span class="evaluation-card-label">{{ card.spec.label }}</span>
              <el-tag effect="plain" size="small">{{ card.spec.group }}</el-tag>
            </div>
            <strong class="evaluation-metric-value">{{
              formatMetricValue(card.spec, card.value)
            }}</strong>
            <small v-if="card.spec.description" class="evaluation-meta">{{
              card.spec.description
            }}</small>
          </div>
        </div>
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
          empty-text="该批次没有用例结果"
        >
          <el-table-column label="用例" min-width="210">
            <template #default="{ row }">
              <button class="evaluation-link" type="button" @click="openCaseJson(row.case_id)">
                {{ row.name }}
              </button>
              <small class="evaluation-meta">{{ row.case_id }}</small>
            </template>
          </el-table-column>
          <el-table-column label="分组" width="110">
            <template #default="{ row }">{{
              evaluationSuites[row.suite] || row.suite || '—'
            }}</template>
          </el-table-column>
          <el-table-column label="得分" width="80">
            <template #default="{ row }">{{
              typeof row.score === 'number' ? row.score.toFixed(2) : '—'
            }}</template>
          </el-table-column>
          <el-table-column label="判定" width="120">
            <template #default="{ row }">
              <el-tag :type="evaluationTags[row.status as EvaluationStatus]" size="small">{{
                evaluationStatuses[row.status as EvaluationStatus]
              }}</el-tag>
              <small v-if="row.hard_failure" class="evaluation-meta">硬性违规</small>
            </template>
          </el-table-column>
          <el-table-column label="原始 JSON" width="110" fixed="right">
            <template #default="{ row }">
              <el-button link type="primary" @click="openCaseJson(row.case_id)"
                >原始 JSON</el-button
              >
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
        原始字段，未裁剪。
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
  type EvaluationDatasetDetail,
  type EvaluationDatasetRaw,
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
const DATASET_CASE_PAGE_SIZE = 20
const RUN_CASE_PAGE_SIZE = 30
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

interface DatasetFileEntry {
  path: string
  sha256: string
}

interface DatasetCaseColumn {
  key: string
  label: string
  keys: string[]
  preview: boolean
  width: number
}

interface RawExportSummary {
  sha256: string
  bytes: number
  cases: number
  files: number
}

const props = defineProps<{
  isAdmin: boolean
  canWriteEval: boolean
  api: ApiClient
  view: 'launch' | 'results'
  kind: EvaluationKind
}>()
const emit = defineEmits<{ (e: 'inspect', taskId: string, traceId: string): void }>()

const evalKind = computed<EvaluationKind>(() => props.kind)
const centerTitle = computed(() => (props.kind === 'agent' ? 'Agent 评测' : 'RAG 评测'))
const centerSubtitle = computed(() => {
  if (props.view === 'launch') {
    return '登记算法版本，选择数据集与评测口径发起真实评测，并跟踪当前任务进度。'
  }
  return `概览指标与趋势、数据集原始内容、批次与基线${props.canWriteEval ? '，以及管理员写权限可见的审计留痕' : ''}。`
})
const activeTab = ref(props.view === 'launch' ? 'launch' : 'overview')
const owner = ref('all')
const scope = ref<EvaluationScope>('full')
const mode = ref<EvaluationMode>('live')
const datasetVersion = ref('')
const refreshing = ref(false)

const catalog = ref<EvaluationMetricCatalog | null>(null)
const catalogError = ref('')

const datasets = ref<EvaluationDataset[]>([])
const datasetsBusy = ref(false)
const datasetsError = ref('')
const showArchived = ref(false)

const trends = ref<EvaluationTrends | null>(null)
const trendsBusy = ref(false)
const trendsError = ref('')

const runDatasetId = ref('')

const baselines = ref<EvaluationBaseline[]>([])
const baselinesBusy = ref(false)
const baselinesError = ref('')
const baselineActiveOnly = ref(false)

const profiles = ref<EvaluationProfile[]>([])
const profilesError = ref('')

const auditAction = ref<EvaluationAuditAction | ''>('')
const auditLogs = ref<EvaluationAuditLog[]>([])
const auditCursors = ref<string[]>([''])
const auditNext = ref<string | null>(null)
const auditBusy = ref(false)
const auditError = ref('')

const datasetDrawer = ref(false)
const inspectedDataset = ref<EvaluationDataset | null>(null)
const datasetCases = ref<Record<string, unknown>[]>([])
const datasetCaseCursors = ref<string[]>([''])
const datasetCaseNext = ref<string | null>(null)
const datasetCaseTotal = ref(0)
const datasetCaseBusy = ref(false)
const datasetCaseError = ref('')
const rawCaseDialog = ref(false)
const rawCaseJson = ref('')
const rawCaseTitle = ref('')
const dsRawBusy = ref(false)
const dsRawError = ref('')
const rawExport = ref<RawExportSummary | null>(null)

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

const activeJob = ref<EvaluationJob | null>(null)
const jobError = ref('')
const jobBusy = ref(false)

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

let trendRequest = 0
let datasetRequest = 0
let baselineRequest = 0
let profileRequest = 0
let runDetailRequest = 0
let runCaseRequest = 0
let jobTimer: number | null = null
let polledJobId = ''

const kindDatasets = computed(() =>
  datasets.value.filter((item) => item.eval_kind === evalKind.value),
)
const activeDatasets = computed(() => kindDatasets.value.filter((item) => !item.archived))
const visibleDatasets = computed(() =>
  showArchived.value ? kindDatasets.value : activeDatasets.value,
)
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
const schemaSummaryText = computed(() => {
  if (!schema.value) return '指标目录尚未加载，暂无可渲染的指标口径'
  return `指标口径 ${schema.value.label} · schema v${schema.value.version} · 共 ${schema.value.metrics.length} 项指标`
})

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
const baselineHint = computed(() => {
  if (!trends.value) return ''
  if (!trends.value.baseline)
    return '当前数据集版本与口径没有 active 基线，概览仅展示最新批次数值。'
  if (!baselineTrend.value) return 'active 基线批次不在最近批次列表内，无法按指标逐项对比。'
  if (!latestTrend.value) return '当前口径还没有批次数据。'
  if (!latestTrend.value.comparable) return '最新批次被标记为不可比，不给出环比结论。'
  if (latestTrend.value.run_id === baselineTrend.value.run_id) {
    return '最新批次就是当前 active 基线，环比值为 0。'
  }
  return `与 active 基线 ${baselineTrend.value.run_id} 对比（${baselineTrend.value.algorithm_version || '未绑定算法版本'}）。`
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
const baselineMetricLabel = computed(() => {
  const spec = aggregateMetric.value
  return spec && (spec.key === 'task_completion' || spec.key === 'pass_rate')
    ? spec.label
    : '通过率'
})

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
const datasetFileList = computed<DatasetFileEntry[]>(() =>
  datasetFileEntries(inspectedDataset.value),
)
const datasetCaseColumns = computed<DatasetCaseColumn[]>(() => {
  if (inspectedDataset.value?.eval_kind === 'rag') {
    return [
      { key: 'case_id', label: '用例 ID', keys: ['case_id', 'id'], preview: false, width: 150 },
      { key: 'category', label: '分类', keys: ['category'], preview: false, width: 110 },
      {
        key: 'question',
        label: '问题',
        keys: ['question', 'query', 'prompt'],
        preview: true,
        width: 260,
      },
      {
        key: 'expected_documents',
        label: '预期文档 / 依据',
        keys: ['expected_documents', 'expected', 'reference_answer', 'expected_facts'],
        preview: true,
        width: 300,
      },
    ]
  }
  return [
    { key: 'id', label: '用例 ID', keys: ['id', 'case_id'], preview: false, width: 150 },
    { key: 'name', label: '名称', keys: ['name', 'title'], preview: false, width: 140 },
    { key: 'category', label: '分类', keys: ['category'], preview: false, width: 110 },
    {
      key: 'prompt',
      label: 'Prompt / 输入',
      keys: ['prompt', 'input', 'question'],
      preview: true,
      width: 320,
    },
    {
      key: 'required_tools',
      label: '必调工具',
      keys: ['required_tools', 'expected_tools', 'expected'],
      preview: true,
      width: 200,
    },
  ]
})
const runDetailSchema = computed<EvaluationMetricSchema | null>(
  () => runDetail.value?.metric_schema ?? schema.value,
)
const runMetricCards = computed<RunMetricCard[]>(() =>
  (runDetailSchema.value?.metrics ?? []).map((spec) => ({
    spec,
    value: runDetail.value?.run.summary?.[spec.key] ?? null,
  })),
)

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
 * 把数据集 metadata.files 归一为路径与 sha256 列表。
 * @param dataset - 数据集条目。
 * @returns 文件清单。
 */
function datasetFileEntries(dataset: EvaluationDataset | null): DatasetFileEntry[] {
  const files = dataset?.metadata?.files
  if (!files || typeof files !== 'object' || Array.isArray(files)) return []
  const entries: DatasetFileEntry[] = []
  for (const [path, sha] of Object.entries(files as Record<string, unknown>)) {
    if (typeof sha === 'string') entries.push({ path, sha256: sha })
  }
  return entries.sort((a, b) => a.path.localeCompare(b.path))
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
 * 构造数据集原始文件下载地址（二进制附件，不经过 JSON API 客户端）。
 * @param datasetId - 数据集编号。
 * @param path - 仓库相对路径。
 * @returns 同源下载地址。
 */
function datasetFileUrl(datasetId: string, path: string): string {
  return (
    '/api/evaluations/datasets/' +
    encodeURIComponent(datasetId) +
    '/files?path=' +
    encodeURIComponent(path)
  )
}

/**
 * 在新窗口打开数据集原始文件（服务端以附件响应，浏览器按文件类型处理）。
 * @param path - 仓库相对路径。
 */
function viewDatasetFile(path: string): void {
  const dataset = inspectedDataset.value
  if (!dataset) return
  window.open(datasetFileUrl(dataset.dataset_id, path), '_blank', 'noopener')
}

/**
 * 以 Blob 方式下载数据集原始文件，失败时给出可见提示。
 * @param path - 仓库相对路径。
 */
async function downloadDatasetFile(path: string): Promise<void> {
  const dataset = inspectedDataset.value
  if (!dataset) return
  try {
    const response = await fetch(datasetFileUrl(dataset.dataset_id, path))
    if (!response.ok) throw new Error('读取数据集文件失败：文件未登记或内容摘要不一致')
    saveBlob(await response.blob(), path.split('/').at(-1) || 'dataset-file')
  } catch (cause) {
    ElMessage.error(cause instanceof Error ? cause.message : '下载数据集文件失败')
  }
}

/**
 * 导出数据集全部原始用例与文件摘要为 JSON 文件，并展示其 SHA-256 便于核对。
 */
async function downloadDatasetRaw(): Promise<void> {
  const dataset = inspectedDataset.value
  if (!dataset) return
  dsRawBusy.value = true
  dsRawError.value = ''
  try {
    const raw = await props.api<EvaluationDatasetRaw>(
      '/evaluations/datasets/' + encodeURIComponent(dataset.dataset_id) + '/raw',
    )
    const payload = JSON.stringify(raw, null, 2)
    const blob = new Blob([payload], { type: 'application/json' })
    const bytes = new TextEncoder().encode(payload)
    rawExport.value = {
      sha256: await sha256Hex(bytes),
      bytes: bytes.byteLength,
      cases: raw.cases.length,
      files: Object.keys(raw.files).length,
    }
    saveBlob(blob, dataset.dataset_id + '-raw.json')
  } catch (cause) {
    dsRawError.value = cause instanceof Error ? cause.message : '导出原始用例失败'
  } finally {
    dsRawBusy.value = false
  }
}

/**
 * 计算字节的 SHA-256 十六进制摘要。
 * @param payload - 待计算字节。
 * @returns 十六进制摘要；运行环境不支持时返回空串。
 */
async function sha256Hex(payload: Uint8Array): Promise<string> {
  const cryptoApi = globalThis.crypto
  if (!cryptoApi?.subtle) return ''
  const buffer = new ArrayBuffer(payload.byteLength)
  new Uint8Array(buffer).set(payload)
  const digest = await cryptoApi.subtle.digest('SHA-256', buffer)
  return Array.from(new Uint8Array(digest))
    .map((byte) => byte.toString(16).padStart(2, '0'))
    .join('')
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
 * 查看数据集版本的文件清单、原始用例与逐步分页内容。
 * @param dataset - 数据集条目。
 */
async function inspectDataset(dataset: EvaluationDataset): Promise<void> {
  inspectedDataset.value = dataset
  datasetCases.value = []
  datasetCaseCursors.value = ['']
  datasetCaseNext.value = null
  datasetCaseTotal.value = 0
  datasetCaseError.value = ''
  rawExport.value = null
  dsRawError.value = ''
  datasetDrawer.value = true
  await loadDatasetCases()
}

/**
 * 按游标读取数据集原始用例页。
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
      '/evaluations/datasets/' + encodeURIComponent(dataset.dataset_id) + '?' + query,
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
 * 按游标前后翻动数据集用例页。
 * @param forward - 是否读取下一页。
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
 * @param item - 数据集中登记的用例对象。
 * @param keys - 候选字段名。
 * @returns 可读文本或缺失占位。
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
 * @param item - 数据集中登记的用例对象。
 * @param keys - 候选字段名。
 * @returns 截断后的摘要文本。
 */
function datasetCasePreview(item: Record<string, unknown>, keys: string[]): string {
  const text = datasetCaseText(item, keys)
  return text.length > 160 ? text.slice(0, 160) + '…' : text
}

/**
 * 打开当前用例的完整原始 JSON（不裁剪、不改名）。
 * @param item - 数据集中登记的原始用例对象。
 */
function openRawCase(item: Record<string, unknown>): void {
  rawCaseJson.value = JSON.stringify(item, null, 2)
  rawCaseTitle.value = datasetCaseText(item, ['id', 'case_id'])
  rawCaseDialog.value = true
}

/**
 * 从数据集页跳转到批次页并按该数据集筛选。
 * @param dataset - 数据集条目。
 */
function filterRunsByDataset(dataset: EvaluationDataset): void {
  runDatasetId.value = dataset.dataset_id
  activeTab.value = 'runs'
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
 * 打开用例详情并展示 expected / observed / verdicts 的原始 JSON。
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
    await Promise.all([loadDatasets(), loadTrends(), refreshRuns()])
  } catch (cause) {
    writeError.value = cause instanceof Error ? cause.message : '发起评测失败'
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
onMounted(async () => {
  await loadCatalog()
  await loadDatasets()
  ensureSelections()
  if (props.view === 'results') await loadTrends()
  else await loadProfiles()
})
onBeforeUnmount(stopJobPolling)
</script>
