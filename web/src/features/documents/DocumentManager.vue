<template>
  <section v-if="libraryVisible" class="panel">
    <div class="section-head">
      <div>
        <h3>知识文档</h3>
        <p class="section-sub">
          展示当前权限范围内的企业文档，支持按标题检索、查看密级及提交访问申请。
        </p>
      </div>
      <div class="toolbar">
        <el-input
          v-model="search"
          :maxlength="200"
          placeholder="搜索文档标题..."
          clearable
          class="doc-search-input"
        />
        <el-button v-if="user.capabilities.permissions" plain @click="permissionIdOpen = true">
          按编号查权限
        </el-button>
        <el-button plain @click="accessOpen = true">申请阅读权限</el-button>
        <el-button v-if="user.capabilities.write" type="primary" @click="newDoc">
          上传文档
        </el-button>
      </div>
    </div>

    <el-table :data="search ? searchResults : documents" empty-text="当前权限范围内暂无文档">
      <el-table-column prop="title" label="文档标题" min-width="240">
        <template #default="{ row }">
          <div class="doc-title-cell">
            <strong>{{ row.title }}</strong>
            <small class="muted-line">文档 ID：{{ row.id }}</small>
          </div>
        </template>
      </el-table-column>

      <el-table-column label="可见范围" min-width="150">
        <template #default="{ row }">
          <span class="org-chip">
            {{ row.scope === 'company' ? '全企业' : row.node_name || row.node_id }}
          </span>
        </template>
      </el-table-column>

      <el-table-column label="密级" width="110">
        <template #default="{ row }">
          <span class="level-pill" :class="`level-${row.level}`">
            {{ levelText(row.level) }}
          </span>
        </template>
      </el-table-column>

      <el-table-column label="状态" width="110">
        <template #default="{ row }">
          <span class="status-pill" :class="stateClass(row.state)">
            <span class="status-dot" />
            {{ stateText(row.state) }}
          </span>
        </template>
      </el-table-column>

      <el-table-column label="操作" width="110" align="right">
        <template #default="{ row }">
          <el-button size="small" type="primary" plain @click="openDocument(row.id)">
            查看
          </el-button>
        </template>
      </el-table-column>
    </el-table>
  </section>

  <el-dialog
    :key="detail?.id"
    :model-value="docOpen"
    :title="detail?.title || '文档详情'"
    width="760px"
    @update:model-value="closeDocument"
  >
    <template v-if="detail">
      <div class="detail-meta">
        <span class="org-chip">
          {{ detail.scope === 'company' ? '全企业' : detail.node_name || detail.node_id }}
        </span>
        <span class="level-pill" :class="`level-${detail.level}`">
          {{ levelText(detail.level) }}
        </span>
        <el-tag size="small" :type="stateTag(detail.state)">{{ stateText(detail.state) }}</el-tag>
        <span>版本 V{{ detail.current_version }}</span>
        <span>权限版本 #{{ detail.acl_version }}</span>
      </div>
      <el-alert v-if="detail.error" type="error" :title="detail.error" :closable="false" />
      <el-tabs v-model="detailTab" class="document-tabs">
        <el-tab-pane label="原始文档" name="original">
          <article
            v-if="detail.mime_type === 'text/markdown' && (originalText || detail.body)"
            ref="markdownHost"
            class="document-markdown"
          />
          <template v-else-if="detail.mime_type === 'application/pdf' && detail.has_original">
            <div v-if="pdfTotal" class="pdf-viewer">
              <div class="pdf-toolbar">
                <el-button size="small" :disabled="pdfPage <= 1" @click="pdfPage -= 1">
                  上一页
                </el-button>
                <span>第 {{ pdfPage }} / {{ pdfTotal }} 页</span>
                <el-button size="small" :disabled="pdfPage >= pdfTotal" @click="pdfPage += 1">
                  下一页
                </el-button>
                <el-link :href="originalUrl" target="_blank" type="primary"> 在新窗口打开 </el-link>
              </div>
              <img class="document-pdf" :src="pdfPageUrl" :alt="`PDF 第 ${pdfPage} 页`" />
            </div>
            <iframe v-else class="document-pdf" :src="originalUrl" title="PDF 原件" />
            <p class="chunk-note">当前按 PDF 原件版面展示，使用上一页 / 下一页浏览全部页面。</p>
          </template>
          <article v-else-if="docxReady" ref="docxHost" class="document-markdown" />
          <pre v-else-if="originalText || detail.body" class="document-body">{{
            originalText || detail.body
          }}</pre>
          <el-empty v-else description="当前仅可查看文档基本信息，暂未获准阅读正文" />
        </el-tab-pane>
        <el-tab-pane label="切片与词嵌入" name="chunks">
          <div v-if="chunkError" class="chunk-summary">
            <el-alert type="error" :title="chunkError" :closable="false" />
            <el-button size="small" @click="loadChunks">重新读取</el-button>
          </div>
          <div v-else-if="!chunkIndex" class="chunk-summary">正在读取切片与词嵌入…</div>
          <template v-else>
            <div class="chunk-summary">
              <el-tag size="small">V{{ chunkIndex.version }}</el-tag>
              <span>共 {{ chunkIndex.total }} 个切片</span>
              <span>已向量化 {{ chunkIndex.embedded }}/{{ chunkIndex.total }}</span>
              <span v-if="chunkIndex.dim">{{ chunkIndex.dim }} 维</span>
              <span v-if="chunkIndex.model">模型 {{ chunkIndex.model }}</span>
              <span v-else-if="chunkIndex.embedded">嵌入服务暂不可用，展示已入库向量</span>
            </div>
            <p class="chunk-note">
              向量输入为「文档标题 + 切片正文」，切片文本与原文遵循同一脱敏规则。
            </p>
            <el-empty
              v-if="!chunkIndex.items.length"
              description="当前版本尚未生成切片：解析与向量编码在发布审核前完成"
            />
            <div v-else class="chunk-list">
              <article v-for="chunk in chunkIndex.items" :key="chunk.id" class="chunk-card">
                <header>
                  <strong>#{{ chunk.ordinal + 1 }}</strong>
                  <span>第 {{ chunk.page }} 页</span>
                  <span>{{ chunk.chars }} 字</span>
                  <el-tag size="small" :type="chunk.embedded ? 'success' : 'info'">
                    {{ chunk.embedded ? '已向量化' : '未向量化' }}
                  </el-tag>
                  <span v-if="chunk.embedded">L2 范数 {{ chunk.norm.toFixed(3) }}</span>
                </header>
                <pre class="chunk-text">{{ chunk.text }}</pre>
                <div v-if="chunk.embedded" class="vector-strip">
                  <span
                    v-for="(value, index) in chunk.preview"
                    :key="index"
                    class="vector-cell"
                    :style="{ background: vectorColor(value) }"
                    :title="`维度 ${index + 1}：${value}`"
                  />
                </div>
              </article>
            </div>
          </template>
        </el-tab-pane>
      </el-tabs>
      <div class="dialog-actions">
        <el-link
          v-if="detail.can_download"
          :href="'/api/documents/' + encodeURIComponent(detail.id) + '/download'"
          target="_blank"
          type="primary"
        >
          下载原文件
        </el-link>
        <el-button v-if="detail.can_write" @click="editDoc">编辑文档</el-button>
        <el-button v-if="user.capabilities.permissions" @click="showPolicy">权限与密级</el-button>
        <el-button v-if="user.capabilities.permissions" @click="showGrant">人员授权</el-button>
        <el-button v-if="detail.body" @click="showVersions">历史版本</el-button>
        <el-button v-if="detail.can_write" type="danger" plain @click="deleteDoc">
          删除文档
        </el-button>
      </div>
    </template>
  </el-dialog>

  <el-dialog
    v-model="docEdit"
    destroy-on-close
    :title="docForm.id ? '编辑文档' : '上传 / 新建文档'"
    width="680px"
  >
    <div class="dialog-lead">
      新增或修改文档后将进入审核流程，审核通过后可供相应权限成员检索查阅。
    </div>
    <el-form label-position="top">
      <el-form-item label="文档标题">
        <el-input v-model="docForm.title" placeholder="请输入文档标题" />
      </el-form-item>
      <div v-if="!docForm.id" class="form-columns">
        <el-form-item label="可见范围">
          <el-select v-model="docForm.scope">
            <el-option
              v-if="user.is_boss || user.capabilities.system"
              label="全企业"
              value="company"
            />
            <el-option label="指定部门" value="org" />
          </el-select>
        </el-form-item>
        <el-form-item v-if="docForm.scope === 'org'" label="所属部门">
          <el-select v-model="docForm.node_id">
            <el-option
              v-for="organization in organizations"
              :key="organization.id"
              :label="organization.name"
              :value="organization.id"
            />
          </el-select>
        </el-form-item>
      </div>
      <div v-if="!docForm.id" class="form-columns">
        <el-form-item label="文档密级">
          <el-select v-model="docForm.level">
            <el-option label="公开 (L1)" :value="1" />
            <el-option label="内部 (L2)" :value="2" />
            <el-option label="机密 (L3)" :value="3" />
          </el-select>
        </el-form-item>
        <el-form-item label="上传本地文件（支持 Markdown / TXT / Word / PDF）">
          <input type="file" @change="picked" />
        </el-form-item>
      </div>
      <el-form-item v-if="!selectedFile" label="文档正文">
        <el-input
          v-model="docForm.body"
          type="textarea"
          :rows="8"
          placeholder="请输入文档正文，或选择本地文件上传"
        />
      </el-form-item>
    </el-form>
    <template #footer>
      <el-button @click="docEdit = false">取消</el-button>
      <el-button type="primary" :loading="busy" @click="saveDoc">保存提交</el-button>
    </template>
  </el-dialog>

  <el-dialog v-model="policyOpen" title="修改文档权限与密级" width="540px">
    <div class="dialog-lead">
      修改文档密级、可见部门范围或 AI 使用授权需提交企业负责人审批通过后生效。
    </div>
    <el-form label-position="top">
      <div class="form-columns">
        <el-form-item label="可见范围">
          <el-select v-model="policyForm.scope">
            <el-option label="全企业" value="company" />
            <el-option label="指定部门" value="org" />
          </el-select>
        </el-form-item>
        <el-form-item label="文档密级">
          <el-select v-model="policyForm.level">
            <el-option label="公开 (L1)" :value="1" />
            <el-option label="内部 (L2)" :value="2" />
            <el-option label="机密 (L3)" :value="3" />
          </el-select>
        </el-form-item>
      </div>
      <el-form-item v-if="policyForm.scope === 'org'" label="所属部门">
        <el-select v-model="policyForm.node_id">
          <el-option
            v-for="organization in organizations"
            :key="organization.id"
            :label="organization.name"
            :value="organization.id"
          />
        </el-select>
      </el-form-item>
      <el-checkbox v-model="policyForm.ai_allowed">允许授权人员用于 AI 问答分析</el-checkbox>
      <el-checkbox v-model="policyForm.download_allowed">允许具备权限的成员下载原文件</el-checkbox>
    </el-form>
    <template #footer>
      <el-button @click="policyOpen = false">取消</el-button>
      <el-button type="primary" :loading="busy" @click="savePolicy">提交审批</el-button>
    </template>
  </el-dialog>

  <el-dialog v-model="grantOpen" title="文档人员授权管理" width="600px">
    <div v-if="currentGrants.length" class="subagents-roster-box" style="margin-top: 0">
      <div class="roster-header">
        <strong>当前有效授权</strong>
      </div>
      <div v-for="g in currentGrants" :key="g.user_id" class="scope-card">
        <span>{{ g.display_name }}</span>
        <strong>
          {{ g.effect === 'deny' ? '禁止访问' : '允许阅读' }}
          {{ g.can_download ? ' · 可下载' : '' }}
          {{ g.expires_at ? ' · 有效期至 ' + g.expires_at : '' }}
        </strong>
      </div>
    </div>
    <el-form label-position="top">
      <el-form-item label="选择成员">
        <el-select v-model="grantForm.user_id" filterable placeholder="请选择企业成员">
          <el-option
            v-for="u in users"
            :key="u.id"
            :label="`${u.display_name} (${u.username})`"
            :value="u.id"
          />
        </el-select>
      </el-form-item>
      <div class="form-columns">
        <el-form-item label="授权类型">
          <el-select v-model="grantForm.effect">
            <el-option label="允许阅读" value="allow" />
            <el-option label="禁止访问" value="deny" />
          </el-select>
        </el-form-item>
        <el-form-item label="有效期至（可选）">
          <el-date-picker
            v-model="grantForm.expires_at"
            type="datetime"
            style="width: 100%"
            placeholder="留空表示长期有效"
          />
        </el-form-item>
      </div>
      <el-checkbox v-model="grantForm.can_download">允许下载原文件</el-checkbox>
      <el-checkbox v-model="grantForm.revoke">收回该成员的文档授权</el-checkbox>
    </el-form>
    <template #footer>
      <el-button @click="grantOpen = false">取消</el-button>
      <el-button type="primary" :loading="busy" @click="saveGrant">提交审批</el-button>
    </template>
  </el-dialog>

  <el-dialog v-model="historyOpen" title="历史版本记录" width="660px">
    <el-collapse>
      <el-collapse-item
        v-for="v in history"
        :key="v.version"
        :title="`V${v.version} · ${v.title} (${v.created_at})`"
      >
        <pre class="quote-body">{{ v.body }}</pre>
      </el-collapse-item>
    </el-collapse>
  </el-dialog>

  <el-dialog v-model="accessOpen" title="申请文档阅读权限" width="440px">
    <div class="dialog-lead">
      输入管理员或同事提供的文档 ID 后可直接发起阅读授权申请，申请将流转至对应负责人审批。
    </div>
    <el-form label-position="top">
      <el-form-item label="文档 ID">
        <el-input v-model="accessId" placeholder="请输入文档 ID" />
      </el-form-item>
    </el-form>
    <template #footer>
      <el-button @click="accessOpen = false">取消</el-button>
      <el-button type="primary" :loading="busy" @click="requestAccess">提交申请</el-button>
    </template>
  </el-dialog>

  <el-dialog v-model="permissionIdOpen" title="按文档 ID 管理权限" width="440px">
    <el-form label-position="top">
      <el-form-item label="文档 ID">
        <el-input v-model="permissionId" placeholder="请输入文档 ID" />
      </el-form-item>
    </el-form>
    <template #footer>
      <el-button @click="permissionIdOpen = false">取消</el-button>
      <el-button type="primary" :loading="busy" @click="manageById">查询配置</el-button>
    </template>
  </el-dialog>
