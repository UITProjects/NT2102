<script setup lang="ts">
// "Normal is a sphere", redrawn from fig:hypersphere in latex/contents.tex (2-D stand-in for 32-D).
// Click 1 shows example 1 (z on the surface, s = 0); click 2 shows example 2 (z = (3,4), s = 24 > theta).
import { computed } from 'vue'
import { useSlideContext } from '@slidev/client'

const { $clicks } = useSlideContext()
const k = computed(() => $clicks.value)

const S = 78
const X = (x: number) => 150 + x * S
const Y = (y: number) => 140 - y * S
const benign = [[0.2, 0.3], [-0.4, 0.1], [0.1, -0.5], [-0.2, -0.3], [0.5, -0.1], [-0.6, 0.5], [0.3, 0.6], [-0.1, 0.7],
  [-0.7, -0.4], [0.6, -0.6], [1.05, 0.3], [-0.95, -0.55]]
</script>

<template>
  <svg viewBox="0 0 680 260" class="hs">
    <circle :cx="X(0)" :cy="Y(0)" :r="S" class="sphere" />
    <circle :cx="X(0)" :cy="Y(0)" :r="1.3 * S" class="theta" />
    <text :x="X(1.34) + 4" :y="Y(-0.55)" class="lbl muted">threshold θ</text>
    <circle v-for="(p, i) in benign" :key="i" :cx="X(p[0])" :cy="Y(p[1])" r="4" class="ben"
            :style="{ animationDelay: `${i * 0.25}s` }" />
    <circle :cx="X(0)" :cy="Y(0)" r="3.5" fill="#111" />
    <text :x="X(0) + 5" :y="Y(0) + 16" class="lbl">c</text>
    <line :x1="X(0)" :y1="Y(0)" :x2="X(-1)" :y2="Y(0)" class="radius" />
    <text :x="X(-0.5)" :y="Y(0) - 6" class="lbl">R</text>

    <g :class="['ex', { on: k >= 1 }]">
      <circle :cx="X(0.6)" :cy="Y(0.8)" r="6" fill="#1d4ed8" />
      <polyline :points="`${X(0.6)},${Y(0.8)} ${X(0.3)},${Y(1.55)} ${X(-1.6)},${Y(1.55)}`" class="lead" />
      <text :x="X(-1.62)" :y="Y(1.55) - 6" class="lbl" text-anchor="start">Ví dụ 1: z = (0.6, 0.8) → s = 0 (trên mặt cầu)</text>
    </g>
    <g :class="['ex', { on: k >= 2 }]">
      <line :x1="X(0)" :y1="Y(0)" :x2="X(4.5)" :y2="Y(1.2)" class="far" />
      <circle :cx="X(4.6)" :cy="Y(1.2)" r="8" class="anom" />
      <text x="392" :y="Y(1.2) + 40" class="lbl atk">Ví dụ 2: z = (3, 4), cách center 5</text>
      <text x="392" :y="Y(1.2) + 58" class="lbl atk">s = 25 − 1 = 24 &gt; θ ⇒ bất thường</text>
      <text x="392" :y="Y(1.2) + 74" class="tinylbl">(vẽ gần lại, không đúng tỉ lệ)</text>
    </g>
  </svg>
</template>

<style scoped>
.hs { width: 100%; height: auto; font-family: 'Be Vietnam Pro', sans-serif; }
.sphere { fill: #dbe4fb; stroke: #1e3a8a; stroke-width: 2; }
.theta { fill: none; stroke: #111; stroke-width: 1.6; stroke-dasharray: 2 4; }
.ben { fill: #8b8f97; animation: drift 3s ease-in-out infinite; transform-box: fill-box; }
@keyframes drift { 0%, 100% { transform: translate(0, 0); } 50% { transform: translate(1.5px, -1.5px); } }
.radius { stroke: #111; stroke-width: 1.3; }
.lbl { font-size: 13px; fill: #111827; }
.lbl.muted { fill: #6b7280; font-size: 12px; }
.lbl.atk { fill: #a51d1d; font-weight: 600; }
.tinylbl { font-size: 11px; fill: #6b7280; }
.lead { fill: none; stroke: #9ca3af; stroke-width: 1; }
.far { stroke: #a51d1d; stroke-width: 1.6; stroke-dasharray: 5 4; }
.anom { fill: #a51d1d; animation: pulse 1.4s ease-in-out infinite; transform-box: fill-box; transform-origin: center; }
@keyframes pulse { 0%, 100% { transform: scale(1); } 50% { transform: scale(1.35); } }
.ex { opacity: 0; transition: opacity 0.5s ease; }
.ex.on { opacity: 1; }
</style>
