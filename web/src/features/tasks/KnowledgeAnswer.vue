<script setup lang="ts">
import { computed, ref } from 'vue'
import { answerBasisLabel, citationGroups, publicSourceUrl } from './knowledgeAnswers'
import type { TaskResult } from './types'
import MarkdownReport from '../../shared/MarkdownReport.vue'

const props = defineProps<{ result: TaskResult; taskId: string }>()
const emit = defineEmits<{ openDoc: [id: string] }>()
const groups = computed(() => citationGroups(props.result))
const answerRoot = ref<HTMLElement>()
const paragraphs = computed(() => {
  const body = props.result.answer || ''
  if (!props.result.citations?.some((source) => source.cited)) return [{ text: body, citation: 0 }]
  return body.split(/\n\s*\n/).map((text) => {
    const marker = text.match(/\s*\[(\d+)\]\s*$/)
    const citation = Number(marker?.[1])
    return marker && props.result.citations?.[citation - 1]?.cited
      ? { text: text.slice(0, marker.index), citation }
      : { text, citation: 0 }
  })
})
const sources = computed(() =>
  (props.result.public_sources || [])
    .map((source) => ({ title: source.title, url: publicSourceUrl(source.url) }))
    .filter((source) => source.url),
)

/**
 * 打开正文编号对应的引文并将键盘焦点移至其标题。
 * @param number - 正文中的引用编号。
 */
function openCitation(number: number): void {
  const source = answerRoot.value?.querySelector<HTMLDetailsElement>(
    `details[data-citation="${number}"]`,
  )
  if (source) {
    source.open = true
    source.querySelector('summary')?.focus()
  }
}
</script>

<template>
  <section ref="answerRoot" class="knowledge-answer" aria-label="答复与依据">
    <div class="knowledge-answer-heading">
      <h3>
        {{ answerBasisLabel(result.answer_basis) || (result.data ? '经营指标答复' : '问答结果') }}
      </h3>
      <span v-if="result.cache_hit" class="knowledge-meta">复用已核验答复</span>
    </div>
    <template v-for="(paragraph, index) in paragraphs" :key="index">
      <MarkdownReport
        v-if="paragraph.text"
        :content="paragraph.text"
        :id-prefix="'answer-' + taskId + '-' + index"
        :show-outline="false"
        compact
      />
      <button
        v-if="paragraph.citation"
        type="button"
        class="knowledge-citation-link"
        :aria-label="'展开引用 ' + paragraph.citation"
        @click="openCitation(paragraph.citation)"
      >
        [{{ paragraph.citation }}] 查看引文
      </button>
    </template>
    <div v-if="result.missing?.length || result.guidance" class="knowledge-guidance">
      <strong>{{ result.missing?.length ? '待核实事项' : '使用说明' }}</strong>
      <ul v-if="result.missing?.length">
        <li v-for="(item, index) in result.missing" :key="index">{{ item }}</li>
      </ul>
      <p v-if="result.guidance">{{ result.guidance }}</p>
    </div>
    <section v-if="sources.length" class="knowledge-sources" aria-label="公开资料来源">
      <h4>公开资料来源</h4>
      <a
        v-for="source in sources"
        :key="source.url"
        :href="source.url"
        rel="noopener noreferrer"
        target="_blank"
      >
        {{ source.title }} ↗
      </a>
    </section>
    <section v-for="group in groups" :key="group.label" class="knowledge-sources">
      <h4>
        {{ group.label }} <small>{{ group.entries.length }}</small>
      </h4>
      <details
        v-for="{ citation, number } in group.entries"
        :key="citation.chunk_id"
        :data-citation="number"
      >
        <summary>
          <strong>[{{ number }}] {{ citation.title }}</strong>
          <span
            >V{{ citation.version
            }}<template v-if="citation.page"> · 第 {{ citation.page }} 页</template></span
          >
        </summary>
        <blockquote>{{ citation.quote }}</blockquote>
        <el-button link type="primary" @click="emit('openDoc', citation.document_id)"
          >打开授权文档</el-button
        >
      </details>
    </section>
    <details v-if="result.specialists?.length && !result.cache_hit" class="knowledge-specialists">
      <summary>处理角色</summary>
      <ul>
        <li v-for="expert in result.specialists" :key="expert.name">
          {{ expert.display_name || expert.name }} · {{ expert.count }} 条结论
        </li>
      </ul>
    </details>
  </section>
</template>