</template>

<script setup lang="ts">
import { computed, onBeforeUnmount, reactive, ref, watch, watchEffect } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { levelText, stateTag, stateText } from '../../shared/labels'
import { useWorkspaceAction } from '../../shared/useWorkspaceAction'
import { isAdmin } from '../administration/types'
import type { ManagedUser, Organization, User } from '../administration/types'
import { renderMarkdown } from './markdown'
import type {
  DocumentChunkIndex,
  DocumentListItem,
  DocumentRecord,
  DocumentVersion,
  Grant,
} from './types'
import type { ApiClient, Items } from '../../shared/apiTypes'

const props = defineProps<{
  libraryVisible: boolean
  api: ApiClient
  refreshWorkspace: () => Promise<void>
  documents: DocumentListItem[]
  organizations: Organization[]
  rootOrganizationId: string
  users: ManagedUser[]
  user: User
}>()

const { busy, action } = useWorkspaceAction()
const search = ref('')
const searchResults = ref<DocumentListItem[]>([])
let selection = 0
const docOpen = ref(false)
const detail = ref<DocumentRecord | null>(null)
const docEdit = ref(false)
const docForm = reactive({
  id: '',
  title: '',
  body: '',
  scope: 'org',
  node_id: '',
  level: 2,
  version: 1,
})
const selectedFile = ref<File | null>(null)

