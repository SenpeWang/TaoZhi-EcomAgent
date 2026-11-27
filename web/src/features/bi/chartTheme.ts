/** 数据中心和问答图表共用的坐标轴、业务序列与异常状态颜色。 */
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

/**
 * 多序列图表的取色顺序，按主色、辅助色、成功色、警告色、异常色排列，
 * 末位补充一个区分度较高的紫色，供超过五条序列时循环使用。
 */
export const chartSeriesColors = [
  chartColors.primary,
  chartColors.secondary,
  chartColors.success,
  chartColors.warning,
  chartColors.danger,
  '#7c3aed',
]

/**
 * 图表坐标轴的统一样式：轴标签字号与颜色、轴线颜色、分割线颜色，
 * 直接作为 ECharts 的 axisLabel、axisLine、splitLine 配置使用。
 */
export const chartAxisStyle = {
  axisLabel: { color: chartColors.label, fontSize: 12 },
  axisLine: { lineStyle: { color: chartColors.border } },
  splitLine: { lineStyle: { color: chartColors.grid } },
}
