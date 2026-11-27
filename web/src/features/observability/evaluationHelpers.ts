import { ElMessage } from 'element-plus'
import {
  evaluationAuditActions,
  formatMetricValue,
  type EvaluationAuditAction,
  type EvaluationDataset,
  type EvaluationMetricSpec,
  type EvaluationMode,
  type EvaluationScope,
} from './evaluationTypes'

const CORE_METRIC_COUNT = 5

/**
 * 概览指标卡数据：指标定义、最新值、基线值与相对基线差值。
 */
export interface MetricCard {
  spec: EvaluationMetricSpec
  latest: number | null
  baseline: number | null
  delta: number | null
}

/**
 * 批次详情指标卡数据：指标定义与当前批次取值。
 */
export interface RunMetricCard {
  spec: EvaluationMetricSpec
  value: number | null
}

/**
 * 按指标目录顺序挑选核心指标：优先比例/得分类，不足时回退原始顺序。
 * @param specs - 当前口径的指标定义。
 * @returns 最多 5 个核心指标定义。
 */
export function selectCoreMetrics(specs: EvaluationMetricSpec[]): EvaluationMetricSpec[] {
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
export function shortMetricLabel(spec: EvaluationMetricSpec): string {
  return spec.label.replace(/\s*[（(][^（）()]*[）)]\s*/g, '').trim() || spec.label
}

/**
 * 组合指标的悬浮说明：完整名称与口径描述。
 * @param spec - 指标定义。
 * @returns 可放入 el-tooltip 的说明文本。
 */
export function metricTooltip(spec: EvaluationMetricSpec): string {
  return spec.description ? `${spec.label}\n${spec.description}` : spec.label
}

/**
 * 按单位格式化聚合卡片数值，缺少比例指标时回退为百分比。
 * @param spec - 聚合指标定义；为空表示目录未登记比例指标。
 * @param value - 聚合统计值。
 * @returns 可读文本或缺失占位。
 */
export function formatAggregateValue(
  spec: EvaluationMetricSpec | null,
  value: number | null,
): string {
  if (spec) return formatMetricValue(spec, value)
  return value === null ? '—' : (value * 100).toFixed(1) + '%'
}

/**
 * 读取数据集可用于评测中心真实结果的执行方式：优先接口的 execution_modes，缺省时从元数据过滤。
 * @param dataset - 数据集条目。
 * @returns 仅包含真实执行的执行方式；无登记时回退为 live。
 */
export function datasetExecutionModes(dataset: EvaluationDataset | null): EvaluationMode[] {
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
export function datasetScopeList(dataset: EvaluationDataset | null): EvaluationScope[] {
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
 * 截取 sha256 摘要前 12 位用于表格展示，完整值放在 title 提示中。
 * @param value - 摘要文本。
 * @returns 前 12 位摘要或缺失占位。
 */
export function shortSha(value: string | null | undefined): string {
  return value ? value.slice(0, 12) : '—'
}

/**
 * 展示审计动作的中文名称。
 * @param action - 审计动作编码。
 * @returns 中文动作名称或原始编码。
 */
export function auditActionLabel(action: string): string {
  return evaluationAuditActions[action as EvaluationAuditAction] ?? action
}

/**
 * 将审计摘要压缩为单行文本。
 * @param value - 审计摘要对象。
 * @returns 截断后的单行摘要。
 */
export function summarizeJson(value: Record<string, unknown>): string {
  const text = JSON.stringify(value)
  return text.length > 120 ? text.slice(0, 120) + '…' : text
}

/**
 * 解析配置弹窗中的 JSON 对象文本。
 * @param text - 用户输入的 JSON 文本。
 * @param label - 出错时提示的字段名称。
 * @returns 解析后的普通对象。
 * @throws 文本不是合法 JSON 对象时抛中文错误。
 */
export function parseJsonObject(text: string, label: string): Record<string, unknown> {
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
 * 通过临时 <a> 元素触发浏览器下载并释放对象 URL。
 * @param blob - 文件内容。
 * @param filename - 保存文件名。
 */
export function saveBlob(blob: Blob, filename: string): void {
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
 * 下载批次原始报告 JSON；未保存原始报告时给出可见提示。
 * @param runId - 批次编号。
 */
export async function downloadRunReport(runId: string): Promise<void> {
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