const policyOpen = ref(false)
const policyForm = reactive({
  scope: 'org',
  node_id: '',
  level: 2,
  ai_allowed: false,
  download_allowed: false,
})

const grantOpen = ref(false)
const currentGrants = ref<Grant[]>([])
const grantForm = reactive({
  user_id: '',
  effect: 'allow',
  can_download: false,
  expires_at: '',
  revoke: false,
})

const historyOpen = ref(false)
const detailTab = ref<'original' | 'chunks'>('original')
const chunkIndex = ref<DocumentChunkIndex | null>(null)
const chunkError = ref('')
const markdownHost = ref<HTMLElement | null>(null)
const docxHost = ref<HTMLElement | null>(null)
const docxHtml = ref('')
const docxReady = ref(false)
const originalText = ref('')
const DOCX_MIME = 'application/vnd.openxmlformats-officedocument.wordprocessingml.document'

watchEffect(() => {
  if (markdownHost.value) {
    markdownHost.value.innerHTML = renderMarkdown(originalText.value || detail.value?.body || '')
  }
})

watchEffect(() => {
  if (docxHost.value) {
    docxHost.value.innerHTML = docxHtml.value
  }
})
const originalUrl = computed(() =>
  detail.value ? '/api/documents/' + encodeURIComponent(detail.value.id) + '/original' : '',
)
const pdfPage = ref(1)
const pdfTotal = ref(0)
const pdfPageUrl = computed(() =>
  detail.value
    ? '/api/documents/' + encodeURIComponent(detail.value.id) + '/pages/' + pdfPage.value + '.png'
    : '',
)
const history = ref<DocumentVersion[]>([])
const accessOpen = ref(false)
const accessId = ref('')
const permissionIdOpen = ref(false)
const permissionId = ref('')

