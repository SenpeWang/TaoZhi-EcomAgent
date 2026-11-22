<template>
  <div class="bi-shell">
    <header class="bi-toolbar">
      <div class="bi-tabs" role="tablist" aria-label="经营分析视图切换">
        <button
          :class="{ active: tab === 'board' }"
          type="button"
          role="tab"
          :aria-selected="tab === 'board'"
          @click="switchTab('board')"
        >
          <svg class="bi-tab-svg" viewBox="0 0 24 24" aria-hidden="true">
            <path
              fill="currentColor"
              d="M4 20h16v2H4v-2zm2-7h3v5H6v-5zm5-5h3v10h-3V8zm5-4h3v14h-3V4z"
            />
          </svg>
          经营总览看板
        </button>
        <button
          :class="{ active: tab === 'chat' }"
          type="button"
          role="tab"
          :aria-selected="tab === 'chat'"
          @click="switchTab('chat')"
        >
          <svg class="bi-tab-svg" viewBox="0 0 24 24" aria-hidden="true">
            <path fill="currentColor" d="M4 4h16v11H7l-3 3V4zm3 4v2h10V8H7zm0 4v2h7v-2H7z" />
          </svg>
          指标智能问数
        </button>
      </div>

      <div class="bi-toolbar-meta">
        <div v-if="tab === 'board'" class="bi-period-group" role="group" aria-label="统计周期">
          <button
            v-for="opt in periodOptions"
            :key="opt.days"
            type="button"
            class="period-btn"
            :class="{ active: periodDays === opt.days }"
            @click="changePeriod(opt.days)"
          >
            {{ opt.label }}
          </button>
        </div>
        <span v-if="tab === 'board' && latestOrderLabel" class="bi-meta-pill">
          数据截至 {{ latestOrderLabel }}
        </span>
        <span class="bi-meta-pill tone-gov">数据权限分级受控</span>
        <el-button v-if="tab === 'board'" size="small" :loading="boardLoading" @click="loadBoard">
          刷新数据
        </el-button>
      </div>
    </header>

    <div v-show="tab === 'board'" v-loading="boardLoading" class="bi-board">
      <template v-if="board">
        <section class="kpi-grid" aria-label="核心经营指标">
          <div v-for="c in kpiCards" :key="c.label" class="kpi-card" :class="c.tone">
            <div class="kpi-head">
              <span class="kpi-label">{{ c.label }}</span>
              <span class="kpi-badge">{{ c.badge }}</span>
            </div>
            <div class="kpi-value">
              {{ c.value }}<small v-if="c.unit">{{ c.unit }}</small>
            </div>
            <div class="kpi-sub">{{ c.sub }}</div>
          </div>
        </section>

        <div v-if="!hasOrderData" class="panel empty-period-card">
          <strong>当前统计周期（近 {{ board.days || periodDays }} 日）暂无经营订单流水</strong>
          <p v-if="latestOrderLabel">
            系统内最近一笔订单时间为 {{ latestOrderLabel }}，可切换更长统计周期或同步最新交易数据。
          </p>
          <p v-else>当前企业账套尚未录入销售订单流水。</p>
        </div>

        <section v-if="board.low_stock === null && board.gross_profit === null" class="gov-banner">
          <span class="gov-dot" />
          <span>
            销售毛利与分仓安全库存预警属于企业二级机密（L2）经营指标，当前账号仅展示已授权范围内的业务数据。
          </span>
        </section>

        <section class="chart-grid" aria-label="经营趋势与渠道分析">
          <article class="panel chart-panel wide">
            <div class="panel-head">
              <div>
                <h3>成交额日趋势分析</h3>
                <p class="panel-sub">统计周期：近 {{ board.days || periodDays }} 日 · 单位：元</p>
              </div>
            </div>
            <div ref="trendRef" class="chart-box tall" />
            <el-empty
              v-if="!hasRows(board.trend)"
              description="当前周期暂无日趋势数据"
              :image-size="60"
            />
          </article>

          <article class="panel chart-panel">
            <div class="panel-head">
              <div>
                <h3>全渠道成交贡献分布</h3>
                <p class="panel-sub">近 {{ board.days || periodDays }} 日有效成交额 · 单位：元</p>
              </div>
            </div>
            <div ref="channelRef" class="chart-box" />
            <el-empty
              v-if="!hasChannelData"
              description="当前周期暂无渠道成交数据"
              :image-size="60"
            />
          </article>

          <article class="panel chart-panel">
            <div class="panel-head">
              <div>
                <h3>各渠道售后退货率监控</h3>
                <p class="panel-sub">虚线为全渠道均值 · 单位：%</p>
              </div>
            </div>
            <div ref="refundRef" class="chart-box" />
            <el-empty
              v-if="!hasRows(board.refund_rate)"
              description="当前周期暂无退货率数据"
              :image-size="60"
            />
          </article>

          <article class="panel chart-panel wide">
            <div class="panel-head">
              <div>
                <h3>售后工单类型与原因归因</h3>
                <p class="panel-sub">
                  近 {{ board.days || periodDays }} 日按售后原因与处理方式分布 · 单位：单
                </p>
              </div>
            </div>
            <div ref="afterRef" class="chart-box" />
            <el-empty
              v-if="!hasRows(board.aftersales)"
              description="当前周期暂无售后工单记录"
              :image-size="60"
            />
          </article>
        </section>

        <section
          v-if="categoryRows.length || lowStockRows.length"
          class="detail-grid"
          aria-label="经营结构明细"
        >
          <article v-if="categoryRows.length" class="panel chart-panel">
            <div class="panel-head">
              <div>
                <h3>品类经营贡献明细</h3>
                <p class="panel-sub">近 {{ board.days || periodDays }} 日全渠道各商品品类表现</p>
              </div>
            </div>
            <el-table :data="categoryRows" size="small" class="enterprise-table">
              <el-table-column prop="category" label="商品品类" min-width="100" />
              <el-table-column label="成交金额" min-width="120" align="right">
                <template #default="{ row }">
                  <span class="num-strong">¥{{ fmtExactMoney(row.gsv) }}</span>
                </template>
              </el-table-column>
              <el-table-column label="营收占比" min-width="110" align="right">
                <template #default="{ row }">
                  <span class="share-pill">{{ row.sharePct }}%</span>
                </template>
              </el-table-column>
              <el-table-column
                v-if="hasProfitSummary"
                label="销售毛利额"
                min-width="120"
                align="right"
              >
                <template #default="{ row }">
                  <span class="num-strong">
                    {{ row.grossProfit !== null ? '¥' + fmtExactMoney(row.grossProfit) : '—' }}
                  </span>
                </template>
              </el-table-column>
              <el-table-column v-if="hasProfitSummary" label="毛利率" min-width="90" align="right">
                <template #default="{ row }">
                  <span>{{ row.marginPct !== null ? row.marginPct + '%' : '—' }}</span>
                </template>
              </el-table-column>
            </el-table>
          </article>

          <article v-if="lowStockRows.length" class="panel chart-panel">
            <div class="panel-head">
              <div>
                <h3>分仓安全库存预警</h3>
                <p class="panel-sub">低于安全库存阈值 SKU 汇总 · 二级机密 (L2)</p>
              </div>
            </div>
            <el-table :data="lowStockRows" size="small" class="enterprise-table">
              <el-table-column prop="warehouse" label="仓储中心" min-width="120" />
              <el-table-column label="预警 SKU 数" min-width="110" align="right">
                <template #default="{ row }">
                  <span class="num-strong">{{ row.count }} 个</span>
                </template>
              </el-table-column>
              <el-table-column label="补货优先级" min-width="110" align="right">
                <template #default="{ row }">
                  <el-tag :type="row.count >= 2 ? 'danger' : 'warning'" size="small" effect="light">
                    {{ row.count >= 2 ? '高优先级补货' : '建议及时补货' }}
                  </el-tag>
                </template>
              </el-table-column>
            </el-table>
          </article>
        </section>
      </template>
      <el-empty v-else-if="!boardLoading" description="经营看板数据暂不可用" />
    </div>

    <div v-show="tab === 'chat'" class="chat">
      <div ref="chatList" class="chat-list">
        <div v-if="!messages.length" class="chat-welcome">
          <span class="welcome-eyebrow">企业数据语义层 · 指标智能问数</span>
          <h2>经营指标自然语言检索</h2>
          <p>按账号所属组织范围与数据密级实时查询企业经营报表，自动生成明细表与可视化图表。</p>
          <div class="quick-chips">
            <button v-for="q in quickQuestions" :key="q" type="button" @click="ask(q)">
              {{ q }}
            </button>
          </div>
        </div>
        <template v-for="(m, i) in messages" :key="i">
          <div v-if="m.role === 'user'" class="msg msg-user">
            <div class="bubble">{{ m.text }}</div>
          </div>
          <div v-else class="msg msg-system">
            <div class="bubble wide">
              <template v-if="m.pending">
                <div class="pending">
                  <el-icon class="is-loading"><Loading /></el-icon>
                  <span>{{ m.stage || '正在匹配企业经营指标' }}</span>
                  <el-progress
                    v-if="typeof m.progress === 'number'"
                    :percentage="m.progress"
                    :stroke-width="6"
                    style="max-width: 260px"
                  />
                </div>
                <p v-if="m.text" class="plain-answer">{{ m.text }}</p>
              </template>
              <template v-else-if="m.denied">
                <div class="denied-card">
                  <span class="denied-glyph">限</span>
                  <div>
                    <strong>当前账号暂未获得该指标访问权限</strong>
                    <p>{{ m.text }}</p>
                  </div>
                </div>
              </template>
              <template v-else-if="m.data">
                <div class="answer-head">
                  <span class="answer-tag">指标口径 · {{ m.data.metric_name }}</span>
                  <span class="answer-meta">
                    返回 {{ m.data.meta.row_count }} 条记录 · 耗时 {{ m.data.meta.elapsed_ms }}ms ·
                    数据密级 L{{ m.data.meta.level }}
                  </span>
                </div>
                <el-table
                  :data="tableRows(m.data)"
                  size="small"
                  max-height="300"
                  class="answer-table"
                >
                  <el-table-column
                    v-for="(c, ci) in m.data.columns"
                    :key="c"
                    :prop="String(ci)"
                    :label="columnLabel(c, m.data.unit)"
                    :formatter="cellFormatter"
                  />
                </el-table>
                <AnswerChart v-if="m.data.rows.length >= 2" :data="m.data" />
                <p class="answer-foot">口径说明：{{ m.data.description }}</p>
              </template>
              <template v-else>
                <p class="plain-answer">{{ m.text }}</p>
              </template>
            </div>
          </div>
        </template>
      </div>
      <div class="chat-input">
        <el-input
          v-model="draft"
          placeholder="输入经营分析问题，例如：近30天退货率最高的渠道是哪个？"
          size="large"
          clearable
          @keyup.enter="ask(draft)"
        />
        <el-button type="primary" size="large" :loading="asking" @click="ask(draft)">
          发起查询
        </el-button>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, nextTick, onMounted, onUnmounted, reactive, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { Loading } from '@element-plus/icons-vue'
