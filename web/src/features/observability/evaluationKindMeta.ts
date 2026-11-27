/**
 * 评测体系展示文案：统一 Agent / RAG 的可见名称与说明，供体系切换与页面标题复用。
 */
import type { EvaluationKind } from './evaluationTypes'

export interface EvaluationKindMeta {
  title: string
  shortTitle: string
  description: string
}

export const evaluationKindMeta: Record<EvaluationKind, EvaluationKindMeta> = {
  agent: {
    title: 'Agent 多智能体调研',
    shortTitle: 'Agent',
    description: '工具调用 · 任务轨迹 · 研报交付',
  },
  rag: {
    title: 'RAG 企业知识问答',
    shortTitle: 'RAG',
    description: '检索 · 问答 · 权限与安全对抗',
  },
}