watch(
  () => [search.value, props.libraryVisible, props.documents] as const,
  async ([query, visible], _previous, onCleanup) => {
    searchResults.value = []
    if (!query || !visible) return
    let current = true
    onCleanup(() => {
      current = false
    })
    await action(async () => {
      const page = await props.api<Items<DocumentListItem>>(
        '/documents?q=' + encodeURIComponent(query),
      )
      if (current) searchResults.value = page.items
    })
  },
)

/** 关闭文档及其附属窗口，作废读取中的请求并清空正文和历史版本。 */
function closeDocument(): void {
  selection++
  docOpen.value = false
  docEdit.value = false
  policyOpen.value = false
  grantOpen.value = false
  historyOpen.value = false
  detail.value = null
  detailTab.value = 'original'
  chunkIndex.value = null
  chunkError.value = ''
  pdfPage.value = 1
  pdfTotal.value = 0
  docxHtml.value = ''
  docxReady.value = false
  originalText.value = ''
  selectedFile.value = null
  docForm.id = docForm.title = docForm.body = ''
  history.value = []
  currentGrants.value = []
}

/** 复核正在阅读的文档，撤权时同时移除编辑草稿、授权和历史版本。 */
async function refreshDetail(): Promise<void> {
  const document = detail.value
  if (!docOpen.value || !document) return
  try {
    const snapshot = await props.api<DocumentRecord>(
      '/documents/' + encodeURIComponent(document.id),
    )
    if (detail.value !== document) return
    const versionChanged = snapshot.current_version !== document.current_version
    if (
      snapshot.acl_version !== document.acl_version ||
      (document.can_write && !snapshot.can_write)
    ) {
      closeDocument()
      docOpen.value = true
    }
    detail.value = snapshot
    if (versionChanged) {
      chunkIndex.value = null
      chunkError.value = ''
      if (detailTab.value === 'chunks') void loadChunks()
    }
  } catch {
    if (detail.value === document) closeDocument()
  }
}

