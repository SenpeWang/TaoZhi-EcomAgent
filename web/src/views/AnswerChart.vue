<template>
  <div ref="host" class="answer-chart" />
</template>

<script setup lang="ts">
import { onMounted, onUnmounted, ref } from 'vue'
import * as echarts from 'echarts/core'
import { BarChart, LineChart } from 'echarts/charts'
import { GridComponent, TooltipComponent } from 'echarts/components'
import { CanvasRenderer } from 'echarts/renderers'

echarts.use([BarChart, LineChart, GridComponent, TooltipComponent, CanvasRenderer])

type MetricBlock = {
  columns: string[]
  rows: unknown[][]
  meta?: { metric_key?: string }
  [k: string]: unknown
}
const props = defineProps<{ data: MetricBlock }>()
const host = ref<HTMLElement | null>(null)
let chart: echarts.ECharts | null = null

/** 按列结构自动选型：日期序列→折线，双类目→堆叠条，单类目→横向条形。 */
function render() {
  if (!host.value || props.data.rows.length < 2) return
  if (!chart) chart = echarts.init(host.value)
  const d = props.data
  const cols = d.columns
  const axis = {
    axisLabel: { color: '#64748b' },
    axisLine: { lineStyle: { color: '#e2e8f0' } },
    splitLine: { lineStyle: { color: '#f1f5f9' } },
  }
  const PALETTE = ['#2563eb', '#0891b2', '#059669', '#d97706', '#dc2626', '#7c3aed']
  const isSeries = cols.includes('day') && cols.length === 2
  const twoDims = cols.length === 3
  const valueIdx = cols.length - 1
  if (isSeries) {
    chart.setOption(
      {
        tooltip: { trigger: 'axis' },
        grid: { left: 60, right: 16, top: 16, bottom: 30 },
        xAxis: { type: 'category', data: d.rows.map((r) => String(r[0]).slice(5)), ...axis },
        yAxis: { type: 'value', ...axis },
        series: [
          {
            type: 'line',
            data: d.rows.map((r) => r[1]),
            smooth: true,
            lineStyle: { width: 2.5, color: '#2563eb' },
            areaStyle: { color: 'rgba(37,99,235,.12)' },
          },
        ],
      },
      true,
    )
  } else if (twoDims) {
    const cats = [...new Set(d.rows.map((r) => String(r[0])))]
    const subs = [...new Set(d.rows.map((r) => String(r[1])))]
    chart.setOption(
      {
        tooltip: { trigger: 'axis' },
        legend: { top: 0, textStyle: { color: '#64748b' } },
        grid: { left: 70, right: 16, top: 30, bottom: 30 },
        xAxis: { type: 'category', data: cats, ...axis },
        yAxis: { type: 'value', ...axis },
        series: subs.map((s, si) => ({
          name: s,
          type: 'bar',
          stack: 'total',
          barMaxWidth: 26,
          itemStyle: { color: PALETTE[si % PALETTE.length] },
          data: cats.map((c) => {
            const hit = d.rows.find((r) => String(r[0]) === c && String(r[1]) === s)
            return hit ? Number(hit[valueIdx]) : 0
          }),
        })),
      },
      true,
    )
  } else {
    const rows = [...d.rows].sort((a, b) => Number(b[valueIdx]) - Number(a[valueIdx]))
    chart.setOption(
      {
        tooltip: { trigger: 'axis' },
        grid: { left: 90, right: 44, top: 12, bottom: 26 },
        xAxis: { type: 'value', ...axis },
        yAxis: {
          type: 'category',
          data: rows.map((r) => String(r[0])),
          ...axis,
          splitLine: { show: false },
        },
        series: [
          {
            type: 'bar',
            data: rows.map((r) => Number(r[valueIdx])),
            barWidth: 14,
            itemStyle: { borderRadius: [0, 6, 6, 0], color: '#2563eb' },
            label: { show: true, position: 'right', color: '#475569' },
          },
        ],
      },
      true,
    )
  }
}
/**
 * 窗口尺寸变化时重算图表尺寸。
 */
function onResize() {
  chart?.resize()
}
onMounted(() => {
  render()
  window.addEventListener('resize', onResize)
})
onUnmounted(() => {
  window.removeEventListener('resize', onResize)
  chart?.dispose()
  chart = null
})
</script>
