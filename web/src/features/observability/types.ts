import type { CursorPage } from '../../shared/apiTypes'
import type { TaskEventItem } from '../tasks/types'

/**
 * 轨迹列表摘要。
 * kind 区分问答与调研；question 为原始提问；owner_id、username、display_name 为发起人；
 * state、stage、progress 为执行状态；attempts 为尝试次数；
 * matched_trace_id 为关联轨迹编号；created_at、started_at、finished_at 为时间信息。
 */
export interface TraceSummary {
  id: string
  kind: 'ask' | 'research'
  question: string
  owner_id: string
  username: string | null
  display_name: string | null
  node_name: string | null
  state: string
  stage: string
  progress: number
  attempts: number
  matched_trace_id: string | null
  created_at: string
  started_at: string | null
  finished_at: string | null
}

/**
 * 轨迹发起人的身份信息，包含编号、登录名与显示名。
 */
export interface TraceOwner {
  id: string
  username: string
  display_name: string
}

/**
 * 轨迹时间线事件，在任务事件基础上补充执行版本 run_version 与创建时间。
 */
export interface TraceEvent extends TaskEventItem {
  run_version: number
  created_at: string
}

/**
 * 轨迹详情。
 * trace 为摘要；events 为时间线事件；has_more_events 表示是否还有更早的事件未加载。
 */
export interface TraceDetail {
  trace: TraceSummary
  events: TraceEvent[]
  has_more_events: boolean
}

/**
 * 调用节点状态：running 执行中、ok 成功、error 失败、interrupted 已中断。
 */
export type SpanStatus = 'running' | 'ok' | 'error' | 'interrupted'

/**
 * 调用节点类型，覆盖链路、智能体、模型、工具、检索、向量化、重排与内部操作。
 */
export type SpanKind =
  'CHAIN' | 'AGENT' | 'LLM' | 'TOOL' | 'RETRIEVER' | 'EMBEDDING' | 'RERANKER' | 'INTERNAL'

/**
 * 行动分类，在 SpanKind 基础上补充 CONTEXT 上下文处理与 GUARDRAIL 权限校验。
 */
export type ActivityCategory = SpanKind | 'CONTEXT' | 'GUARDRAIL'

/**
 * 诊断分类，标识问题发生在模型 API、工具、检索、上下文、权限、存储或工作流。
 */
export type DiagnosticCategory =
  'model_api' | 'tool' | 'retrieval' | 'context' | 'permission' | 'storage' | 'workflow'

/**
 * 采集到的异常诊断。
 * timestamp 为发生时间；category、code、message 为分类、编码与说明；
 * outcome 为处理结果；level 为严重级别；exception_type、http_status 为异常类型与状态码。
 */
export interface TraceDiagnostic {
  timestamp: string
  category: DiagnosticCategory
  code: string
  message: string
  outcome: 'failed' | 'retry' | 'fallback' | 'skipped'
  level: 'error' | 'warning'
  exception_type: string | null
  http_status: number | null
}

/**
 * 一次执行版本的汇总。
 * run_version、trace_id 定位执行，started_at、ended_at、status 为时间与状态；
 * kind_counts、category_counts、diagnostic_counts 为各类节点与诊断计数；
 * span_count、failed_count 为节点与失败数；input_tokens、output_tokens 为 Token 用量。
 */
export interface TraceRun {
  run_version: number
  trace_id: string
  started_at: string
  ended_at: string | null
  status: SpanStatus
  kind_counts: Partial<Record<SpanKind, number>>
  category_counts: Partial<Record<ActivityCategory, number>>
  diagnostic_counts: Partial<Record<DiagnosticCategory, number>>
  span_count: number
  failed_count: number
  input_tokens: number
  output_tokens: number
}

/**
 * 单个调用节点。
 * span_id、parent_span_id 构成父子关系，为 null 时是根节点；
 * name、display_name 为技术标识与展示名称；kind、category 为类型；
 * started_at、ended_at、duration_ms 为时间信息（毫秒）；
 * status、error_type、diagnostics 为状态与异常；attributes 为采集到的属性。
 */
export interface TraceSpan {
  trace_id: string
  span_id: string
  parent_span_id: string | null
  name: string
  display_name: string
  kind: SpanKind
  category: ActivityCategory
  started_at: string
  ended_at: string | null
  duration_ms: number | null
  status: SpanStatus
  error_type: string | null
  diagnostics: TraceDiagnostic[]
  attributes: Record<string, string | number | boolean>
}

/**
 * 调用节点的游标分页结果。
 * observed_at 为服务端本次观测的时间戳，用于计算运行中执行的耗时。
 */
export interface TraceSpanPage<T extends TraceSpan> extends CursorPage<T> {
  observed_at: string
}

/**
 * 时间线中的行动条目，在调用节点基础上补充真实执行者 actor 信息。
 */
export interface TraceActivity extends TraceSpan {
  actor: { kind: 'agent' | 'workflow'; span_id: string; display_name: string } | null
}