import * as echarts from 'echarts/core'
import { BarChart, LineChart } from 'echarts/charts'
import {
  GridComponent,
  LegendComponent,
  MarkLineComponent,
  TooltipComponent,
} from 'echarts/components'
import { CanvasRenderer } from 'echarts/renderers'
import AnswerChart from './AnswerChart.vue'
import { chartAxisStyle, chartColors } from './chartTheme'
import { openTaskEventStream, pollTaskUntilTerminal } from '../../shared/taskStream'
import type { ApiClient, CreatedResource } from '../../shared/apiTypes'

echarts.use([
  BarChart,
  LineChart,
  GridComponent,
  TooltipComponent,
  LegendComponent,
  MarkLineComponent,
  CanvasRenderer,
])

type MetricBlock = {
  columns: string[]
  rows: unknown[][]
  denied?: boolean
  name: string
  unit: string
  level: number
  description: string
  meta: { row_count: number; elapsed_ms: number; level: number; metric_key: string }
}
type CategorySummaryItem = {
  category: string
  gsv: number
  gross_profit: number | null
}
type BoardData = {
  days?: number
  latest_order_at?: string | null
  generated_at?: string
  cards: {
    gsv_30d: number | null
    orders_30d: number | null
    gross_profit_30d: number | null
    low_stock: number | null
    aftersales_count?: number | null
    worst_refund_channel?: { channel: string; rate: number }
    avg_refund_rate?: number
  }
  trend: MetricBlock | null
  channel_gsv: Record<string, number> | null
  category_summary?: CategorySummaryItem[] | null
  refund_rate: MetricBlock | null
  aftersales: MetricBlock | null
  low_stock: MetricBlock | null
  gross_profit: MetricBlock | null
}
type Message = {
  role: 'user' | 'system'
  text?: string
  pending?: boolean
  denied?: boolean
  stage?: string
  progress?: number
  data?: MetricBlock & { metric_name: string }
}
const props = defineProps<{ user: { id: string; display_name: string }; api: ApiClient }>()

