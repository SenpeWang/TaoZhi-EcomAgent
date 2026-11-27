/**
 * 知识文档的完整记录。
 * scope、node_id、level 为归属范围、部门与密级；state 为处理状态；
 * current_version 与 acl_version 为正文与授权版本；
 * ai_allowed、download_allowed 以及服务端下发的 can_write、can_download 为访问控制。
 */
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

/**
 * 文档列表项，在 DocumentRecord 基础上保证标题已生成。
 */
export interface DocumentListItem extends DocumentRecord {
  title: string
}

/**
 * 文档的单个历史版本。
 * version 为版本号；title、body 为该版本的标题与正文快照；created_at 为生成时间。
 */
export interface DocumentVersion {
  version: number
  title: string
  body: string
  created_at: string
}

/**
 * 文档授权记录。
 * user_id、display_name 为被授权成员；effect 为允许或拒绝；
 * can_download、can_write 为可执行操作；expires_at 为到期时间，为 null 表示长期有效。
 */
export interface Grant {
  user_id: string
  display_name: string
  effect: string
  can_download: boolean
  can_write: boolean
  expires_at: string | null
}

/**
 * 文档切片及其向量化状态。
 * ordinal 为切片序号，page 为页码，chars 为字符数，text 为原文；
 * embedded 表示已写入向量库；dim、norm、preview 为向量维度、范数与预览分量。
 */
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

/**
 * 文档切片索引的当前版本信息。
 * version 为索引版本；state 为索引状态；
 * total 与 embedded 为切片总数与已向量化数量；model、dim 为向量模型与维度；items 为切片列表。
 */
export interface DocumentChunkIndex {
  version: number
  state: string
  total: number
  embedded: number
  model: string
  dim: number
  items: DocumentChunk[]
}