/** 读取当前文档版本的切片与词嵌入摘要，同一版本只请求一次。 */
async function loadChunks(): Promise<void> {
  const document = detail.value
  if (!document) return
  if (chunkIndex.value?.version === document.current_version && !chunkError.value) return
  const request = selection
  chunkError.value = ''
  await action(async () => {
    try {
      const index = await props.api<DocumentChunkIndex>(
        '/documents/' + encodeURIComponent(document.id) + '/chunks',
      )
      if (request !== selection || detail.value?.id !== document.id) return
      if (index.version !== detail.value.current_version) return
      chunkIndex.value = index
    } catch (error) {
      chunkError.value = error instanceof Error ? error.message : '切片读取失败，请稍后重试'
    }
  })
}

/**
 * 把归一化向量的分量映射为红蓝热力色：正负区分方向，绝对值决定深浅。
 * @param value - 向量的单个分量，取值约在 -1 到 1 之间。
 * @returns 可直接用于 CSS 背景色的 rgba 字符串。
 */
function vectorColor(value: number): string {
  const magnitude = Math.min(1, Math.abs(value))
  const alpha = (0.12 + magnitude * 0.88).toFixed(2)
  return value >= 0 ? `rgba(64, 158, 255, ${alpha})` : `rgba(245, 108, 108, ${alpha})`
}