const COLUMN_LABEL_MAP: Record<string, string> = {
  channel_name: '销售渠道',
  category: '商品品类',
  status: '订单状态',
  type: '售后类型',
  reason: '售后原因',
  warehouse: '仓储中心',
  day: '统计日期',
}

const VALUE_LABEL_MAP: Record<string, string> = {
  refund: '仅退款',
  return: '退货退款',
  exchange: '换新',
  pending: '待付款',
  paid: '已付款',
  shipped: '已发货',
  delivered: '已签收',
  completed: '已完成',
  cancelled: '已取消',
  refunded: '已退款',
  resolved: '已结案',
  approved: '已审核',
  rejected: '已驳回',
}

const periodOptions = [
  { days: 7, label: '近 7 日' },
  { days: 30, label: '近 30 日' },
  { days: 90, label: '近 90 日' },
]

const tab = ref<'board' | 'chat'>('board')
const periodDays = ref(30)
const board = ref<BoardData | null>(null)
const boardLoading = ref(false)
const messages = ref<Message[]>([])
const draft = ref('')
const asking = ref(false)
const chatList = ref<HTMLElement | null>(null)
const trendRef = ref<HTMLElement | null>(null)
const channelRef = ref<HTMLElement | null>(null)
const refundRef = ref<HTMLElement | null>(null)
const afterRef = ref<HTMLElement | null>(null)
const quickQuestions = [
  '近30天退货率最高的渠道是哪个？',
  '天猫近30天成交金额是多少？',
  '近14天每日成交趋势',
  '近30天毛利情况',
  '各仓库存预警情况',
]
let charts: echarts.ECharts[] = []
let closeStream: (() => void) | null = null
const viewLifetime = new AbortController()
let boardRequest = 0

const activeDays = computed(() => board.value?.days || periodDays.value)

const latestOrderLabel = computed(() => {
  const raw = board.value?.latest_order_at
  if (!raw) return ''
  const dt = new Date(raw)
  if (Number.isNaN(dt.getTime())) return ''
  return dt.toLocaleString('zh-CN', {
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
  })
})

const hasOrderData = computed(() => {
  const c = board.value?.cards
  return Boolean(
    c && ((c.gsv_30d !== null && c.gsv_30d > 0) || (c.orders_30d !== null && c.orders_30d > 0)),
  )
})

const hasChannelData = computed(() =>
  Boolean(board.value?.channel_gsv && Object.keys(board.value.channel_gsv).length > 0),
)

const hasProfitSummary = computed(() =>
  Boolean(board.value?.category_summary?.some((item) => item.gross_profit !== null)),
)

const categoryRows = computed(() => {
  const list = board.value?.category_summary || []
  if (!list.length) return []
  const totalGsv = list.reduce((sum, item) => sum + item.gsv, 0)
  return list.map((item) => ({
    category: item.category,
    gsv: item.gsv,
    sharePct: totalGsv > 0 ? ((item.gsv / totalGsv) * 100).toFixed(1) : '0.0',
    grossProfit: item.gross_profit,
    marginPct:
      item.gross_profit !== null && item.gsv > 0
        ? ((item.gross_profit / item.gsv) * 100).toFixed(1)
        : null,
  }))
})

const lowStockRows = computed(() => {
  const rows = board.value?.low_stock?.rows || []
  return rows.map((row) => ({
    warehouse: String(row[0] ?? '未命名仓库'),
    count: Number(row[1] ?? 0),
  }))
})

