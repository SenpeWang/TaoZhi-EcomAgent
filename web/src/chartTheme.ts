/** 经营看板和问答图表共用的坐标轴、业务序列与异常状态颜色。 */
export const chartColors = {
  primary: '#145b76',
  secondary: '#188577',
  primaryArea: 'rgba(20, 91, 118, 0.14)',
  label: '#546b79',
  border: '#dce3e8',
  grid: '#eaf0f3',
  warning: '#a56019',
  danger: '#ad4149',
}

export const chartSeriesColors = [
  chartColors.primary,
  chartColors.secondary,
  '#726499',
  '#a07129',
  '#4d789c',
  '#a75570',
]

export const chartAxisStyle = {
  axisLabel: { color: chartColors.label, fontSize: 12 },
  axisLine: { lineStyle: { color: chartColors.border } },
  splitLine: { lineStyle: { color: chartColors.grid } },
}
