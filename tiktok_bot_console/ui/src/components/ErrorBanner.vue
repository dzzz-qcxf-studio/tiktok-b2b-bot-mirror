<template>
  <div class="error-banner" v-if="message">
    <span class="error-icon">⚠️</span>
    <span class="error-text">{{ message }}</span>
    <button v-if="retryable" class="btn sm ghost" @click="$emit('retry')">
      {{ $t('common.refresh') }}
    </button>
    <button class="error-close" @click="$emit('dismiss')" aria-label="Close">×</button>
  </div>
</template>

<script setup lang="ts">
defineProps<{
  message: string
  retryable?: boolean
}>()

defineEmits<{
  (e: 'retry'): void
  (e: 'dismiss'): void
}>()
</script>

<style scoped>
.error-banner {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 12px 16px;
  background: var(--err-soft);
  color: var(--err);
  border-radius: 10px;
  font-size: 13px;
  margin-bottom: 16px;
}
.error-icon { font-size: 16px; flex-shrink: 0; }
.error-text { flex: 1; }
.error-close {
  border: none;
  background: transparent;
  color: var(--err);
  font-size: 18px;
  cursor: pointer;
  padding: 0 4px;
  opacity: 0.7;
}
.error-close:hover { opacity: 1; }
</style>