const kpiCards = computed(() => {
  const c = board.value?.cards
  if (!c) return []
  const spanLabel = `近 ${activeDays.value} 日`
  const cards: {
    label: string
    badge: string
    value: string
    unit: string
    sub: string
    tone?: string
  }[] = []
  cards.push({
    label: '成交总额 (GMV)',
    badge: spanLabel,
    value: fmtMoney(c.gsv_30d),
    unit: '元',
    sub: '有效订单累计（已剔除取消单）',
    tone: 'blue',
  })
  cards.push({
    label: '订单总量',
    badge: spanLabel,
    value: fmtNum(c.orders_30d),
    unit: '单',
    sub: '全渠道订单笔数统计',
    tone: 'cyan',
  })
  if (c.worst_refund_channel)
    cards.push({
      label: '渠道退货率峰值',
      badge: spanLabel,
      value: `${c.worst_refund_channel.rate}%`,
      unit: '',
      sub: `${c.worst_refund_channel.channel} · 全渠道均值 ${c.avg_refund_rate}%`,
      tone: 'red',
    })
  if (c.gross_profit_30d !== null && c.gross_profit_30d !== undefined) {
    const marginText =
      c.gsv_30d && c.gsv_30d > 0
        ? `综合毛利率 ${((c.gross_profit_30d / c.gsv_30d) * 100).toFixed(1)}% · L2`
        : '成交额 − 商品成本 · L2'
    cards.push({
      label: '销售毛利额',
      badge: spanLabel,
      value: fmtMoney(c.gross_profit_30d),
      unit: '元',
      sub: marginText,
      tone: 'green',
    })
  }
  if (c.aftersales_count !== null && c.aftersales_count !== undefined)
    cards.push({
      label: '售后工单总量',
      badge: spanLabel,
      value: fmtNum(c.aftersales_count),
      unit: '单',
      sub: '仅退款 · 退货退款 · 换新',
      tone: 'slate',
    })
  if (c.low_stock !== null && c.low_stock !== undefined)
    cards.push({
      label: '库存预警 SKU',
      badge: '实时库存',
      value: fmtNum(c.low_stock),
      unit: '个',
      sub: '低于安全库存阈值 · L2',
      tone: 'amber',
    })
  return cards
})

/**
 * 判断指标数据块是否包含有效数据行。
 * @param block 指标数据块，允许为空
 * @returns 至少存在一行数据时返回 true
 */
function hasRows(block: MetricBlock | null | undefined): boolean {
  return Boolean(block && block.rows && block.rows.length > 0)
}

/**
 * 金额格式化：万级换算为"万"单位，缺失显示占位符。
 * @param v 金额数值（元），允许为空
 * @returns 人类可读的金额字符串
 */
function fmtMoney(v: number | null | undefined): string {
  if (v === null || v === undefined) return '—'
  return v >= 10000
    ? (v / 10000).toFixed(2) + ' 万'
    : v.toLocaleString('zh-CN', { minimumFractionDigits: 0, maximumFractionDigits: 2 })
}

/**
 * 表格明细金额格式化：保留两位小数且带千分位。
 * @param v 金额数值（元）
 * @returns 带千分位的精确金额字符串
 */
