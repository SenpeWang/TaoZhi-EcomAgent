/**
 * 回答引用的文档片段。
 * chunk_id、document_id 定位来源，version 为引用版本，quote 为原文摘录，
 * cited 表示该引用是否在答复中被引用，page 为页码。
 */
export interface Citation {
  chunk_id: string
  document_id: string
  title: string
  version: number
  quote: string
  cited?: boolean
  page?: number | null
}

/**
 * 知识问答的输入参数。
 * node_id 为检索范围；allow_external、allow_public_reference、allow_general_knowledge
 * 分别控制外发调用、公开来源引用与通用知识补充；require_review 表示答复需要审核；
 * sku_id、order_id 为本轮问答附带的业务上下文。
 */
export interface KnowledgeQuestion {
  question: string
  node_id: string
  allow_external: boolean
  allow_public_reference: boolean
  allow_general_knowledge: boolean
  require_review: boolean
  sku_id: string
  order_id: string
}

/**
 * 调研任务采集的货源平台状态。
 * state、state_label 为采集状态及其中文名称；items_count 为采集条数；
 * brief 为平台摘要；error 为失败原因。
 */
export interface ResearchPlatform {
  name: string
  label: string
  state: string
  state_label: string
  error?: string
  brief?: string
  items_count: number
}

/**
 * 调研子智能体的执行记录。
 * role、display_name、agent_type、model_tier 为分工与模型档位；
 * status 为执行状态；decision_reason、summary 为决策与产出摘要；
 * elapsed 为耗时（秒）；artifacts 为产物路径。
 */
export interface ResearchSubagent {
  id: string
  role: string
  display_name: string
  task_id?: string
  agent_type?: string
  model_tier?: string
  status: string
  decision_reason?: string
  summary?: string
  elapsed?: number
  artifacts?: string[]
}

/**
 * 调研任务的评论记录。
 * author_agent 为评论智能体，body 为内容，created_at 为提交时间。
 */
export interface ResearchTaskComment {
  id: number
  author_agent: string
  body: string
  created_at?: string
}

/**
 * 调研任务板条目。
 * task_key 为任务编号，role 为分工角色，status 为状态，revision 用于并发更新；
 * owner_agent 为承接智能体；brief 为任务说明；result 为执行结果；comments 为评论。
 */
export interface ResearchTaskBoardItem {
  id: string
  task_key: string
  role: string
  status: string
  revision: number
  owner_agent?: string | null
  brief?: {
    goal?: string
    output_format?: string
    source_guidance?: string
    boundaries?: string
    model_tier?: string
  }
  result?: {
    summary?: string
    status?: string
    elapsed?: number
    model_tier?: string
    gaps?: string[]
  } | null
  error?: string
  comments?: ResearchTaskComment[]
}

/**
 * 任务产出的完整结果。
 * answer、answer_basis 为答复与答复依据类型；guidance、outline 为写作指引与提纲；
 * platforms、subagents、research_tasks、citations、public_sources 等字段随任务类型出现；
 * elapsed 与 model_calls 为耗时（秒）与模型调用次数。
 */
export interface TaskResult {
  answer?: string
  answer_basis?:
    | 'internal_evidence'
    | 'business_snapshot'
    | 'source_excerpt'
    | 'public_reference'
    | 'general_knowledge'
    | 'insufficient_evidence'
  guidance?: string
  public_sources?: { title: string; url: string }[]
  cache_hit?: boolean
  data?: unknown
  report?: string
  keyword?: string
  outline?: string[]
  platforms?: ResearchPlatform[]
  subagents?: ResearchSubagent[]
  research_tasks?: ResearchTaskBoardItem[]
  artifacts?: string[]
  elapsed?: number
  model_calls?: number
  specialists?: { name: string; display_name: string; count: number }[]
  missing?: string[]
  citations?: Citation[]
}

/**
 * 任务阶段事件。
 * stage 为阶段编码，progress 为进度百分比，status 为状态，created_at 为发生时间。
 */
export interface TaskEventItem {
  id: number
  stage: string
  progress: number
  status: string
  created_at?: string | null
}

/**
 * 任务列表与详情的记录。
 * kind 为任务类型，question 为原始提问；state、stage、progress 为执行状态；
 * input_level 与 node_id 为输入密级及归属部门；result 为产出结果；
 * restricted 表示是否受限；can_review、can_cancel 为当前用户可执行的操作。
 */
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
