<template>
  <div class="bi-shell">
    <div class="bi-tabs">
      <button :class="{ active: tab === 'board' }" @click="switchTab('board')">
        <span class="tab-glyph">板</span>经营看板
      </button>
      <button :class="{ active: tab === 'chat' }" @click="switchTab('chat')">
        <span class="tab-glyph">数</span>智能问数
      </button>
      <span class="bi-tab-note">受控只读查询</span>
    </div>

    <!-- ── 经营看板 ─────────────────────────────────────────────── -->
    <div v-show="tab === 'board'" v-loading="boardLoading" class="board">
      <template v-if="board">
        <div class="kpi-grid">
          <div v-for="k in kpiCards" :key="k.label" class="kpi-card" :class="k.tone">
            <span class="kpi-label">{{ k.label }}</span>
            <strong class="kpi-value"
              >{{ k.value }}<small>{{ k.unit }}</small></strong
            >
            <span class="kpi-sub">{{ k.sub }}</span>
          </div>
        </div>
        <el-alert
          v-if="!board.cards.gross_profit_30d"
          type="info"
          :closable="false"
          show-icon
          class="bi-note"
          title="毛利、采购成本、库存预警为二级及以上机密指标，当前身份不可见。"
        />
        <div class="chart-grid">
          <section class="panel chart-panel wide">
            <h3>日销售趋势<small>近 30 天 · 单位：元</small></h3>
            <div ref="trendRef" class="chart-box tall" />
            <el-empty v-if="!board.trend" description="暂无趋势数据" :image-size="60" />
          </section>
          <section class="panel chart-panel">
            <h3>渠道销售额分布<small>近 30 天 · 单位：元</small></h3>
            <div ref="channelRef" class="chart-box" />
            <el-empty v-if="!board.channel_gsv" description="暂无数据" :image-size="60" />
          </section>
          <section class="panel chart-panel">
            <h3>渠道退货率对比<small>近 30 天 · 虚线为均值</small></h3>
            <div ref="refundRef" class="chart-box" />
            <el-empty v-if="!board.refund_rate" description="暂无数据" :image-size="60" />
          </section>
          <section class="panel chart-panel wide">
            <h3>售后原因分布<small>近 30 天 · 按工单类型着色</small></h3>
            <div ref="afterRef" class="chart-box" />
            <el-empty v-if="!board.aftersales" description="暂无数据" :image-size="60" />
          </section>
        </div>
      </template>
      <el-empty v-else-if="!boardLoading" description="看板数据暂不可用" />
    </div>

    <!-- ── 智能问数 ─────────────────────────────────────────────── -->
    <div v-show="tab === 'chat'" class="chat">
      <div ref="chatList" class="chat-list">
        <div v-if="!messages.length" class="chat-welcome">
          <h2>问点数据</h2>
          <p>用自然语言查询授权范围内的经营指标。</p>
          <div class="quick-chips">
            <button v-for="q in quickQuestions" :key="q" @click="ask(q)">{{ q }}</button>
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
                  <span>{{ m.stage || '指标匹配中' }}</span>
                  <el-progress
                    v-if="typeof m.progress === 'number'"
                    :percentage="m.progress"
                    :stroke-width="6"
                    style="max-width: 260px"
                  />
                </div>
              </template>
              <template v-else-if="m.denied">
                <div class="denied-card">
                  <span class="denied-glyph">密</span>
                  <div>
                    <strong>该指标需要更高权限</strong>
                    <p>{{ m.text }}</p>
                  </div>
                </div>
              </template>
              <template v-else-if="m.data">
                <div class="answer-head">
                  <span class="answer-tag">指标 · {{ m.data.metric_name }}</span>
                  <span class="answer-meta"
                    >{{ m.data.meta.row_count }} 行 · {{ m.data.meta.elapsed_ms }}ms · 密级 L{{
                      m.data.meta.level
                    }}</span
                  >
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
                    :label="c"
                    :formatter="cellFormatter"
                  />
                </el-table>
                <AnswerChart v-if="m.data.rows.length >= 2" :data="m.data" />
                <p class="answer-foot">{{ m.data.description }}</p>
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
          placeholder="例如：近30天退货率最高的渠道是哪个？"
          size="large"
          clearable
          @keyup.enter="ask(draft)"
        />
        <el-button type="primary" size="large" :loading="asking" @click="ask(draft)"
          >查询</el-button
        >
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, nextTick, onMounted, onUnmounted, ref } from 'vue'
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
type BoardData = {
  cards: {
    gsv_30d: number | null
    orders_30d: number | null
    gross_profit_30d: number | null
    low_stock: number | null
    worst_refund_channel?: { channel: string; rate: number }
    avg_refund_rate?: number
  }
  trend: MetricBlock | null
  channel_gsv: Record<string, number> | null
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
type ApiFn = <T = Record<string, unknown>>(
  path: string,
  method?: string,
  payload?: unknown,
) => Promise<T>

const props = defineProps<{ user: { id: string; display_name: string }; api: ApiFn }>()

const tab = ref<'board' | 'chat'>('board')
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
  '天猫近30天卖了多少？',
  '近14天日销售趋势',
  '近30天毛利情况',
  '库存预警情况',
]
let charts: echarts.ECharts[] = []
let stream: EventSource | null = null

