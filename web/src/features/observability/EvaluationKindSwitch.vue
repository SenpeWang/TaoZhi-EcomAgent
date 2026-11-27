<template>
  <section class="evaluation-kind-switch" role="tablist" aria-label="选择评测体系">
    <span class="evaluation-kind-switch-label">评测体系</span>
    <div class="evaluation-kind-switch-options">
      <button
        v-for="option in options"
        :key="option.value"
        type="button"
        role="tab"
        class="evaluation-kind-option"
        :class="{ 'evaluation-kind-option-active': option.value === modelValue }"
        :aria-selected="option.value === modelValue"
        @click="select(option.value)"
      >
        <span class="evaluation-kind-option-head">
          <strong>{{ option.title }}</strong>
          <span v-if="option.value === modelValue" class="evaluation-kind-option-current"
            >当前</span
          >
        </span>
        <span class="evaluation-kind-option-desc">{{ option.description }}</span>
      </button>
    </div>
  </section>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import { evaluationKindMeta } from './evaluationKindMeta'
import type { EvaluationKind } from './evaluationTypes'

const props = defineProps<{ modelValue: EvaluationKind }>()
const emit = defineEmits<{ (e: 'update:modelValue', value: EvaluationKind): void }>()

const options = computed(() =>
  (Object.keys(evaluationKindMeta) as EvaluationKind[]).map((value) => ({
    value,
    ...evaluationKindMeta[value],
  })),
)

/**
 * 切换当前评测体系。
 * @param value - 目标评测体系。
 */
function select(value: EvaluationKind): void {
  if (value === props.modelValue) return
  emit('update:modelValue', value)
}
</script>

<style scoped>
.evaluation-kind-switch {
  display: flex;
  align-items: stretch;
  flex-wrap: wrap;
  gap: 12px 18px;
  margin-bottom: 16px;
  padding: 12px 16px;
  background: var(--tz-surface);
  border: 1px solid var(--tz-border);
  border-radius: 12px;
}

.evaluation-kind-switch-label {
  align-self: center;
  color: var(--tz-ink-secondary);
  font-size: 13px;
  font-weight: 600;
  white-space: nowrap;
}

.evaluation-kind-switch-options {
  display: flex;
  flex: 1;
  flex-wrap: wrap;
  gap: 10px;
  min-width: 0;
}

.evaluation-kind-option {
  display: flex;
  flex: 1 1 220px;
  flex-direction: column;
  gap: 4px;
  min-width: 0;
  padding: 10px 14px;
  color: var(--tz-ink);
  text-align: left;
  background: var(--tz-surface-muted, var(--tz-surface));
  border: 1px solid var(--tz-border);
  border-radius: 10px;
  cursor: pointer;
  transition:
    border-color 0.15s ease,
    background-color 0.15s ease,
    box-shadow 0.15s ease;
}

.evaluation-kind-option:hover {
  border-color: var(--tz-primary);
}

.evaluation-kind-option:focus-visible {
  outline: 2px solid var(--tz-primary);
  outline-offset: 2px;
}

.evaluation-kind-option-active {
  background: var(--tz-primary-soft, rgba(64, 128, 255, 0.08));
  border-color: var(--tz-primary);
  box-shadow: 0 0 0 1px var(--tz-primary) inset;
}

.evaluation-kind-option-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
}

.evaluation-kind-option-head strong {
  font-size: 14px;
  font-weight: 600;
}

.evaluation-kind-option-current {
  flex: none;
  padding: 1px 7px;
  color: var(--tz-primary);
  font-size: 11px;
  border: 1px solid currentColor;
  border-radius: 999px;
}

.evaluation-kind-option-desc {
  color: var(--tz-ink-muted);
  font-size: 12px;
}

@media (max-width: 720px) {
  .evaluation-kind-switch {
    flex-direction: column;
    align-items: stretch;
  }

  .evaluation-kind-option {
    flex-basis: auto;
  }
}
</style>
