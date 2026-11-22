/** 经营看板和问答图表共用的坐标轴、业务序列与异常状态颜色。 */
export const chartColors = {
  primary: '#2563eb',
  secondary: '#0891b2',
  success: '#059669',
  primaryArea: 'rgba(37, 99, 235, 0.12)',
  label: '#64748b',
  border: '#e2e8f0',
  grid: '#f1f5f9',
  warning: '#d97706',
  danger: '#dc2626',
}

export const chartSeriesColors = [
  chartColors.primary,
  chartColors.secondary,
  chartColors.success,
  chartColors.warning,
  chartColors.danger,
  '#7c3aed',
]

export const chartAxisStyle = {
  axisLabel: { color: chartColors.label, fontSize: 12 },
  axisLine: { lineStyle: { color: chartColors.border } },
  splitLine: { lineStyle: { color: chartColors.grid } },
}