/** 当前登录者是否老板级（毛利与库存预警卡由后端按权限返回，缺失即不渲染）。 */
const kpiCards = computed(() => {
  const c = board.value?.cards
  if (!c) return []
  const cards: { label: string; value: string; unit: string; sub: string; tone?: string }[] = []
  cards.push({
    label: '销售额（近30天）',
    value: fmtMoney(c.gsv_30d),
    unit: '元',
    sub: '不含取消订单',
    tone: 'blue',
  })
  cards.push({
    label: '订单量（近30天）',
    value: fmtNum(c.orders_30d),
    unit: '单',
    sub: '含全部订单状态',
    tone: 'cyan',
  })
  if (c.worst_refund_channel)
    cards.push({
      label: '最高退货率渠道',
      value: `${c.worst_refund_channel.rate}%`,
      unit: '',
      sub: `${c.worst_refund_channel.channel} · 均值 ${c.avg_refund_rate}%`,
      tone: 'red',
    })
  if (c.gross_profit_30d !== null && c.gross_profit_30d !== undefined)
    cards.push({
      label: '毛利（近30天）',
      value: fmtMoney(c.gross_profit_30d),
      unit: '元',
      sub: '销售额 − 成本 · L2',
      tone: 'green',
    })
  if (c.low_stock !== null && c.low_stock !== undefined)
    cards.push({
      label: '低库存 SKU',
      value: fmtNum(c.low_stock),
      unit: '个',
      sub: '低于安全线 · L2',
      tone: 'amber',
    })
  return cards
})
/**
 * 金额格式化：万级换算为"万"单位，缺失显示占位符。
 * @param v 金额数值（元），允许为空
 * @returns 人类可读的金额字符串
 */