function fmtExactMoney(v: number): string {
  return v.toLocaleString('zh-CN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })
}

/**
 * 整数格式化：千分位显示，缺失显示占位符。
 * @param v 数值，允许为空
 * @returns 人类可读的整数字符串
 */
function fmtNum(v: number | null | undefined): string {
  return v === null || v === undefined ? '—' : Math.round(v).toLocaleString('zh-CN')
}

/**
 * 映射指标原始字段名为企业中文列标题。
 * @param col 后端返回的列名
 * @param unit 指标计量单位
 * @returns 企业级中文列标题
 */
function columnLabel(col: string, unit: string): string {
  if (col === 'value') return unit ? `指标数值（${unit}）` : '指标数值'
  return COLUMN_LABEL_MAP[col] || col
}

/**
 * 切换统计周期并重新加载看板指标。
 * @param days 统计周期天数（7 / 30 / 90）
 * @returns 异步加载完成后无返回值
 */
async function changePeriod(days: number): Promise<void> {
  if (periodDays.value === days && board.value) return
  periodDays.value = days
  await loadBoard()
}

/**
 * 切换看板/问数页签；回到看板时重渲染图表。
 * @param t - 目标页签：board 看板或 chat 问数。
 * @returns 切换完成后无返回值。
 */
function switchTab(t: 'board' | 'chat'): void {
  tab.value = t
  if (t === 'board') nextTick(() => renderCharts())
}

/**
 * 拉取经营看板数据；失败时提示中文错误信息但不阻断页面。
 * @returns 异步完成后无返回值
 */
async function loadBoard(): Promise<void> {
  const request = ++boardRequest
  boardLoading.value = true
  try {
    const snapshot = await props.api<BoardData>(`/bi/dashboard?days=${periodDays.value}`)
    if (request !== boardRequest || viewLifetime.signal.aborted) return
    board.value = snapshot
    await nextTick()
    renderCharts()
  } catch (e: unknown) {
    if (request !== boardRequest || viewLifetime.signal.aborted) return
    board.value = null
    const msg =
      e instanceof Error && /[\u4e00-\u9fff]/.test(e.message)
        ? e.message
        : '经营看板加载失败，请稍后重试'
    ElMessage.error(msg)
  } finally {
    if (request === boardRequest) boardLoading.value = false
  }
}

/**
 * 表格行转对象（列下标字符串作 prop）。
 * @param d 指标数据块，rows 为二维数组
 * @returns 以列下标字符串为键的行对象数组
 */
function tableRows(d: MetricBlock): Record<string, unknown>[] {
  return d.rows.map((r) => Object.fromEntries(r.map((v, i) => [String(i), v])))
}

/**
 * 表格单元格格式化：翻译状态与类型枚举，万级数值换算为"万"单位。
 * @param _row 当前行数据（保留 el-table 签名）
 * @param _col 当前列定义（保留 el-table 签名）
 * @param cellValue 单元格原始值
 * @returns 面向企业用户的展示字符串
 */
function cellFormatter(_row: Record<string, unknown>, _col: unknown, cellValue: unknown): string {
  if (typeof cellValue === 'number') {
    if (Math.abs(cellValue) >= 10000) return (cellValue / 10000).toFixed(2) + ' 万'
    return cellValue.toLocaleString('zh-CN')
  }
  if (cellValue === null || cellValue === undefined) return '—'
  const text = String(cellValue)
  return VALUE_LABEL_MAP[text] || text
}

/**
 * 趋势图悬浮提示：千分位展示成交额。
 * @param v 成交额数值（元）
 * @returns 千分位字符串（含"元"）
 */
function trendTooltipValue(v: number): string {
  return v?.toLocaleString('zh-CN') + ' 元'
}

/**
 * 渠道销售额悬浮提示：千分位并附加单位。
 * @param v 数值（元）
 * @returns 千分位字符串（含"元"）
 */
function channelTooltipValue(v: number): string {
  return v?.toLocaleString('zh-CN') + ' 元'
}

/**
 * 渠道条形图数值标签：千分位展示。
 * @param p 数据项包装对象
 * @param p.value 数值
 * @returns 千分位字符串
 */
function channelBarLabel(p: { value: number }): string {
  return p.value.toLocaleString('zh-CN')
}

/**
 * 退货率悬浮提示：附加百分号。
 * @param v 百分比数值
 * @returns 含"%"的字符串
 */
function refundTooltipValue(v: number): string {
  return v + '%'
}

/**
 * 渲染经营看板四组图表；对应数据集为空时跳过初始化。
 * @returns 图表绘制完成后无返回值。
 */
function renderCharts(): void {
  charts.forEach((c) => c.dispose())
  charts = []
  const metrics = board.value
  if (!metrics) return
  if (hasRows(metrics.trend) && metrics.trend && trendRef.value) {
    const chart = echarts.init(trendRef.value)
    chart.setOption({
      tooltip: {
        trigger: 'axis',
        valueFormatter: trendTooltipValue,
      },
      grid: { left: 64, right: 20, top: 20, bottom: 30 },
      xAxis: {
        type: 'category',
        data: metrics.trend.rows.map((r) => String(r[0]).slice(5)),
        ...chartAxisStyle,
      },
      yAxis: { type: 'value', ...chartAxisStyle },
      series: [
        {
          name: '日成交额',
          type: 'line',
          data: metrics.trend.rows.map((r) => r[1]),
          smooth: true,
          symbol: 'circle',
          symbolSize: 5,
          lineStyle: { width: 2.5, color: chartColors.primary },
          itemStyle: { color: chartColors.primary },
          areaStyle: {
            color: {
              type: 'linear',
              x: 0,
              y: 0,
              x2: 0,
              y2: 1,
              colorStops: [
                { offset: 0, color: 'rgba(37, 99, 235, 0.20)' },
                { offset: 1, color: 'rgba(37, 99, 235, 0.01)' },
              ],
            },
          },
        },
      ],
    })
    charts.push(chart)
  }
  if (metrics.channel_gsv && Object.keys(metrics.channel_gsv).length && channelRef.value) {
    const entries = Object.entries(metrics.channel_gsv).reverse()
    const chart = echarts.init(channelRef.value)
    chart.setOption({
      tooltip: {
        trigger: 'axis',
        valueFormatter: channelTooltipValue,
      },
      grid: { left: 84, right: 64, top: 14, bottom: 26 },
      xAxis: { type: 'value', ...chartAxisStyle },
      yAxis: {
        type: 'category',
        data: entries.map((e) => e[0]),
        ...chartAxisStyle,
        splitLine: { show: false },
      },
      series: [
        {
          name: '成交额',
          type: 'bar',
          data: entries.map((e) => e[1]),
          barWidth: 16,
          itemStyle: { borderRadius: [0, 6, 6, 0], color: chartColors.secondary },
          label: {
            show: true,
            position: 'right',
            color: '#475569',
            fontSize: 12,
            formatter: channelBarLabel,
          },
        },
      ],
    })
    charts.push(chart)
  }
  if (hasRows(metrics.refund_rate) && metrics.refund_rate && refundRef.value) {
    const rows = metrics.refund_rate.rows
    const averageRefundRate = rows.reduce((s, r) => s + Number(r[1]), 0) / Math.max(1, rows.length)
    const chart = echarts.init(refundRef.value)
    chart.setOption({
      tooltip: {
        trigger: 'axis',
        valueFormatter: refundTooltipValue,
      },
      grid: { left: 48, right: 20, top: 20, bottom: 36 },
      xAxis: {
        type: 'category',
        data: rows.map((r) => String(r[0])),
        ...chartAxisStyle,
        axisLabel: { ...chartAxisStyle.axisLabel, rotate: 20 },
      },
      yAxis: { type: 'value', ...chartAxisStyle },
      series: [
        {
          name: '退货率',
          type: 'bar',
          data: rows.map((r) => Number(r[1])),
          barWidth: 22,
          itemStyle: {
            borderRadius: [6, 6, 0, 0],
            /**
             * 按全渠道均值分档着色：高于均值红色预警，接近均值琥珀色，其余绿色稳态。
             * @param p 数据项包装对象
             * @param p.value 退货率百分比数值
             * @returns 对应预警档位色值
             */
            color: (p: { value: number }) =>
              p.value >= averageRefundRate
                ? chartColors.danger
                : p.value >= averageRefundRate * 0.7
                  ? chartColors.warning
                  : chartColors.success,
          },
          markLine: {
            symbol: 'none',
            lineStyle: { type: 'dashed', color: '#94a3b8' },
            label: { formatter: '均值 {c}%', color: chartColors.label },
            data: [{ yAxis: Number(averageRefundRate.toFixed(2)) }],
          },
        },
      ],
    })
    charts.push(chart)
  }
  if (hasRows(metrics.aftersales) && metrics.aftersales && afterRef.value) {
    const aftersalesColors: Record<string, string> = {
      refund: chartColors.danger,
      return: chartColors.warning,
      exchange: chartColors.primary,
    }
    const aftersalesLabels: Record<string, string> = {
      refund: '仅退款',
      return: '退货退款',
      exchange: '换新',
    }
    const rows = metrics.aftersales.rows
    const reasons = [...new Set(rows.map((r) => String(r[1])))].slice(0, 10)
    const countMap = new Map<string, number>()
    for (const r of rows) {
      const key = `${String(r[0])}::${String(r[1])}`
      countMap.set(key, (countMap.get(key) || 0) + Number(r[2]))
    }
    const chart = echarts.init(afterRef.value)
    chart.setOption({
      tooltip: { trigger: 'axis' },
      legend: { top: 0, textStyle: { color: chartColors.label } },
      grid: { left: 90, right: 24, top: 34, bottom: 26 },
      xAxis: {
        type: 'value',
        ...chartAxisStyle,
        splitLine: { lineStyle: { color: chartColors.grid } },
      },
      yAxis: {
        type: 'category',
        data: reasons,
        ...chartAxisStyle,
        splitLine: { show: false },
      },
      series: ['refund', 'return', 'exchange'].map((t) => ({
        name: aftersalesLabels[t],
        type: 'bar',
        stack: 'total',
        barWidth: 16,
        itemStyle: { color: aftersalesColors[t] },
        data: reasons.map((reason) => countMap.get(`${t}::${reason}`) || 0),
      })),
    })
    charts.push(chart)
  }
}

/**
 * 问数查询：提交任务并订阅事件流；终态后拉取结果渲染明细表与图表。
 * @param q 用户输入的问题，为空时回退输入框草稿
 * @returns 异步完成后无返回值
 */
async function ask(q: string): Promise<void> {
  const question = (q || draft.value || '').trim()
  if (!question) return void ElMessage.warning('请输入您想分析的经营问题')
  if (asking.value) return
  draft.value = ''
  asking.value = true
  messages.value.push({ role: 'user', text: question })
  const pending = reactive<Message>({
    role: 'system',
    pending: true,
    stage: '正在匹配企业经营指标',
  })
  messages.value.push(pending)
  await scrollBottom()
  try {
    const r = await props.api<CreatedResource>('/tasks', 'POST', {
      question,
      input_level: 1,
    })
    if (viewLifetime.signal.aborted) return
    followTask(r.id, pending)
  } catch (e: unknown) {
    asking.value = false
    messages.value.pop()
    const msg =
      e instanceof Error && /[\u4e00-\u9fff]/.test(e.message) ? e.message : '提交失败，请稍后重试'
    messages.value.push({ role: 'system', text: msg })
  }
  await scrollBottom()
}

/**
 * 打开问数或问答任务的流式订阅。
 * @param taskId - 当前问数任务编号。
 * @param pending - 界面中等待完成的消息。
 */
function followTask(taskId: string, pending: Message): void {
  closeStream?.()
  closeStream = openTaskEventStream(taskId, {
    /**
     * 接收进度事件：更新待处理消息的阶段与进度。
     * @param row - 服务端推送的任务进度条目。
     */
    onMessage: (row) => {
      if (row.stage) pending.stage = row.stage
      if (typeof row.progress === 'number') pending.progress = row.progress
    },
    /**
     * 展示问答智能体已核验的结论。
     * @param text - 此次执行的实时正文。
     */
    onResult: (text) => {
      pending.text = text
    },
    /**
     * 监听终态事件：清理句柄并拉取结果。
     */
    onDone: () => {
      closeStream = null
      void finish(taskId, pending)
    },
    /**
     * SSE 断连兜底：清空句柄并转入轮询拉取终态。
     */
    onError: () => {
      closeStream = null
      void finish(taskId, pending)
    },
  })
}

/**
 * 拉取任务终态结果并转为回答消息；SSE 提前断开时以轮询兜底（最多 60s）。
 * @param taskId 任务 ID
 * @param pending 聊天区待处理消息，完成后原地替换为结果
 * @returns 异步完成后无返回值
 */
async function finish(taskId: string, pending: Message): Promise<void> {
  try {
    type BiTaskResult = {
      state: string
      result?: { answer?: string; data?: MetricBlock & { metric_name: string } }
    }
    const job = await pollTaskUntilTerminal<BiTaskResult>({
      /**
       * 单次查询问数任务状态与结果。
       * @returns 服务端最新任务对象。
       */
      fetchTask: () => props.api<BiTaskResult>('/tasks/' + taskId),
      signal: viewLifetime.signal,
      terminalStates: ['completed', 'review', 'failed', 'cancelled', 'rejected'],
      maxAttempts: 30,
      intervalMs: 2000,
    })
    if (viewLifetime.signal.aborted) return
    if (!job) throw new Error('查询连接已中断')
    if (job.state === 'running' || job.state === 'queued') {
      followTask(taskId, pending)
      return
    }
    const res = job.result
    if (res?.data && !res.data.denied && res.data.columns?.length) {
      messages.value.splice(messages.value.indexOf(pending), 1, {
        role: 'system',
        data: res.data,
      })
      return
    } else if (res?.answer && /无权查看|受限指标/.test(res.answer)) {
      messages.value.splice(messages.value.indexOf(pending), 1, {
        role: 'system',
        denied: true,
        text: res.answer.split('\n')[0],
      })
    } else {
      messages.value.splice(messages.value.indexOf(pending), 1, {
        role: 'system',
        text: res?.answer || '暂未匹配到相关指标数据，请换个问法，或前往知识文档检索资料。',
      })
    }
  } catch (e: unknown) {
    const raw = e instanceof Error ? e.message : String(e)
    const detail =
      raw && /[\u4e00-\u9fff]/.test(raw)
        ? raw
        : '结果获取异常：' + (raw || '未知错误').slice(0, 120)
    messages.value.splice(messages.value.indexOf(pending), 1, {
      role: 'system',
      text: detail + '，请稍后重试。',
    })
  } finally {
    if (!viewLifetime.signal.aborted && !closeStream) asking.value = false
    await scrollBottom()
  }
}

/**
 * 聊天列表滚动到底部（在下一渲染帧执行）。
 * @returns 异步滚动完成后无返回值
 */
async function scrollBottom(): Promise<void> {
  await nextTick()
  if (chatList.value) chatList.value.scrollTop = chatList.value.scrollHeight
}

/**
 * 窗口尺寸变化时重算全部图表尺寸。
 * @returns 图表重绘完成后无返回值。
 */
function onResize(): void {
  charts.forEach((c) => c.resize())
}

onMounted(async () => {
  await loadBoard()
  if (!viewLifetime.signal.aborted) window.addEventListener('resize', onResize)
})

onUnmounted(() => {
  viewLifetime.abort()
  window.removeEventListener('resize', onResize)
  closeStream?.()
  charts.forEach((c) => c.dispose())
  charts = []
})
</script>

<style scoped>
.bi-shell {
  display: flex;
  flex-direction: column;
  gap: 16px;
  height: 100%;
}

.bi-toolbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  flex-wrap: wrap;
  gap: 12px;
  padding: 12px 16px;
  background: var(--tz-surface, #ffffff);
  border: 1px solid var(--tz-border, #e2e8f0);
  border-radius: 12px;
  box-shadow: var(--tz-shadow-sm, 0 1px 2px rgba(15, 23, 42, 0.04));
}

.bi-tabs {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  padding: 3px;
  background: var(--tz-surface-muted, #f8fafc);
  border: 1px solid var(--tz-border, #e2e8f0);
  border-radius: 9px;
}

.bi-tabs button {
  display: inline-flex;
  align-items: center;
  gap: 7px;
  padding: 7px 14px;
  border: none;
  border-radius: 7px;
  background: transparent;
  color: var(--tz-ink-secondary, #475569);
  font-size: 13px;
  font-weight: 500;
  cursor: pointer;
  transition: all 0.15s;
}

.bi-tabs button:hover {
  color: var(--tz-ink, #0f172a);
}

.bi-tabs button.active {
  background: var(--tz-primary, #2563eb);
  color: #ffffff;
  font-weight: 600;
  box-shadow: 0 2px 8px rgba(37, 99, 235, 0.22);
}

.bi-tab-svg {
  width: 15px;
  height: 15px;
  flex-shrink: 0;
}

.bi-toolbar-meta {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 8px;
}

.bi-period-group {
  display: inline-flex;
  align-items: center;
  padding: 2px;
  background: var(--tz-surface-muted, #f8fafc);
  border: 1px solid var(--tz-border, #e2e8f0);
  border-radius: 8px;
}

.period-btn {
  padding: 5px 10px;
  border: none;
  border-radius: 6px;
  background: transparent;
  color: var(--tz-ink-muted, #64748b);
  font-size: 12px;
  font-weight: 500;
  cursor: pointer;
  transition: all 0.15s;
}

.period-btn:hover {
  color: var(--tz-ink, #0f172a);
}

.period-btn.active {
  background: #ffffff;
  color: var(--tz-primary, #2563eb);
  font-weight: 600;
  box-shadow: 0 1px 2px rgba(15, 23, 42, 0.08);
}

.bi-meta-pill {
  display: inline-flex;
  align-items: center;
  padding: 4px 10px;
  border-radius: 6px;
  background: var(--tz-surface-muted, #f8fafc);
  border: 1px solid var(--tz-border-soft, #eef2f6);
  color: var(--tz-ink-muted, #64748b);
  font-size: 12px;
}

.bi-meta-pill.tone-gov {
  background: var(--tz-primary-soft, #eff6ff);
  border-color: #dbeafe;
  color: var(--tz-primary, #2563eb);
  font-weight: 500;
}

.bi-board {
  display: flex;
  flex-direction: column;
  gap: 14px;
}

.kpi-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
  gap: 12px;
}

.kpi-card {
  position: relative;
  display: flex;
  flex-direction: column;
  gap: 8px;
  padding: 16px 18px;
  background: var(--tz-surface, #ffffff);
  border: 1px solid var(--tz-border, #e2e8f0);
  border-radius: 12px;
  box-shadow: var(--tz-shadow-sm, 0 1px 2px rgba(15, 23, 42, 0.04));
  overflow: hidden;
}

.kpi-card::before {
  content: '';
  position: absolute;
  inset: 0 auto 0 0;
  width: 3px;
  background: var(--tone, #2563eb);
}

.kpi-card.blue {
  --tone: #2563eb;
}

.kpi-card.cyan {
  --tone: #0891b2;
}

.kpi-card.green {
  --tone: #059669;
}

.kpi-card.red {
  --tone: #dc2626;
}

.kpi-card.amber {
  --tone: #d97706;
}

.kpi-card.slate {
  --tone: #475569;
}

.kpi-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
}

.kpi-label {
  color: var(--tz-ink-secondary, #475569);
  font-size: 13px;
  font-weight: 600;
}

.kpi-badge {
  padding: 2px 7px;
  border-radius: 4px;
  background: var(--tz-surface-muted, #f8fafc);
  color: var(--tz-ink-subtle, #94a3b8);
  font-size: 11px;
  font-weight: 500;
}

.kpi-value {
  font-size: 26px;
  font-weight: 700;
  color: var(--tz-ink, #0f172a);
  letter-spacing: -0.4px;
  font-variant-numeric: tabular-nums;
}

.kpi-value small {
  margin-left: 4px;
  font-size: 13px;
  font-weight: 500;
  color: var(--tz-ink-muted, #64748b);
}

.kpi-sub {
  color: var(--tz-ink-muted, #64748b);
  font-size: 12px;
}

.empty-period-card {
  padding: 14px 18px;
  border: 1px solid #fde68a;
  background: #fffbeb;
  border-radius: 10px;
}

.empty-period-card strong {
  display: block;
  color: #92400e;
  font-size: 14px;
  margin-bottom: 4px;
}

.empty-period-card p {
  margin: 0;
  color: #b45309;
  font-size: 13px;
}

.gov-banner {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 10px 14px;
  background: var(--tz-surface, #ffffff);
  border: 1px solid var(--tz-border, #e2e8f0);
  border-radius: 10px;
  color: var(--tz-ink-muted, #64748b);
  font-size: 12.5px;
}

.gov-dot {
  width: 7px;
  height: 7px;
  border-radius: 999px;
  background: var(--tz-primary, #2563eb);
  flex-shrink: 0;
}

.chart-grid,
.detail-grid {
  display: grid;
  grid-template-columns: repeat(2, 1fr);
  gap: 12px;
}

.chart-panel {
  background: var(--tz-surface, #ffffff);
  border: 1px solid var(--tz-border, #e2e8f0);
  border-radius: 12px;
  padding: 16px 18px;
  box-shadow: var(--tz-shadow-sm, 0 1px 2px rgba(15, 23, 42, 0.04));
}

.chart-panel.wide {
  grid-column: 1 / -1;
}

.panel-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 12px;
  margin-bottom: 10px;
}

.panel-head h3 {
  margin: 0 0 3px;
  font-size: 15px;
  font-weight: 600;
  color: var(--tz-ink, #0f172a);
}

.panel-sub {
  margin: 0;
  font-size: 12px;
  color: var(--tz-ink-subtle, #94a3b8);
}

.chart-box {
  height: 240px;
}

.chart-box.tall {
  height: 280px;
}

.enterprise-table {
  width: 100%;
}

.num-strong {
  font-weight: 600;
  color: var(--tz-ink, #0f172a);
  font-variant-numeric: tabular-nums;
}

.share-pill {
  display: inline-block;
  padding: 1px 8px;
  border-radius: 999px;
  background: var(--tz-surface-muted, #f1f5f9);
  color: var(--tz-ink-secondary, #475569);
  font-size: 12px;
  font-variant-numeric: tabular-nums;
}

.chat {
  display: flex;
  flex-direction: column;
  gap: 12px;
  height: calc(100vh - 220px);
  min-height: 440px;
}

.chat-list {
  flex: 1;
  overflow-y: auto;
  display: flex;
  flex-direction: column;
  gap: 14px;
  padding: 8px 4px;
}

.chat-welcome {
  margin: auto;
  text-align: center;
  max-width: 520px;
  padding: 24px;
}

.welcome-eyebrow {
  display: inline-block;
  margin-bottom: 10px;
  padding: 4px 10px;
  border-radius: 999px;
  background: var(--tz-primary-soft, #eff6ff);
  color: var(--tz-primary, #2563eb);
  font-size: 12px;
  font-weight: 600;
}

.chat-welcome h2 {
  margin: 0 0 8px;
  font-size: 22px;
  color: var(--tz-ink, #0f172a);
}

.chat-welcome p {
  color: var(--tz-ink-muted, #64748b);
  font-size: 13px;
  line-height: 1.7;
}

.quick-chips {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  justify-content: center;
  margin-top: 18px;
}

.quick-chips button {
  padding: 7px 14px;
  border: 1px solid var(--tz-border, #dbe4ef);
  border-radius: 999px;
  background: #ffffff;
  color: var(--tz-ink-secondary, #334155);
  font-size: 13px;
  cursor: pointer;
  transition: all 0.15s;
}

.quick-chips button:hover {
  border-color: var(--tz-primary, #2563eb);
  color: var(--tz-primary, #2563eb);
  background: var(--tz-primary-soft, #eff6ff);
}

.msg {
  display: flex;
}

.msg-user {
  justify-content: flex-end;
}

.msg-user .bubble {
  background: var(--tz-primary, #2563eb);
  color: #ffffff;
  border-radius: 14px 14px 4px 14px;
  padding: 10px 16px;
  max-width: 70%;
  font-size: 14px;
  line-height: 1.6;
}

.msg-system {
  justify-content: flex-start;
}

.msg-system .bubble {
  background: #ffffff;
  border: 1px solid var(--tz-border, #e2e8f0);
  border-radius: 4px 14px 14px 14px;
  padding: 16px 18px;
  max-width: 88%;
  box-shadow: var(--tz-shadow-sm, 0 1px 3px rgba(15, 23, 42, 0.05));
}

.pending {
  display: flex;
  align-items: center;
  gap: 10px;
  color: var(--tz-primary, #2563eb);
  font-size: 13px;
}

.answer-head {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 10px;
  margin-bottom: 12px;
}

.answer-tag {
  background: var(--tz-primary-soft, #eff6ff);
  color: var(--tz-primary, #2563eb);
  border-radius: 6px;
  padding: 3px 10px;
  font-size: 12px;
  font-weight: 600;
}

.answer-meta {
  color: var(--tz-ink-subtle, #94a3b8);
  font-size: 12px;
}

.answer-table {
  width: 100%;
}

.answer-chart {
  height: 230px;
  margin-top: 12px;
}

.answer-foot {
  margin: 12px 0 0;
  color: var(--tz-ink-muted, #64748b);
  font-size: 12px;
  line-height: 1.6;
  border-top: 1px dashed var(--tz-border, #e2e8f0);
  padding-top: 8px;
}

.plain-answer {
  margin: 0;
  color: var(--tz-ink-secondary, #334155);
  font-size: 14px;
  line-height: 1.7;
  white-space: pre-wrap;
}

.denied-card {
  display: flex;
  gap: 12px;
  align-items: flex-start;
}

.denied-glyph {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 34px;
  height: 34px;
  border-radius: 9px;
  background: var(--tz-danger-soft, #fef2f2);
  color: var(--tz-danger, #dc2626);
  font-weight: 700;
  flex: none;
}

.denied-card strong {
  color: var(--tz-ink, #0f172a);
  font-size: 14px;
}

.denied-card p {
  margin: 4px 0 0;
  color: var(--tz-ink-muted, #64748b);
  font-size: 13px;
  line-height: 1.6;
}

.chat-input {
  display: flex;
  gap: 10px;
}

@media (max-width: 1080px) {
  .chart-grid,
  .detail-grid {
    grid-template-columns: 1fr;
  }
}
</style>