/** 读取 PDF 原件总页数；仅在老板查看带原件的 PDF 时请求一次。 */
async function loadPdfMeta(): Promise<void> {
  const document = detail.value
  if (!document || document.mime_type !== 'application/pdf') return
  if (!document.has_original || pdfTotal.value) return
  const request = selection
  await action(async () => {
    try {
      const page = await props.api<{ total: number }>(
        '/documents/' + encodeURIComponent(document.id) + '/pages',
      )
      if (request !== selection || detail.value?.id !== document.id) return
      pdfTotal.value = page.total
    } catch {
      pdfTotal.value = 0
    }
  })
}

/** 读取并渲染 DOCX 原件；仅在老板查看带原件的 DOCX 时请求一次。 */
async function loadDocx(): Promise<void> {
  const document = detail.value
  if (!document || document.mime_type !== DOCX_MIME) return
  if (!document.has_original || docxReady.value) return
  const request = selection
  await action(async () => {
    try {
      const response = await fetch('/api/documents/' + encodeURIComponent(document.id) + '/docx', {
        credentials: 'same-origin',
      })
      if (!response.ok) return
      const html = await response.text()
      if (request !== selection || detail.value?.id !== document.id) return
      docxHtml.value = html
      docxReady.value = true
    } catch {
      docxReady.value = false
    }
  })
}

/** 读取 Markdown/TXT 归档原件，按原始权限展示未脱敏内容。 */
async function loadOriginalText(): Promise<void> {
  const document = detail.value
  if (!document || !document.has_original) return
  if (!['text/markdown', 'text/plain'].includes(document.mime_type || '')) return
  if (originalText.value) return
  const request = selection
  await action(async () => {
    try {
      const response = await fetch(
        '/api/documents/' + encodeURIComponent(document.id) + '/original',
        { credentials: 'same-origin' },
      )
      if (!response.ok) return
      const text = await response.text()
      if (request !== selection || detail.value?.id !== document.id) return
      originalText.value = text
    } catch {
      originalText.value = ''
    }
  })
}

watch(
  () => [detail.value?.id, detailTab.value] as const,
  ([id, tab]) => {
    if (id && tab === 'original') {
      void loadPdfMeta()
      void loadDocx()
      void loadOriginalText()
    }
  },
)

watch(detailTab, (tab) => {
  if (tab === 'chunks') void loadChunks()
})

watch(docOpen, (open, _previous, onCleanup) => {
  if (!open) return
  const timer = setInterval(refreshDetail, 2500)
  onCleanup(() => clearInterval(timer))
})

onBeforeUnmount(closeDocument)

/**
 * 根据文档状态映射语义视觉样式类。
 * @param state - 文档状态编码。
 * @returns 样式类名。
 */
function stateClass(state: string): string {
  if (state === 'published' || state === 'approved') return 'status-active'
  if (state === 'review' || state === 'pending') return 'status-pending'
  return 'status-disabled'
}

/**
 * 查看授权文档详情。
 * @param id - 文档 ID。
 */
async function openDocument(id: string): Promise<void> {
  closeDocument()
  const request = selection
  await action(async () => {
    const document = await props.api<DocumentRecord>('/documents/' + encodeURIComponent(id))
    if (selection !== request) return
    detail.value = document
    docOpen.value = true
  })
}

/**
 * 初始化新建文档表单。
 */
function newDoc(): void {
  closeDocument()
  const companyDefault = isAdmin(props.user) && !props.user.is_boss
  Object.assign(docForm, {
    id: '',
    title: '',
    body: '',
    scope: companyDefault ? 'company' : 'org',
    node_id: companyDefault
      ? props.rootOrganizationId
      : props.user.memberships[0]?.node_id || props.rootOrganizationId,
    level: companyDefault ? 1 : 2,
    version: 1,
  })
  docEdit.value = true
}

/**
 * 载入现有文档版本准备编辑。
 */
function editDoc(): void {
  if (!detail.value) return
  Object.assign(docForm, {
    id: detail.value.id,
    title: detail.value.title || '',
    body: detail.value.body || '',
    scope: detail.value.scope,
    node_id: detail.value.node_id,
    level: detail.value.level,
    version: detail.value.current_version,
  })
  selectedFile.value = null
  docEdit.value = true
}

