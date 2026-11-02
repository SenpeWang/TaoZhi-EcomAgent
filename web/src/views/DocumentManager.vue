<script setup lang="ts">
import { computed, ref } from 'vue'
import type { Capabilities, DocumentListItem } from '../types'

const props = defineProps<{
  docs: DocumentListItem[]
  caps: Partial<Capabilities>
}>()

const emit = defineEmits<{
  (e: 'openDoc', docId: string): void
  (e: 'openPermissionId'): void
  (e: 'openAccess'): void
}>()

const searchTerm = ref('')

const levelMap: Record<number, string> = {
  1: 'L1 公开',
  2: 'L2 内部',
  3: 'L3 机密',
}

const stateMap: Record<string, string> = {
  published: '已发布',
  review: '等待审核',
  draft: '草稿',
  failed: '处理失败',
}

/**
 * 格式化密级标签。
 * @param l - 密级数值。
 * @returns 密级中文描述。
 */
function level(l: number) {
  return levelMap[l] || 'L' + l
}

/**
 * 格式化状态标签。
 * @param s - 状态码。
 * @returns 状态中文描述。
 */
function state(s: string) {
  return stateMap[s] || s
}

const filteredDocs = computed(() => {
  if (!searchTerm.value.trim()) return props.docs
  const term = searchTerm.value.trim().toLowerCase()
  return props.docs.filter((d) => (d.title || '').toLowerCase().includes(term))
})
</script>

<template>
  <section class="panel">
    <div class="section-heading">
      <el-input v-model="searchTerm" class="search-box" placeholder="搜索可访问资料" clearable />
      <div>
        <el-button v-if="caps.system" @click="emit('openPermissionId')">按编号配置权限</el-button>
        <el-button @click="emit('openAccess')">申请资料访问</el-button>
      </div>
    </div>

    <el-table :data="filteredDocs">
      <el-table-column label="资料名称" min-width="300">
        <template #default="{ row }">
          <el-button text type="primary" class="table-question" @click="emit('openDoc', row.id)">
            {{ row.title }}
          </el-button>
        </template>
      </el-table-column>

      <el-table-column label="组织范围" prop="node_name" min-width="150" />

      <el-table-column label="密级" width="110">
        <template #default="{ row }">
          <el-tag :type="row.level === 3 ? 'danger' : row.level === 2 ? 'warning' : 'info'">
            {{ level(row.level) }}
          </el-tag>
        </template>
      </el-table-column>

      <el-table-column label="版本" width="80">
        <template #default="{ row }">第 {{ row.current_version }} 版</template>
      </el-table-column>

      <el-table-column label="状态" width="120">
        <template #default="{ row }">{{ state(row.state) }}</template>
      </el-table-column>

      <el-table-column label="操作" width="160">
        <template #default="{ row }">
          <el-button text @click="emit('openDoc', row.id)">查看</el-button>
          <el-button
            v-if="row.can_download"
            text
            tag="a"
            :href="'/api/documents/' + row.id + '/download'"
          >
            下载
          </el-button>
        </template>
      </el-table-column>
    </el-table>

    <el-empty v-if="!filteredDocs.length" description="暂无匹配资料" />
  </section>
</template>
