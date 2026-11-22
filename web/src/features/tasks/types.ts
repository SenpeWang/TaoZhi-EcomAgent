export interface Citation {
  chunk_id: string
  document_id: string
  title: string
  version: number
  quote: string
  cited?: boolean
  page?: number | null
}

export interface KnowledgeQuestion {
  question: string
  node_id: string
  allow_external: boolean
  allow_public_reference: boolean
  require_review: boolean
  sku_id: string
  order_id: string
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
  role: string
  display_name: string
  model_tier?: string
  status: string
  elapsed?: number
  artifacts?: string[]
}

export interface TaskResult {
  answer?: string
  answer_basis?:
    | 'internal_evidence'
    | 'business_snapshot'
    | 'source_excerpt'
    | 'public_reference'
    | 'insufficient_evidence'
  guidance?: string
  public_sources?: { title: string; url: string }[]
  cache_hit?: boolean
  data?: unknown
  report?: string
  outline?: string[]
  platforms?: ResearchPlatform[]
  subagents?: ResearchSubagent[]
  artifacts?: string[]
  elapsed?: number
  model_calls?: number
  specialists?: { name: string; display_name: string; count: number }[]
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
