<template>
  <section class="panel">
    <div class="section-heading">
      <h3>{{ isAdmin ? '企业提问记录' : '我的提问记录' }}</h3>
      <el-button :loading="busy" @click="refresh">刷新</el-button>
    </div>

    <form class="toolbar trace-filters" @submit.prevent="applyFilters">
      <TenantOwnerSelect
        v-if="isAdmin"
        v-model="selectedOwnerId"
        class="trace-owner"
        :api="api"
        @update:model-value="applyFilters"
      />
      <el-input
        v-model="keyword"
        class="trace-search"
        :aria-label="searchPlaceholder"
        :placeholder="searchPlaceholder"
        maxlength="200"
        clearable
        @clear="applyFilters"
      />
      <el-select
        v-model="taskKindFilter"
        aria-label="任务类型"
        placeholder="全部类型"
        @change="applyFilters"
      >
        <el-option label="全部类型" value="" />
        <el-option :label="traceKind('ask')" value="ask" />
        <el-option :label="traceKind('research')" value="research" />
      </el-select>
      <el-select
        v-model="taskStateFilter"
        aria-label="执行状态"
        placeholder="全部状态"
        @change="applyFilters"
      >
        <el-option label="全部状态" value="" />
        <el-option v-for="value in states" :key="value" :label="stateText(value)" :value="value" />
      </el-select>
      <el-button native-type="submit" type="primary">查询</el-button>
    </form>

    <el-alert v-if="error" :title="error" type="error" show-icon :closable="false" />
    <template v-else>
      <el-table
        v-loading="busy"
        class="desktop-traces"
        :data="rows"
        row-key="id"
        empty-text="没有符合条件的提问"
      >
        <el-table-column label="提问" min-width="250">
          <template #default="{ row }">
            <button
              class="trace-question"
              type="button"
              @click="emit('inspect', row.id, row.matched_trace_id)"
            >
              {{ row.question || row.id }}
            </button>
            <small
              >{{ traceKind(row.kind)
              }}<template v-if="row.attempts > 1"> · 已执行 {{ row.attempts }} 次</template></small
            >
          </template>
        </el-table-column>
        <el-table-column label="发起人" min-width="140">
          <template #default="{ row }">
            <span>{{ row.display_name || row.username || row.owner_id }}</span>
            <small v-if="row.node_name">{{ row.node_name }}</small>
            <small v-else-if="row.username && row.username !== row.display_name">{{
              row.username
            }}</small>
          </template>
        </el-table-column>
        <el-table-column label="当前阶段" min-width="185" show-overflow-tooltip>
          <template #default="{ row }">{{ formatStage(row.stage) }}</template>
        </el-table-column>
        <el-table-column label="状态" width="88">
          <template #default="{ row }">
            <el-tag size="small" :type="stateTag(row.state)">{{ stateText(row.state) }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="发起时间" width="170">
          <template #default="{ row }">{{ traceTime(row.created_at) }}</template>
        </el-table-column>
      </el-table>
      <div v-loading="busy" class="mobile-traces" :aria-busy="busy">
        <ul v-if="rows.length">
          <li v-for="trace in rows" :key="trace.id">
            <div class="trace-mobile-meta">
              <span>{{ traceKind(trace.kind) }}</span>
              <el-tag size="small" :type="stateTag(trace.state)">{{
                stateText(trace.state)
              }}</el-tag>
            </div>
            <button
              class="trace-question"
              type="button"
              @click="emit('inspect', trace.id, trace.matched_trace_id)"
            >
              {{ trace.question || trace.id }}
            </button>
            <small
              >{{ trace.display_name || trace.username || trace.owner_id }} ·
              {{ traceTime(trace.created_at) }}</small
            >
          </li>
        </ul>
        <el-empty v-else-if="!busy" :image-size="48" description="没有符合条件的提问" />
      </div>
    </template>

    <nav class="trace-pagination" aria-label="提问列表分页">
      <span
        >第 {{ page }} 页<template v-if="!busy && !error">
          · 本页 {{ rows.length }} 条</template
        ></span
      >
      <el-button :disabled="busy || page === 1" @click="turn(false)">上一页</el-button>
      <el-button :disabled="busy || !nextCursor" @click="turn(true)">下一页</el-button>
    </nav>
  </section>
</template>

<script setup lang="ts">
import { computed, ref } from 'vue'
import { formatStage, stateTag, stateText, traceKind, traceTime } from '../labels'
import type { ApiReader, TraceSummary } from '../types'
import { useCursorPage } from '../composables/useCursorPage'
import TenantOwnerSelect from './TenantOwnerSelect.vue'

const emit = defineEmits<{ (e: 'inspect', taskId: string, traceId: string | null): void }>()
const props = defineProps<{ isAdmin: boolean; api: ApiReader }>()
const searchPlaceholder = computed(() =>
  props.isAdmin ? '提问、发起人、任务 ID 或 Trace ID' : '提问、任务 ID 或 Trace ID',
)
const states = [
  'queued',
  'running',
  'review',
  'completed',
  'failed',
  'cancelled',
  'rejected',
  'legacy',
]
const selectedOwnerId = ref('all')

const keyword = ref('')
const taskKindFilter = ref('')
const taskStateFilter = ref('')
const submittedKeyword = ref('')
const endpoint = computed(() => {
  const query = new URLSearchParams({ limit: '30' })
  if (props.isAdmin) query.set('owner_id', selectedOwnerId.value)
  if (submittedKeyword.value) query.set('q', submittedKeyword.value)
  if (taskKindFilter.value) query.set('kind', taskKindFilter.value)
  if (taskStateFilter.value) query.set('state', taskStateFilter.value)
  return '/admin/traces?' + query
})
const { rows, busy, error, page, nextCursor, refresh, reset, turn } = useCursorPage<TraceSummary>(
  props.api,
  endpoint,
)

/** 提交搜索词并从第一页查询。 */
function applyFilters(): void {
  submittedKeyword.value = keyword.value.trim()
  reset()
}
</script>

<style scoped>
.trace-filters {
  margin-bottom: 16px;
  padding: 14px;
  border: 1px solid var(--tz-border);
  border-radius: 8px;
  background: var(--tz-surface-muted);
}
.trace-search {
  flex: 1 1 240px;
}
.trace-filters .el-select {
  width: 155px;
}
.trace-filters .trace-owner {
  width: 210px;
}
.trace-question {
  display: -webkit-box;
  overflow: hidden;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  padding: 0;
  border: 0;
  background: transparent;
  color: var(--tz-primary);
  font: inherit;
  text-align: left;
  overflow-wrap: anywhere;
  cursor: pointer;
  font-weight: 600;
  line-height: 1.6;
}
.trace-question:hover {
  text-decoration: underline;
}
.mobile-traces {
  display: none;
}
small {
  display: block;
  margin-top: 2px;
  color: var(--tz-ink-muted);
  font-size: 12px;
}
.trace-pagination {
  display: flex;
  align-items: center;
  justify-content: flex-end;
  gap: 8px;
  margin-top: 16px;
  color: var(--tz-ink-muted);
  font-size: 13px;
}
.trace-pagination .el-button {
  margin: 0;
}
@media (max-width: 700px) {
  .desktop-traces {
    display: none;
  }
  .trace-filters {
    display: grid;
    grid-template-columns: repeat(2, minmax(0, 1fr));
    gap: 8px;
    padding: 10px;
  }
  .trace-filters .el-select {
    width: 100%;
  }
  .trace-search,
  .trace-owner,
  .trace-filters .el-button {
    grid-column: 1 / -1;
  }
  .mobile-traces {
    display: block;
    min-height: 75px;
  }
  .mobile-traces ul {
    list-style: none;
    margin: 0;
    padding: 0;
  }
  .mobile-traces li {
    display: grid;
    justify-items: start;
    gap: 4px;
    padding: 12px 2px;
    border-bottom: 1px solid var(--tz-border);
  }
  .trace-mobile-meta {
    display: flex;
    justify-content: space-between;
    align-items: center;
    width: 100%;
    color: var(--tz-ink-muted);
    font-size: 12px;
  }
  .trace-pagination {
    flex-wrap: wrap;
  }
  .trace-pagination > span {
    margin-inline-end: auto;
  }
}
</style>
