<template>
  <div ref="chartContainer" class="answer-chart" />
</template>

<script setup lang="ts">
import { onMounted, onUnmounted, ref, watch } from 'vue'
import * as echarts from 'echarts/core'
import { BarChart, LineChart } from 'echarts/charts'
import { GridComponent, LegendComponent, TooltipComponent } from 'echarts/components'
import { CanvasRenderer } from 'echarts/renderers'
import { chartAxisStyle, chartColors, chartSeriesColors } from '../chartTheme'

echarts.use([BarChart, LineChart, GridComponent, LegendComponent, TooltipComponent, CanvasRenderer])

type MetricBlock = {
  columns: string[]
  rows: unknown[][]
  meta?: { metric_key?: string }
  [k: string]: unknown
}
const props = defineProps<{ data: MetricBlock }>()
const chartContainer = ref<HTMLElement | null>(null)
let chart: echarts.ECharts | null = null

/** 按列结构自动选型：日期序列→折线，双类目→堆叠条，单类目→横向条形。 */
function render() {
  if (!chartContainer.value) return
  if (props.data.rows.length < 2) {
    chart?.clear()
    return
  }
  if (!chart) chart = echarts.init(chartContainer.value)
  const { columns, rows } = props.data
  const isTimeSeries = columns.includes('day') && columns.length === 2
  const hasTwoDimensions = columns.length === 3
  const valueColumnIndex = columns.length - 1
  if (isTimeSeries) {
    chart.setOption(
      {
        tooltip: { trigger: 'axis' },
        grid: { left: 60, right: 16, top: 16, bottom: 30 },
        xAxis: {
          type: 'category',
          data: rows.map((r) => String(r[0]).slice(5)),
          ...chartAxisStyle,
        },
        yAxis: { type: 'value', ...chartAxisStyle },
        series: [
          {
            type: 'line',
            data: rows.map((r) => r[1]),
            itemStyle: { color: chartColors.primary },
            lineStyle: { width: 2.5, color: chartColors.primary },
            areaStyle: { color: chartColors.primaryArea },
          },
        ],
      },
      true,
    )
  } else if (hasTwoDimensions) {
    const categories = [...new Set(rows.map((r) => String(r[0])))]
    const seriesNames = [...new Set(rows.map((r) => String(r[1])))]
    chart.setOption(
      {
        tooltip: { trigger: 'axis' },
        legend: { top: 0, textStyle: { color: chartColors.label } },
        grid: { left: 70, right: 16, top: 30, bottom: 30 },
        xAxis: { type: 'category', data: categories, ...chartAxisStyle },
        yAxis: { type: 'value', ...chartAxisStyle },
        series: seriesNames.map((seriesName, seriesIndex) => ({
          name: seriesName,
          type: 'bar',
          stack: 'total',
          barMaxWidth: 26,
          itemStyle: { color: chartSeriesColors[seriesIndex % chartSeriesColors.length] },
          data: categories.map((category) => {
            const match = rows.find(
              (row) => String(row[0]) === category && String(row[1]) === seriesName,
            )
            return match ? Number(match[valueColumnIndex]) : 0
          }),
        })),
      },
      true,
    )
  } else {
    const rankedRows = [...rows].sort(
      (a, b) => Number(b[valueColumnIndex]) - Number(a[valueColumnIndex]),
    )
    chart.setOption(
      {
        tooltip: { trigger: 'axis' },
        grid: { left: 90, right: 44, top: 12, bottom: 26 },
        xAxis: { type: 'value', ...chartAxisStyle },
        yAxis: {
          type: 'category',
          data: rankedRows.map((r) => String(r[0])),
          ...chartAxisStyle,
          splitLine: { show: false },
        },
        series: [
          {
            type: 'bar',
            data: rankedRows.map((r) => Number(r[valueColumnIndex])),
            barWidth: 14,
            itemStyle: { borderRadius: [0, 6, 6, 0], color: chartColors.primary },
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
watch(() => props.data, render, { flush: 'post' })
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
