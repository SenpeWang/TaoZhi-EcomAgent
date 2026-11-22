import type { CursorPage } from '../../shared/apiTypes'
import type { TaskEventItem } from '../tasks/types'

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

export interface TraceOwner {
  id: string
  username: string
  display_name: string
}

export interface TraceEvent extends TaskEventItem {
  run_version: number
  created_at: string
}

export interface TraceDetail {
  trace: TraceSummary
  events: TraceEvent[]
  has_more_events: boolean
}

export type SpanStatus = 'running' | 'ok' | 'error' | 'interrupted'

export type SpanKind =
  'CHAIN' | 'AGENT' | 'LLM' | 'TOOL' | 'RETRIEVER' | 'EMBEDDING' | 'RERANKER' | 'INTERNAL'

export type ActivityCategory = SpanKind | 'CONTEXT' | 'GUARDRAIL'

export type DiagnosticCategory =
  'model_api' | 'tool' | 'retrieval' | 'context' | 'permission' | 'storage' | 'workflow'

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

export interface TraceSpanPage<T extends TraceSpan> extends CursorPage<T> {
  observed_at: string
}

export interface TraceActivity extends TraceSpan {
  actor: { kind: 'agent' | 'workflow'; span_id: string; display_name: string } | null
}