/**
 * 保存新建文档或更新版本。
 */
async function saveDoc(): Promise<void> {
  const request = selection
  await action(async () => {
    const nodeId = docForm.scope === 'company' ? props.rootOrganizationId : docForm.node_id
    if (docForm.id) {
      await props.api('/documents/' + encodeURIComponent(docForm.id), 'PUT', {
        title: docForm.title,
        body: docForm.body,
        version: docForm.version,
      })
    } else if (selectedFile.value) {
      const form = new FormData()
      form.append('file', selectedFile.value)
      form.append('title', docForm.title)
      form.append('scope', docForm.scope)
      form.append('node_id', nodeId)
      form.append('level', String(docForm.level))
      await props.api('/documents/upload', 'POST', form)
    } else {
      await props.api('/documents', 'POST', {
        title: docForm.title,
        body: docForm.body,
        node_id: nodeId,
        scope: docForm.scope,
        level: docForm.level,
      })
    }
    if (request === selection) closeDocument()
    ElMessage.success('文档已保存，等待审核发布')
    await props.refreshWorkspace()
  })
}

/**
 * 读取选择的文件对象。
 * @param e - 文件选择器事件。
 */
function picked(e: Event): void {
  selectedFile.value = (e.target as HTMLInputElement).files?.[0] || null
}

/**
 * 确认后删除当前文档。
 */
async function deleteDoc(): Promise<void> {
  const document = detail.value
  if (!document?.can_write) return
  const request = selection
  await action(async () => {
    await ElMessageBox.confirm('删除后，引用该文档的历史答案将受限展示，确认删除？', '删除文档', {
      confirmButtonText: '删除',
      cancelButtonText: '取消',
      type: 'warning',
    })
    if (request !== selection) return
    await props.api('/documents/' + encodeURIComponent(document.id), 'DELETE')
    if (request === selection) closeDocument()
    ElMessage.success('文档已删除')
    await props.refreshWorkspace()
  })
}

/**
 * 打开文档权限与密级设置弹窗。
 */
function showPolicy(): void {
  if (!detail.value) return
  Object.assign(policyForm, {
    scope: detail.value.scope,
    node_id: detail.value.node_id,
    level: detail.value.level,
    ai_allowed: detail.value.ai_allowed,
    download_allowed: detail.value.download_allowed,
  })
  policyOpen.value = true
}

/**
 * 提交文档权限调整申请。
 */
async function savePolicy(): Promise<void> {
  const document = detail.value
  if (!document) return
  const request = selection
  await action(async () => {
    await props.api('/documents/' + encodeURIComponent(document.id) + '/policy', 'POST', {
      ...policyForm,
      node_id: policyForm.scope === 'company' ? props.rootOrganizationId : policyForm.node_id,
    })
    if (request === selection) policyOpen.value = false
    ElMessage.success('权限变更已提交，等待企业负责人审批')
    await props.refreshWorkspace()
  })
}

/**
 * 打开单文档人员授权窗口。
 */
async function showGrant(): Promise<void> {
  const document = detail.value
  if (!document) return
  const request = selection
  await action(async () => {
    const page = await props.api<Items<Grant>>(
      '/documents/' + encodeURIComponent(document.id) + '/grants',
    )
    if (request !== selection) return
    currentGrants.value = page.items
    Object.assign(grantForm, {
      user_id: '',
      effect: 'allow',
      can_download: false,
      expires_at: '',
      revoke: false,
    })
    grantOpen.value = true
  })
}

/**
 * 提交单文档人员授权申请。
 */
async function saveGrant(): Promise<void> {
  const document = detail.value
  if (!document) return
  const request = selection
  await action(async () => {
    await props.api('/documents/' + encodeURIComponent(document.id) + '/grants', 'POST', {
      ...grantForm,
      expires_at: grantForm.expires_at ? new Date(grantForm.expires_at).toISOString() : null,
    })
    if (request === selection) grantOpen.value = false
    ElMessage.success('授权申请已提交审批')
    await props.refreshWorkspace()
  })
}

/**
 * 加载获准查阅的历史版本列表。
 */
