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
  mime_type?: string
  has_original?: boolean
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

export interface Grant {
  user_id: string
  display_name: string
  effect: string
  can_download: boolean
  can_write: boolean
  expires_at: string | null
}

export interface DocumentChunk {
  id: string
  ordinal: number
  page: number
  chars: number
  text: string
  embedded: boolean
  dim: number
  norm: number
  preview: number[]
}

export interface DocumentChunkIndex {
  version: number
  state: string
  total: number
  embedded: number
  model: string
  dim: number
  items: DocumentChunk[]
}
