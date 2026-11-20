/** 前后端 /api 契约；字段保留服务端命名，界面变量采用 camelCase。 */
export interface Capabilities {
  system: boolean
  write: boolean
  review: boolean
  permissions: boolean
}
export interface Membership {
  node_id: string
  role: string
  name?: string
}
export interface User {
  id: string
  username: string
  display_name: string
  system_admin: boolean
  is_boss: boolean
  is_leader: boolean
  roles: string[]
  must_change: boolean
  memberships: Membership[]
  capabilities: Capabilities
  active?: boolean
}
export interface QuickAccount {
  username: string
  display_name: string
  role: string
  password?: string
}
export interface WorkspaceInfo {
  name: string
  mode: string
  tenant_code: string
  quick_accounts?: QuickAccount[]
}
export interface Organization {
  id: string
  name: string
  kind: string
  parent_id: string | null
  active: boolean
}
export interface OrganizationTree extends Organization {
  label: string
  children: OrganizationTree[]
}
export interface DocumentRecord {
  id: string
  title?: string
  body?: string
  node_name?: string
  scope: string
  node_id: string
  level: number
  state: string
  current_version: number
  acl_version: number
  ai_allowed: boolean
  download_allowed: boolean
  can_write?: boolean
  can_download?: boolean
  error?: string | null
}
export interface DocumentListItem extends DocumentRecord {
  title: string
}
export interface DocumentVersion {
  version: number
  title: string
  body: string
  created_at: string
}
export interface Citation {
  chunk_id: string
  document_id: string
  title: string
  version: number
  quote: string
}
export interface ResearchPlatform {
  name: string
  label: string
  state: string
  state_label: string
  error?: string
  brief?: string
  items_count: number
}
export interface ResearchSubagent {
  id: string
  type_name: string
  role: string
  display_name?: string
  model_tier?: string
  status: string
  elapsed?: number
  artifacts?: string[]
}
export interface TaskResult {
  answer?: string
  report?: string
  outline?: string[]
  platforms?: ResearchPlatform[]
  subagents?: ResearchSubagent[]
  artifacts?: string[]
  elapsed?: number
  model_calls?: number
  specialists?: { name: string; display_name?: string; count: number }[]
  missing?: string[]
  citations?: Citation[]
}
export interface TaskEventItem {
  id: number
  stage: string
  progress: number
  status: string
  created_at?: string | null
}
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
  matched_run_version?: number | null
  created_at: string
  started_at: string | null
  finished_at: string | null
}
export interface TracePage {
  items: TraceSummary[]
  next_cursor: string | null
}
export interface TraceEvent extends TaskEventItem {
  run_version: number
  created_at: string
}
export interface TraceModelCall {
  id: number
  role: string
  role_label?: string | null
  elapsed_ms: number
  prompt_tokens: number
  completion_tokens: number
  outcome: string
  created_at: string
}
export interface TraceDetail {
  trace: TraceSummary
  trace_id: string | null
  events: TraceEvent[]
  has_more_events: boolean
  model_calls: TraceModelCall[]
  has_more_calls: boolean
}
export type SpanStatus = 'running' | 'ok' | 'error' | 'interrupted'
export type SpanKind = 'CHAIN' | 'AGENT' | 'LLM' | 'TOOL' | 'RETRIEVER' | 'INTERNAL'
export interface TraceRun {
  run_version: number
  trace_id: string
  started_at: string
  ended_at: string | null
  status: SpanStatus
  span_count: number
  failed_count: number
  input_tokens: number
  output_tokens: number
}
export interface TraceRunPage {
  items: TraceRun[]
  next_before_version: number | null
}
export interface TraceSpan {
  trace_id: string
  span_id: string
  parent_span_id: string | null
  name: string
  display_name?: string | null
  kind: SpanKind
  started_at: string
  ended_at: string | null
  duration_ms: number | null
  status: SpanStatus
  error_type: string | null
  attributes: Record<string, string | number | boolean>
}
export interface TraceSpanPage {
  items: TraceSpan[]
  next_cursor: string | null
  observed_at: string
}
export interface Task {
  id: string
  question: string
  kind: string
  state: string
  stage: string
  progress: number
  created_at: string
  finished_at?: string | null
  error?: string | null
  input_level: number
  node_id: string
  owner_id: string
  result?: TaskResult | null
  restricted: boolean
  can_review: boolean
  can_cancel: boolean
  events?: TaskEventItem[]
}
export interface ApprovalPayload {
  user_id?: string
  role?: string
  remove?: boolean
  node_id?: string
  scope?: string
  level?: number
  ai_allowed?: boolean
  download_allowed?: boolean
  can_download?: boolean
  can_write?: boolean
  effect?: string
  expires_at?: string | null
  revoke?: boolean
  reset_password?: boolean
  active?: boolean
  system_admin?: boolean
  name?: string
  expected_version?: number
  expected_acl?: number
}
export interface Approval {
  id: string
  kind: string
  label: string
  title?: string
  state: string
  target_id: string
  requested_by: string
  required_role: string
  payload: ApprovalPayload
  can_decide: boolean
}
export interface AuditEvent {
  id: number
  created_at: string
  action: string
  actor_id: string
  node_id: string
  level: number
  system: boolean
}
export interface Grant {
  user_id: string
  display_name: string
  effect: string
  can_download: boolean
  can_write: boolean
  expires_at: string | null
}
export interface Dashboard {
  documents: number
  tasks: number
  review: number
  completed: number
  teams: string[]
  model_ready: boolean
  business_ready: boolean
}
export interface RuntimeStatus {
  database: boolean
  worker_available: boolean
  counts: { state: string; count: number }[]
  model_configured: boolean
  business_configured: boolean
}
export interface Items<T> {
  items: T[]
}
export interface CreatedResource {
  id: string
}
export interface LoginResponse {
  user: User
}