async function showVersions(): Promise<void> {
  const document = detail.value
  if (!document) return
  const request = selection
  await action(async () => {
    const page = await props.api<Items<DocumentVersion>>(
      '/documents/' + encodeURIComponent(document.id) + '/versions',
    )
    if (request !== selection) return
    history.value = page.items
    historyOpen.value = true
  })
}

/**
 * 按文档 ID 提交阅读权限申请。
 */
async function requestAccess(): Promise<void> {
  await action(async () => {
    await props.api('/documents/' + encodeURIComponent(accessId.value) + '/request-access', 'POST')
    accessOpen.value = false
    ElMessage.success('访问申请已提交')
    await props.refreshWorkspace()
  })
}

/**
 * 管理员按文档 ID 查询并配置权限策略。
 */
async function manageById(): Promise<void> {
  closeDocument()
  const request = selection
  await action(async () => {
    const document = await props.api<DocumentRecord>(
      '/documents/' + encodeURIComponent(permissionId.value) + '/permission-status',
    )
    if (request !== selection || !permissionIdOpen.value) return
    detail.value = document
    permissionIdOpen.value = false
    showPolicy()
  })
}

defineExpose({ openDocument })
</script>

<style scoped>
.document-tabs {
  margin-top: 4px;
}

.chunk-summary {
  display: flex;
  flex-wrap: wrap;
  gap: 10px;
  align-items: center;
  margin-bottom: 8px;
  font-size: 13px;
  color: var(--el-text-color-regular);
}

.chunk-note {
  margin: 0 0 10px;
  font-size: 12px;
  color: var(--el-text-color-secondary);
}

.chunk-list {
  display: flex;
  flex-direction: column;
  gap: 10px;
  max-height: 52vh;
  overflow: auto;
}

.chunk-card {
  padding: 10px 12px;
  border: 1px solid var(--el-border-color-light);
  border-radius: 8px;
}

.chunk-card header {
  display: flex;
  flex-wrap: wrap;
  gap: 10px;
  align-items: center;
  margin-bottom: 8px;
  font-size: 12px;
  color: var(--el-text-color-secondary);
}

.chunk-text {
  margin: 0;
  max-height: 9em;
  overflow: auto;
  font-size: 13px;
  line-height: 1.6;
  white-space: pre-wrap;
}

.vector-strip {
  display: flex;
  gap: 2px;
  margin-top: 8px;
}

.vector-cell {
  width: 8px;
  height: 14px;
  border-radius: 2px;
  background: var(--el-fill-color-light);
}

.document-markdown {
  max-height: 60vh;
  overflow: auto;
  font-size: 14px;
  line-height: 1.7;
}

.document-markdown :deep(h1) {
  margin: 0 0 12px;
  font-size: 20px;
}

.document-markdown :deep(h2) {
  margin: 18px 0 8px;
  font-size: 17px;
}

.document-markdown :deep(table) {
  width: 100%;
  margin: 12px 0;
  border-collapse: collapse;
}

.document-markdown :deep(th),
.document-markdown :deep(td) {
  padding: 6px 8px;
  text-align: left;
  border: 1px solid var(--el-border-color);
}

.document-markdown :deep(blockquote) {
  margin: 12px 0;
  padding: 8px 12px;
  color: var(--el-text-color-secondary);
  background: var(--el-fill-color-light);
  border-left: 3px solid var(--el-border-color);
}

.document-markdown :deep(pre) {
  padding: 10px 12px;
  overflow: auto;
  background: var(--el-fill-color-light);
  border-radius: 6px;
}

.document-markdown :deep(code) {
  font-family: var(--el-font-family-monospace, monospace);
}

.pdf-toolbar {
  display: flex;
  gap: 12px;
  align-items: center;
  margin-bottom: 8px;
  font-size: 13px;
  color: var(--el-text-color-secondary);
}

.document-markdown :deep(.docx-meta) {
  padding: 4px 0;
  margin-bottom: 8px;
  font-size: 12px;
  color: var(--el-text-color-secondary);
  border-bottom: 1px dashed var(--el-border-color);
}

.document-pdf {
  display: block;
  width: 100%;
  height: 62vh;
  object-fit: contain;
  background: var(--el-fill-color-light);
  border: 0;
}
</style>