function fmtMoney(v: number | null | undefined): string {
  if (v === null || v === undefined) return '—'
  return v >= 10000 ? (v / 10000).toFixed(2) + ' 万' : v.toLocaleString('zh-CN')
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
 * 切换看板/问数页签；回到看板时重渲染图表。
 * @param t 目标页签：board 看板或 chat 问数
 */
function switchTab(t: 'board' | 'chat') {
  tab.value = t
  if (t === 'board') nextTick(() => renderCharts())
}
/** 拉取看板数据；失败给中文提示但不打断页面。 */
async function loadBoard() {
  boardLoading.value = true
  try {
    board.value = await props.api<BoardData>('/bi/dashboard')
    await nextTick()
    renderCharts()
  } catch (e: unknown) {
    board.value = null
    const msg =
      e instanceof Error && /[\u4e00-\u9fff]/.test(e.message)
        ? e.message
        : '看板加载失败，请稍后重试'
    ElMessage.error(msg)
  } finally {
    boardLoading.value = false
  }
}
/**
 * 表格行转对象（列名做 prop）。
 * @param d 指标数据块，rows 为二维数组
 * @returns 以列下标字符串为键的行对象数组
 */
function tableRows(d: MetricBlock): Record<string, unknown>[] {
  return d.rows.map((r) => Object.fromEntries(r.map((v, i) => [String(i), v])))
}
/**
 * 表格单元格格式化：万级数值换算为"万"单位，缺失显示占位符。
 * @param row 当前行数据（未使用，保留 el-table 签名）
 * @param _col 当前列定义（未使用，保留 el-table 签名）
 * @param cellValue 单元格原始值
 * @returns 人类可读的单元格字符串
 */
function cellFormatter(row: Record<string, unknown>, _col: unknown, cellValue: unknown): string {
  if (typeof cellValue === 'number' && Math.abs(cellValue) >= 10000)
    return (cellValue / 10000).toFixed(2) + ' 万'
  return cellValue === null || cellValue === undefined ? '—' : String(cellValue)
}
/**
 * 趋势图悬浮提示：千分位展示数值。
 * @param v 数值，允许为空
 * @returns 千分位字符串
 */
function trendTooltipValue(v: number): string {
  return v?.toLocaleString('zh-CN')
}
/**
 * 渠道销售额悬浮提示：千分位并附加单位。
 * @param v 数值（元），允许为空
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
  return v + ' %'
}
/** 渲染看板四图；数据缺失时跳过。 */
function renderCharts() {
  charts.forEach((c) => c.dispose())
  charts = []
  const b = board.value
  if (!b) return
  const axis = {
    axisLabel: { color: '#64748b' },
    axisLine: { lineStyle: { color: '#e2e8f0' } },
    splitLine: { lineStyle: { color: '#f1f5f9' } },
  }
  if (b.trend && trendRef.value) {
    const ch = echarts.init(trendRef.value)
    ch.setOption({
      tooltip: {
        trigger: 'axis',
        valueFormatter: trendTooltipValue,
      },
      grid: { left: 60, right: 20, top: 24, bottom: 32 },
      xAxis: { type: 'category', data: b.trend.rows.map((r) => String(r[0]).slice(5)), ...axis },
      yAxis: { type: 'value', ...axis },
      series: [
        {
          type: 'line',
          data: b.trend.rows.map((r) => r[1]),
          smooth: true,
          symbol: 'circle',
          symbolSize: 5,
          lineStyle: { width: 2.5, color: '#2563eb' },
          areaStyle: {
            color: {
              type: 'linear',
              x: 0,
              y: 0,
              x2: 0,
              y2: 1,
              colorStops: [
                { offset: 0, color: 'rgba(37,99,235,.22)' },
                { offset: 1, color: 'rgba(37,99,235,.02)' },
              ],
            },
          },
        },
      ],
    })
    charts.push(ch)
  }
  if (b.channel_gsv && channelRef.value) {
    const entries = Object.entries(b.channel_gsv).reverse()
    const ch = echarts.init(channelRef.value)
    ch.setOption({
      tooltip: {
        trigger: 'axis',
        valueFormatter: channelTooltipValue,
      },
      grid: { left: 76, right: 30, top: 16, bottom: 28 },
      xAxis: { type: 'value', ...axis },
      yAxis: {
        type: 'category',
        data: entries.map((e) => e[0]),
        ...axis,
        splitLine: { show: false },
      },
      series: [
        {
          type: 'bar',
          data: entries.map((e) => e[1]),
          barWidth: 16,
          itemStyle: { borderRadius: [0, 6, 6, 0], color: '#0891b2' },
          label: {
            show: true,
            position: 'right',
            color: '#475569',
            formatter: channelBarLabel,
          },
        },
      ],
    })
    charts.push(ch)
  }
  if (b.refund_rate && refundRef.value) {
    const rows = b.refund_rate.rows
    const avg = rows.reduce((s, r) => s + Number(r[1]), 0) / Math.max(1, rows.length)
    const ch = echarts.init(refundRef.value)
    ch.setOption({
      tooltip: {
        trigger: 'axis',
        valueFormatter: refundTooltipValue,
      },
      grid: { left: 44, right: 16, top: 20, bottom: 32 },
      xAxis: {
        type: 'category',
        data: rows.map((r) => String(r[0])),
        ...axis,
        axisLabel: { ...axis.axisLabel, rotate: 24 },
      },
      yAxis: { type: 'value', ...axis },
      series: [
        {
          type: 'bar',
          data: rows.map((r) => Number(r[1])),
          barWidth: 22,
          itemStyle: {
            borderRadius: [6, 6, 0, 0],
            /**
             * 按均值分档着色：高于均值红色，接近均值黄色，其余绿色。
             * @param p 数据项包装对象
             * @param p.value 退货率数值
             * @returns 对应档位的颜色值
             */
            color: (p: { value: number }) =>
              p.value >= avg ? '#dc2626' : p.value >= avg * 0.7 ? '#f59e0b' : '#10b981',
          },
          markLine: {
            symbol: 'none',
            lineStyle: { type: 'dashed', color: '#94a3b8' },
            label: { formatter: '均值 {c}%', color: '#64748b' },
            data: [{ yAxis: Number(avg.toFixed(2)) }],
          },
        },
      ],
    })
    charts.push(ch)
  }
  if (b.aftersales && afterRef.value) {
    const typeColor: Record<string, string> = {
      refund: '#dc2626',
      return: '#d97706',
      exchange: '#2563eb',
    }
    const typeName: Record<string, string> = {
      refund: '仅退款',
      return: '退货退款',
      exchange: '换新',
    }
    const rows = [...b.aftersales.rows].sort((a, b2) => Number(b2[2]) - Number(a[2])).slice(0, 10)
    const ch = echarts.init(afterRef.value)
    ch.setOption({
      tooltip: { trigger: 'axis' },
      legend: { top: 0, textStyle: { color: '#64748b' } },
      grid: { left: 90, right: 30, top: 34, bottom: 28 },
      xAxis: { type: 'value', ...axis, splitLine: { lineStyle: { color: '#f1f5f9' } } },
      yAxis: {
        type: 'category',
        data: rows.map((r) => String(r[1])),
        ...axis,
        splitLine: { show: false },
      },
      series: ['refund', 'return', 'exchange'].map((t) => ({
        name: typeName[t],
        type: 'bar',
        stack: 'total',
        barWidth: 16,
        itemStyle: { color: typeColor[t] },
        data: rows.map((r) => (String(r[0]) === t ? Number(r[2]) : 0)),
      })),
    })
    charts.push(ch)
  }
}
/**
 * 问数：提交任务并订阅事件流；终态后拉取结果渲染表格与图表。
 * @param q 用户输入的问题，为空时回退输入框草稿
 * @returns 异步完成后无返回值
 */
async function ask(q: string) {
  const question = (q || draft.value || '').trim()
  if (!question) return void ElMessage.warning('请输入你想查询的问题')
  if (asking.value) return
  draft.value = ''
  asking.value = true
  messages.value.push({ role: 'user', text: question })
  const pending: Message = { role: 'system', pending: true, stage: '指标匹配中' }
  messages.value.push(pending)
  await scrollBottom()
  try {
    const r = await props.api<{ id: string }>('/tasks', 'POST', {
      question,
      input_level: 1,
    })
    stream?.close()
    stream = new EventSource('/api/tasks/' + r.id + '/events')
    /**
     * 接收进度事件：更新待处理消息的阶段与进度。
     * @param ev SSE 消息事件，data 为 JSON 进度行
     */
    stream.onmessage = (ev) => {
      try {
        const row = JSON.parse(ev.data) as { stage?: string; progress?: number }
        if (row.stage) pending.stage = row.stage
        if (typeof row.progress === 'number') pending.progress = row.progress
      } catch {}
    }
    stream.addEventListener('done', () => {
      stream?.close()
      stream = null
      void finish(r.id, pending)
    })
    /**
     * SSE 断连兜底：关闭连接并转入轮询拉取终态。
     */
    stream.onerror = () => {
      stream?.close()
      stream = null
      void finish(r.id, pending)
    }
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
 * 拉取任务终态结果并转为消息；SSE 提前断开时以轮询兜底（最多 60s）。
 * @param taskId 任务 ID
 * @param pending 聊天区待处理消息，完成后原地替换为结果
 * @returns 异步完成后无返回值
 */
async function finish(taskId: string, pending: Message) {
  try {
    let job: {
      state: string
      result?: { answer?: string; data?: MetricBlock & { metric_name: string } }
    } | null = null
    for (let tries = 0; tries < 30; tries++) {
      job = await props.api<{
        state: string
        result?: { answer?: string; data?: MetricBlock & { metric_name: string } }
      }>('/tasks/' + taskId)
      if (['completed', 'review', 'failed', 'cancelled'].includes(job.state)) break
      await new Promise((r) => setTimeout(r, 2000))
    }
    const res = job?.result
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
        text: res?.answer || '这个问题暂时没有可用的指标数据，请换个问法，或到知识库检索相关资料。',
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
    asking.value = false
    await scrollBottom()
  }
}
/**
 * 聊天列表滚动到底部（在下一渲染帧执行）。
 */
async function scrollBottom() {
  await nextTick()
  if (chatList.value) chatList.value.scrollTop = chatList.value.scrollHeight
}
/**
 * 窗口尺寸变化时重算全部图表尺寸。
 */
function onResize() {
  charts.forEach((c) => c.resize())
}
onMounted(async () => {
  await loadBoard()
  window.addEventListener('resize', onResize)
})
onUnmounted(() => {
  window.removeEventListener('resize', onResize)
  stream?.close()
  charts.forEach((c) => c.dispose())
  charts = []
})
</script>

<style scoped>
.bi-shell {
  display: flex;
  flex-direction: column;
  gap: 14px;
  height: 100%;
}
.bi-tabs {
  display: flex;
  align-items: center;
  gap: 8px;
}
.bi-tabs button {
  display: inline-flex;
  align-items: center;
  gap: 7px;
  padding: 9px 18px;
  border: 1px solid var(--line, #e2e8f0);
  border-radius: 10px;
  background: #fff;
  color: #334155;
  font-size: 14px;
  cursor: pointer;
  transition: all 0.15s;
}
.bi-tabs button:hover {
  border-color: #2563eb;
  color: #2563eb;
}
.bi-tabs button.active {
  background: #2563eb;
  border-color: #2563eb;
  color: #fff;
  box-shadow: 0 4px 14px rgba(37, 99, 235, 0.28);
}
.tab-glyph {
  font-size: 12px;
  opacity: 0.75;
}
.bi-tab-note {
  margin-left: auto;
  color: #94a3b8;
  font-size: 12px;
}
/* 看板 */
.kpi-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(190px, 1fr));
  gap: 12px;
}
.kpi-card {
  position: relative;
  display: flex;
  flex-direction: column;
  gap: 6px;
  padding: 18px 20px;
  background: #fff;
  border: 1px solid #e8edf4;
  border-radius: 14px;
  box-shadow: 0 1px 3px rgba(15, 23, 42, 0.05);
  overflow: hidden;
}
.kpi-card::before {
  content: '';
  position: absolute;
  inset: 0 auto 0 0;
  width: 4px;
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
.kpi-label {
  color: #64748b;
  font-size: 13px;
}
.kpi-value {
  font-size: 27px;
  font-weight: 700;
  color: #0f172a;
  letter-spacing: -0.5px;
}
.kpi-value small {
  margin-left: 4px;
  font-size: 13px;
  font-weight: 400;
  color: #94a3b8;
}
.kpi-sub {
  color: #94a3b8;
  font-size: 12px;
}
.chart-grid {
  display: grid;
  grid-template-columns: repeat(2, 1fr);
  gap: 12px;
}
.chart-panel {
  background: #fff;
  border: 1px solid #e8edf4;
  border-radius: 14px;
  padding: 16px 18px;
  box-shadow: 0 1px 3px rgba(15, 23, 42, 0.05);
}
.chart-panel.wide {
  grid-column: 1 / -1;
}
.chart-panel h3 {
  margin: 0 0 6px;
  font-size: 15px;
  color: #0f172a;
}
.chart-panel h3 small {
  margin-left: 8px;
  font-weight: 400;
  font-size: 12px;
  color: #94a3b8;
}
.chart-box {
  height: 240px;
}
.chart-box.tall {
  height: 280px;
}
.bi-note {
  border-radius: 10px;
}
/* 问数 */
.chat {
  display: flex;
  flex-direction: column;
  gap: 12px;
  height: calc(100vh - 210px);
  min-height: 420px;
}
.chat-list {
  flex: 1;
  overflow-y: auto;
  display: flex;
  flex-direction: column;
  gap: 14px;
  padding: 6px 4px;
}
.chat-welcome {
  margin: auto;
  text-align: center;
  max-width: 460px;
}
.chat-welcome h2 {
  margin: 0 0 6px;
  font-size: 22px;
  color: #0f172a;
}
.chat-welcome p {
  color: #64748b;
  font-size: 13px;
  line-height: 1.7;
}
.quick-chips {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  justify-content: center;
  margin-top: 16px;
}
.quick-chips button {
  padding: 7px 14px;
  border: 1px solid #dbe4ef;
  border-radius: 999px;
  background: #fff;
  color: #334155;
  font-size: 13px;
  cursor: pointer;
  transition: all 0.15s;
}
.quick-chips button:hover {
  border-color: #2563eb;
  color: #2563eb;
  background: #eff6ff;
}
.msg {
  display: flex;
}
.msg-user {
  justify-content: flex-end;
}
.msg-user .bubble {
  background: #2563eb;
  color: #fff;
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
  background: #fff;
  border: 1px solid #e8edf4;
  border-radius: 4px 14px 14px 14px;
  padding: 14px 16px;
  max-width: 86%;
  box-shadow: 0 1px 3px rgba(15, 23, 42, 0.05);
}
.pending {
  display: flex;
  align-items: center;
  gap: 10px;
  color: #2563eb;
  font-size: 13px;
}
.answer-head {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-bottom: 10px;
}
.answer-tag {
  background: #eff6ff;
  color: #2563eb;
  border-radius: 6px;
  padding: 3px 10px;
  font-size: 12px;
  font-weight: 600;
}
.answer-meta {
  color: #94a3b8;
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
  margin: 10px 0 0;
  color: #94a3b8;
  font-size: 12px;
  line-height: 1.6;
  border-top: 1px dashed #e2e8f0;
  padding-top: 8px;
}
.plain-answer {
  margin: 0;
  color: #334155;
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
  background: #fef2f2;
  color: #dc2626;
  font-weight: 700;
  flex: none;
}
.denied-card strong {
  color: #0f172a;
  font-size: 14px;
}
.denied-card p {
  margin: 4px 0 0;
  color: #64748b;
  font-size: 13px;
  line-height: 1.6;
}
.chat-input {
  display: flex;
  gap: 10px;
}
@media (max-width: 1080px) {
  .chart-grid {
    grid-template-columns: 1fr;
  }
}
</style>
