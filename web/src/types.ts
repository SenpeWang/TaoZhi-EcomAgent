/** 前后端 /api/v2 契约；字段保留服务端命名，界面变量采用 camelCase。 */
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
export interface WorkspaceInfo {
  name: string
  mode: string
  demo: boolean
  tenant_code: string
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
export interface TaskResult {
  answer: string
  specialists?: { name: string; count: number }[]
  missing?: string[]
  citations?: Citation[]
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
