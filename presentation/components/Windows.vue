<script setup lang="ts">
// Training vs demo windows, redrawn from fig:windows in latex/contents.tex.
// (a) sliding 5-min windows with a 2-min stride: the 14:59:30 event falls in two windows (blue).
// (b) tumbling, clock-aligned 5-min windows (shown on click 1): each event is in exactly one window.
import { computed } from 'vue'
import { useSlideContext } from '@slidev/client'

const { $clicks } = useSlideContext()
const showB = computed(() => $clicks.value >= 1)

const M = 68                       // px per minute
const X = (m: number) => 34 + m * M
const train = [0, 2, 4, 6].map((s, i) => ({ s, id: `w000${i}`, hit: s <= 3.5 && 3.5 < s + 5 }))
const hh = (m: number) => { const t = 14 * 60 + 56 + m; return `${Math.floor(t / 60)}:${String(t % 60).padStart(2, '0')}` }
const dh = (m: number) => { const t = 11 * 60 + 25 + m; return `${Math.floor(t / 60)}:${String(t % 60).padStart(2, '0')}` }
</script>

<template>
  <svg viewBox="0 0 830 330" class="wn">
    <!-- (a) training -->
    <text x="20" y="18" class="ttl">(a) Huấn luyện: cửa sổ trượt (dài 5 phút, bước 2 phút)</text>
    <line :x1="X(0)" y1="44" :x2="X(11.3)" y2="44" class="axis" />
    <g v-for="m in [0, 2, 4, 6, 8, 10]" :key="m">
      <line :x1="X(m)" y1="40" :x2="X(m)" y2="48" class="axis" />
      <text :x="X(m)" y="62" class="tick">{{ hh(m) }}</text>
    </g>
    <line :x1="X(3.5)" y1="44" :x2="X(3.5)" y2="178" class="evline" />
    <g v-for="(w, i) in train" :key="w.id">
      <rect :x="X(w.s)" :y="76 + i * 26" :width="5 * M" height="20" rx="3" :class="['win', { hit: w.hit }]"
            :style="{ animationDelay: `${i * 0.15}s` }" />
      <text :x="X(w.s) + 2.5 * M" :y="90 + i * 26" class="wl">{{ w.id }} [{{ hh(w.s) }}, {{ hh(w.s + 5) }})</text>
    </g>
    <circle :cx="X(3.5)" cy="44" r="6" class="ev" />
    <text :x="X(3.5)" y="32" class="evl">14:59:30</text>

    <!-- (b) demo -->
    <g :class="['part', { on: showB }]">
      <text x="20" y="208" class="ttl">(b) Demo trực tiếp: cửa sổ lật (dài 5 phút, nối tiếp, thẳng hàng đồng hồ)</text>
      <line :x1="X(0)" y1="246" :x2="X(11.3)" y2="246" class="axis" />
      <g v-for="m in [0, 5, 10]" :key="m">
        <line :x1="X(m)" y1="242" :x2="X(m)" y2="250" class="axis" />
        <text :x="X(m)" y="264" class="tick">{{ dh(m) }}</text>
      </g>
      <rect :x="X(0)" y="278" :width="5 * M" height="20" rx="3" class="win" />
      <text :x="X(2.5)" y="292" class="wl">[11:25, 11:30)</text>
      <rect :x="X(5)" y="278" :width="5 * M" height="20" rx="3" class="win" />
      <text :x="X(7.5)" y="292" class="wl">[11:30, 11:35)</text>
      <circle :cx="X(4.85)" cy="246" r="5" class="ev" />
      <circle :cx="X(5.15)" cy="246" r="5" class="ev" />
      <text :x="X(4.8)" y="234" class="evl l">11:29:59.9 → cửa sổ trước</text>
      <text :x="X(5.2)" y="234" class="evl r">11:30:00.0 → cửa sổ sau</text>
    </g>
  </svg>
</template>

<style scoped>
.wn { width: 100%; height: auto; font-family: 'Be Vietnam Pro', sans-serif; }
.ttl { font-size: 13px; font-weight: 700; fill: #111827; }
.axis { stroke: #374151; stroke-width: 1.3; }
.tick { font-size: 11px; text-anchor: middle; fill: #374151; }
.win { fill: #eeeeee; stroke: #6b7280; stroke-width: 1; }
.win.hit { fill: #dbe4fb; stroke: #1e3a8a; animation: glow 2.4s ease-in-out infinite; }
@keyframes glow { 0%, 100% { fill: #dbe4fb; } 50% { fill: #bfd0f7; } }
.wl { font-size: 11px; text-anchor: middle; fill: #111827; font-family: 'JetBrains Mono', monospace; }
.evline { stroke: #a51d1d; stroke-width: 1.6; stroke-dasharray: 2 3; }
.ev { fill: #a51d1d; }
.evl { font-size: 11px; fill: #a51d1d; text-anchor: middle; }
.evl.l { text-anchor: end; }
.evl.r { text-anchor: start; }
.part { opacity: 0; transition: opacity 0.5s ease; }
.part.on { opacity: 1; }
</style>
