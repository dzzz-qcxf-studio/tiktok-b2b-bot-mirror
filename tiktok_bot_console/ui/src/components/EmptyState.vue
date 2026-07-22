<template>
  <div class="empty-state">
    <div class="empty-icon">{{ icon }}</div>
    <div class="empty-title">{{ title }}</div>
    <div v-if="description" class="empty-desc">{{ description }}</div>
    <slot />
  </div>
</template>

<script setup lang="ts">
// defineProps is a Vue compiler macro — no import needed
defineProps<{
  icon?: string
  title: string
  description?: string
}>()
</script>

<style scoped>
.empty-state {
  text-align: center;
  padding: 60px 20px;
  color: var(--muted);
}
.empty-icon {
  font-size: 48px;
  margin-bottom: 16px;
  opacity: 0.8;
}
.empty-title {
  font-size: 15px;
  font-weight: 600;
  color: var(--fg-2);
  margin-bottom: 8px;
}
.empty-desc {
  font-size: 13px;
  max-width: 400px;
  margin: 0 auto;
  line-height: 1.5;
}
</style>
