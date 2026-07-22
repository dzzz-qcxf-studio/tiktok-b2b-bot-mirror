<template>
  <div ref="root" class="wc-root" :style="{ minHeight: minHeight + 'px' }">
    <span
      v-for="(w, i) in placements"
      :key="i"
      class="wc-token"
      :class="['wc-' + (w.cls || 'n'), { 'wc-rot': w.rot }]"
      :style="{
        left: w.x + 'px',
        top:  w.y + 'px',
        fontSize: w.size + 'px',
        lineHeight: 1.1,
        transform: w.rot ? `rotate(${w.rot}deg)` : undefined,
      }"
    >{{ w.text }}</span>
    <div v-if="!placements.length" class="wc-empty">{{ emptyText || '—' }}</div>
  </div>
</template>

<script setup lang="ts">
/**
 * Tag cloud with spiral placement.
 * Words are laid out from the center outward along an Archimedean spiral,
 * skipping positions that overlap an already-placed word's bounding box.
 * Works equally well for ASCII and CJK glyphs.
 */
import { computed, ref, onMounted, onBeforeUnmount, watch, nextTick } from 'vue'

interface RawItem { word: string; count: number; cls?: string }
interface Placement { text: string; size: number; cls?: string; x: number; y: number; rot?: number }

const props = withDefaults(defineProps<{
  items: RawItem[]
  minHeight?: number
  rotateChance?: number   // 0..1 chance any single word gets rotated
  emptyText?: string
}>(), {
  minHeight: 220,
  rotateChance: 0.18,
  emptyText: '',
})

const root = ref<HTMLElement | null>(null)
const w = ref(0)
const h = ref(props.minHeight)

let ro: ResizeObserver | null = null
function measure() {
  if (!root.value) return
  const rect = root.value.getBoundingClientRect()
  w.value = Math.max(320, Math.floor(rect.width))
  h.value = Math.max(props.minHeight, Math.floor(rect.height))
}
onMounted(async () => {
  await nextTick()
  measure()
  if (typeof ResizeObserver !== 'undefined') {
    ro = new ResizeObserver(measure)
    if (root.value) ro.observe(root.value)
  }
})
onBeforeUnmount(() => { if (ro) ro.disconnect() })

/** Rough estimate: handle Latin ~0.55 em / char, CJK ~1.0 em / char. */
function estimateWidth(text: string, fontSize: number) {
  let latin = 0, cjk = 0
  for (const ch of text) {
    // CJK Unified Ideographs + Hiragana/Katakana + Hangul
    if (/[　-鿿가-힯＀-￯]/.test(ch)) cjk++
    else latin++
  }
  return (latin * 0.55 + cjk * 1.0) * fontSize + 12 // +padding
}
function estimateHeight(fontSize: number) {
  return fontSize * 1.25
}

const placements = computed<Placement[]>(() => {
  if (!w.value || !props.items?.length) return []
  const H = h.value

  // Bucket counts → font size 11..28 px
  const counts = props.items.map(i => i.count)
  const cmax = Math.max(...counts), cmin = Math.min(...counts)
  const sized = props.items.map(i => {
    const ratio = cmax === cmin ? 1 : (i.count - cmin) / (cmax - cmin)
    return {
      text: i.word,
      size: Math.round(12 + ratio * 16), // 12..28
      cls: ratio > 0.66 ? 'b' : ratio > 0.4 ? 'c' : ratio > 0.18 ? 'o' : 'n',
    }
  })

  // Sort largest first so big words claim the center
  sized.sort((a, b) => b.size - a.size)

  const placed: { cx: number; cy: number; w: number; h: number }[] = []
  const out: Placement[] = []
  const cx0 = w.value / 2
  const cy0 = H / 2

  for (const t of sized) {
    const tw = estimateWidth(t.text, t.size)
    const th = estimateHeight(t.size)

    // First word — center
    if (!placed.length) {
      placed.push({ cx: cx0, cy: cy0, w: tw, h: th })
      out.push({ ...t, x: cx0 - tw / 2, y: cy0 - th / 2, rot: 0 })
      continue
    }

    // Spiral search outward
    let found = false
    const stepA = 2.5   // angular step (degrees)
    const stepR = 1.2   // radial step (px)
    outer: for (let r = 0; r < Math.max(w.value, H) && !found; r += stepR) {
      // angular density scales with radius so spiral doesn't bunch
      for (let a = 0; a < 360; a += stepA) {
        const rad = (a * Math.PI) / 180
        const px = cx0 + r * Math.cos(rad)
        const py = cy0 + r * Math.sin(rad)
        if (px - tw / 2 < 4 || px + tw / 2 > w.value - 4) continue
        if (py - th / 2 < 4 || py + th / 2 > H - 4) continue
        let hit = false
        for (const p of placed) {
          if (Math.abs(px - p.cx) * 2 < tw + p.w && Math.abs(py - p.cy) * 2 < th + p.h) {
            hit = true; break
          }
        }
        if (!hit) {
          placed.push({ cx: px, cy: py, w: tw, h: th })
          const rot = Math.random() < props.rotateChance ? (Math.random() < 0.5 ? -1 : 1) * (5 + Math.random() * 18) : 0
          out.push({ ...t, x: px - tw / 2, y: py - th / 2, rot })
          found = true
          break outer
        }
      }
    }
    // If no fit found, skip this word
  }
  return out
})

watch(() => props.items, () => { /* re-layout next tick via computed */ }, { deep: true })
</script>

<style scoped>
.wc-root {
  position: relative;
  width: 100%;
  padding: 12px 16px 16px;
  overflow: hidden;
  background:
    radial-gradient(circle at 30% 40%, oklch(96% 0.04 350 / 0.35), transparent 60%),
    radial-gradient(circle at 75% 65%, oklch(96% 0.025 200 / 0.35), transparent 60%);
}
.wc-token {
  position: absolute;
  white-space: nowrap;
  user-select: none;
  cursor: default;
  padding: 2px 8px;
  border-radius: 999px;
  transition: transform .15s ease;
}
.wc-token:hover { transform: scale(1.08) !important; }
.wc-b { background: var(--brand-soft); color: var(--brand-deep); font-weight: 700; }
.wc-c { background: var(--cyan-soft); color: oklch(45% 0.12 200); font-weight: 600; }
.wc-o { background: var(--ok-soft); color: oklch(42% 0.16 150); font-weight: 500; }
.wc-n { background: var(--bg-sub); color: var(--fg-2); font-weight: 500; }
.wc-rot { transform-origin: center; }
.wc-empty {
  position: absolute; inset: 0;
  display: grid; place-items: center;
  color: var(--muted); font-size: 13px;
}
</style>