<template>
  <el-select
    :model-value="modelValue"
    aria-label="按发起人筛选"
    placeholder="搜索姓名或账号"
    filterable
    remote
    clearable
    :remote-method="searchOwners"
    @visible-change="(open: boolean) => open && searchOwners('')"
    @update:model-value="selectOwner"
  >
    <el-option label="全部用户（all）" value="all" />
    <el-option
      v-for="owner in owners"
      :key="owner.id"
      :label="
        owner.display_name ? owner.display_name + '（' + owner.username + '）' : owner.username
      "
      :value="owner.id"
    />
    <template v-if="ownerLoading || ownerError || nextOwnerCursor || !owners.length" #footer>
      <span v-if="ownerLoading" role="status">正在加载用户…</span>
      <div v-else-if="ownerError" class="owner-retry">
        <span role="alert">{{ ownerError }}</span>
        <el-button text type="primary" @click="loadOwners">重试</el-button>
      </div>
      <el-button v-else-if="nextOwnerCursor" text type="primary" @click="loadOwners"
        >加载更多用户</el-button
      >
      <span v-else>没有匹配的用户</span>
    </template>
  </el-select>
</template>

<script setup lang="ts">
import { ref } from 'vue'
import type { ApiReader, CursorPage, TraceOwner } from '../types'

const props = defineProps<{ modelValue: string; api: ApiReader }>()
const emit = defineEmits<{ (e: 'update:modelValue', value: string): void }>()
const owners = ref<TraceOwner[]>([])
const nextOwnerCursor = ref<string | null>(null)
const ownerLoading = ref(false)
const ownerError = ref('')
let ownerQuery = ''
let ownerRequestSequence = 0

/** 仅追加最新搜索词对应的成员。 */
async function loadOwners(): Promise<void> {
  const requestId = ++ownerRequestSequence
  const query = new URLSearchParams({ limit: '50' })
  if (ownerQuery) query.set('q', ownerQuery)
  if (nextOwnerCursor.value) query.set('cursor', nextOwnerCursor.value)
  ownerError.value = ''
  ownerLoading.value = true
  try {
    const page = await props.api<CursorPage<TraceOwner>>('/admin/traces/owners?' + query)
    if (requestId !== ownerRequestSequence) return
    owners.value = [...owners.value, ...page.items]
    nextOwnerCursor.value = page.next_cursor
  } catch (cause) {
    if (requestId === ownerRequestSequence) {
      ownerError.value = cause instanceof Error ? cause.message : '读取用户列表失败'
    }
  } finally {
    if (requestId === ownerRequestSequence) ownerLoading.value = false
  }
}

/**
 * 从当前租户的成员目录按姓名或账号重新搜索。
 * @param input - 管理员输入的姓名或账号。
 */
function searchOwners(input: string): void {
  ownerQuery = input.trim().slice(0, 200)
  owners.value = []
  nextOwnerCursor.value = null
  void loadOwners()
}

/**
 * 清除筛选时恢复整个租户的查询范围。
 * @param value - 当前选择的发起人。
 */
function selectOwner(value: unknown): void {
  emit('update:modelValue', typeof value === 'string' && value ? value : 'all')
}
</script>

<style scoped>
.owner-retry {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
}
</style>
