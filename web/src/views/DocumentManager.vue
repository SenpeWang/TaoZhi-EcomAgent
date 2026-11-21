<template>
  <section class="panel">
    <div class="section-head">
      <div>
        <h3>知识文档</h3>
        <p class="section-sub">
          展示当前权限范围内的企业文档，支持按标题检索、查看密级及提交访问申请。
        </p>
      </div>
      <div class="toolbar">
        <el-input
          v-model="localSearch"
          placeholder="搜索文档标题..."
          clearable
          class="doc-search-input"
          @input="onSearchInput"
        />
        <el-button v-if="canManagePermissions" plain @click="emit('openPermissionId')">
          按编号查权限
        </el-button>
        <el-button plain @click="emit('openRequestAccess')">申请阅读权限</el-button>
        <el-button v-if="canWrite" type="primary" @click="emit('newDoc')"> 上传文档 </el-button>
      </div>
    </div>

    <el-table :data="documents" empty-text="当前权限范围内暂无文档">
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
          <el-button size="small" type="primary" plain @click="emit('openDoc', row.id)">
            查看
          </el-button>
        </template>
      </el-table-column>
    </el-table>
  </section>
</template>

<script setup lang="ts">
import { ref, watch } from 'vue'
import type { DocumentListItem } from '../types'
import { levelText, stateText } from '../labels'

const props = defineProps<{
  documents: DocumentListItem[]
  search: string
  canWrite: boolean
  canManagePermissions: boolean
}>()

const emit = defineEmits<{
  'update:search': [value: string]
  reload: []
  openPermissionId: []
  openRequestAccess: []
  newDoc: []
  openDoc: [id: string]
}>()

const localSearch = ref(props.search)

watch(
  /**
   * 监听外部检索词变化并同步至本地输入框。
   * @returns 当前搜索关键词。
   */
  () => props.search,
  /**
   * 更新本地检索词状态。
   * @param value - 外部传入的搜索字符串。
   */
  (value) => {
    localSearch.value = value
  },
)

/**
 * 同步检索关键词并触发服务端列表刷新。
 * @param value - 输入框最新内容。
 */
function onSearchInput(value: string): void {
  emit('update:search', value)
  emit('reload')
}

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
</script>
