<script setup lang="ts">
// One message-passing layer for node A, drawn without numbers: each click reveals one part of
// h_A' = ReLU(w_self · h_A + w_nb · mean(h_neighbor)); the formula below highlights the same part.
// The numeric version lives on the RgcnLayer slide and in note.txt.
import { computed } from 'vue'
import { useSlideContext } from '@slidev/client'

const { $clicks } = useSlideContext()
const k = computed(() => $clicks.value)

const A = { x: 140, y: 150 }
const nbs = [{ n: 'B', x: 40, y: 55 }, { n: 'C', x: 25, y: 150 }, { n: 'D', x: 40, y: 245 }]
const path = (p: { x: number, y: number }) => `M${p.x},${p.y} L${A.x},${A.y}`

// a vector = 3 cells; shades only (no numbers), signs only where they matter (sum and ReLU)
const cell = 24
const mean = [0.85, 0.45, 0.65]
const self = [0.5, 0.8, 0.35]
const sum = ['+', '−', '+']
const out = ['+', '0', '+']

const steps = [
  'Mỗi neighbor B, C, D gửi vectơ của nó cho A (message)',
  'Gom lại: trung bình vectơ các neighbor, và giữ vectơ của chính A',
  'Nhân mỗi phần với trọng số w (học từ dữ liệu), rồi cộng lại',
  'ReLU: ô âm thành 0 → h′ = hidden layer của A (vectơ trung gian, không có trong log)',
]
</script>

<template>
  <div class="wrap">
    <svg viewBox="0 28 720 242" class="mp">
      <!-- graph: neighbours B, C, D around A -->
      <line v-for="p in nbs" :key="'e' + p.n" :x1="p.x" :y1="p.y" :x2="A.x" :y2="A.y" class="e" />
      <g v-if="k === 1">
        <circle v-for="(p, i) in nbs" :key="'m' + p.n" r="6" class="msg">
          <animateMotion :path="path(p)" dur="1.6s" repeatCount="indefinite" :begin="`${i * 0.3}s`" />
        </circle>
      </g>
      <g v-for="p in nbs" :key="'n' + p.n">
        <circle :cx="p.x" :cy="p.y" r="19" class="nb" />
        <text :x="p.x" :y="p.y + 6" class="nm">{{ p.n }}</text>
      </g>
      <circle :cx="A.x" :cy="A.y" r="26" :class="['me', { upd: k >= 4 }]" />
      <text :x="A.x" :y="A.y + 7" class="nm big">A</text>

      <!-- click 2: gather -> two vectors -->
      <g :class="['fade', { on: k >= 2 }]">
        <path d="M166,138 C205,100 225,80 262,80" class="arr nbc" marker-end="url(#mpa-nb)" />
        <path d="M166,162 C205,200 225,220 262,220" class="arr selfc" marker-end="url(#mpa-self)" />
        <rect v-for="(o, i) in mean" :key="'mn' + i" :x="268 + i * cell" y="68" :width="cell" height="24" class="cell nbf" :fill-opacity="o" />
        <text x="304" y="58" class="lbl nbc b">mean(h<tspan baseline-shift="sub" class="sub">neighbor</tspan>)</text>
        <text x="304" y="112" class="lbl">trung bình vectơ B, C, D</text>
        <rect v-for="(o, i) in self" :key="'sf' + i" :x="268 + i * cell" y="208" :width="cell" height="24" class="cell selff" :fill-opacity="o" />
        <text x="304" y="198" class="lbl selfc b">h<tspan baseline-shift="sub" class="sub">A</tspan></text>
        <text x="304" y="250" class="lbl">vectơ hiện tại của A</text>
        <text x="304" y="265" class="lbl dim">(lớp đầu: feature của A)</text>
      </g>

      <!-- click 3: weights, then add -->
      <g :class="['fade', { on: k >= 3 }]">
        <text x="370" y="85" class="w nbc">× w<tspan baseline-shift="sub" class="sub">nb</tspan></text>
        <text x="370" y="225" class="w selfc">× w<tspan baseline-shift="sub" class="sub">self</tspan></text>
        <path d="M410,80 C440,80 440,140 455,146" class="arr" marker-end="url(#mpa)" />
        <path d="M410,220 C440,220 440,160 455,154" class="arr" marker-end="url(#mpa)" />
        <circle cx="472" cy="150" r="14" class="plus" />
        <text x="472" y="156" class="nm">+</text>
        <line x1="486" y1="150" x2="500" y2="150" class="arr" />
        <g v-for="(s, i) in sum" :key="'su' + i">
          <rect :x="502 + i * cell" y="138" :width="cell" height="24" :class="['cell', s === '−' ? 'neg' : 'pos']" />
          <text :x="514 + i * cell" y="155" class="sg">{{ s }}</text>
        </g>
        <text x="538" y="185" class="lbl">tổng</text>
        <text x="538" y="200" class="lbl">(có thể âm)</text>
      </g>

      <!-- click 4: ReLU -> new vector -->
      <g :class="['fade', { on: k >= 4 }]">
        <line x1="576" y1="150" x2="606" y2="150" class="arr relu" marker-end="url(#mpa-relu)" />
        <rect x="561" y="62" width="60" height="50" rx="6" class="rbox" />
        <polyline points="568,100 591,100 614,70" class="rplot" />
        <line x1="591" y1="66" x2="591" y2="106" class="raxis" />
        <text x="591" y="54" class="lbl relu b">ReLU</text>
        <text x="591" y="128" class="lbl relu">âm → 0</text>
        <g v-for="(s, i) in out" :key="'o' + i">
          <rect :x="612 + i * cell" y="138" :width="cell" height="24" :class="['cell', s === '0' ? 'zero' : 'pos']" />
          <text :x="624 + i * cell" y="155" class="sg">{{ s }}</text>
        </g>
        <text x="648" y="182" class="lbl b">h′<tspan baseline-shift="sub" class="sub">A</tspan></text>
        <text x="648" y="202" class="lbl">vectơ mới của A</text>
        <text x="648" y="218" class="lbl hid">= hidden layer</text>
      </g>

      <defs>
        <marker id="mpa" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="6" markerHeight="6" orient="auto"><path d="M0,0 L10,5 L0,10 z" fill="#6b7280" /></marker>
        <marker id="mpa-nb" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="6" markerHeight="6" orient="auto"><path d="M0,0 L10,5 L0,10 z" fill="#1d4ed8" /></marker>
        <marker id="mpa-self" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="6" markerHeight="6" orient="auto"><path d="M0,0 L10,5 L0,10 z" fill="#166534" /></marker>
        <marker id="mpa-relu" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="6" markerHeight="6" orient="auto"><path d="M0,0 L10,5 L0,10 z" fill="#a51d1d" /></marker>
      </defs>
    </svg>

    <div class="cap">
      <template v-if="k >= 1 && k <= 4"><span class="no">{{ k }}</span>{{ steps[k - 1] }}</template>
    </div>

    <!-- the same formula, each part lit when its picture appears -->
    <div class="fx">
      <span :class="['p', { on: k >= 4 }]">h′<sub>A</sub></span> =
      <span :class="['p', 'relu', { on: k >= 4 }]">ReLU(</span>
      <span :class="['p', 'selfc', { on: k >= 3 }]">w<sub>self</sub></span> ·
      <span :class="['p', 'selfc', { on: k >= 2 }]">h<sub>A</sub></span> +
      <span :class="['p', 'nbc', { on: k >= 3 }]">w<sub>nb</sub></span> ·
      <span :class="['p', 'nbc', { on: k >= 2 }]">mean(h<sub>neighbor</sub>)</span>
      <span :class="['p', 'relu', { on: k >= 4 }]">)</span>
    </div>
  </div>
</template>

<style scoped>
.wrap { display: flex; flex-direction: column; gap: 0.2rem; }
.mp { width: 100%; height: auto; font-family: 'Be Vietnam Pro', sans-serif; }
.e { stroke: #9ca3af; stroke-width: 2.5; }
.msg { fill: #1d4ed8; stroke: #fff; stroke-width: 1.5; }
.nb { fill: #eff4fe; stroke: #1d4ed8; stroke-width: 1.5; }
.me { fill: #dbe4fb; stroke: #1e3a8a; stroke-width: 2.5; transition: fill 0.4s; }
.me.upd { fill: #d6f0d6; stroke: #166534; animation: pulse 1.2s ease-in-out 2; transform-box: fill-box; transform-origin: center; }
@keyframes pulse { 0%, 100% { transform: scale(1); } 50% { transform: scale(1.15); } }
.nm { font-size: 16px; font-weight: 700; text-anchor: middle; fill: #111827; }
.nm.big { font-size: 20px; }
.lbl { font-size: 12px; text-anchor: middle; fill: #374151; }
.lbl.b { font-weight: 700; font-size: 13px; }
.lbl.dim { fill: #6b7280; font-size: 11px; }
.lbl.hid { fill: #166534; font-weight: 700; }
.sub { font-size: 9px; }
.w .sub { font-size: 10px; }
.w { font-size: 15px; font-weight: 700; }
.nbc { fill: #1d4ed8; color: #1d4ed8; }
.selfc { fill: #166534; color: #166534; }
.relu { fill: #a51d1d; color: #a51d1d; }
.cell { stroke: #374151; stroke-width: 1; }
.nbf { fill: #1d4ed8; }
.selff { fill: #166534; }
.pos { fill: #d6f0d6; }
.neg { fill: #fecaca; }
.zero { fill: #ffffff; }
.sg { font-size: 14px; font-weight: 700; text-anchor: middle; fill: #111827; }
.arr { fill: none; stroke: #6b7280; stroke-width: 2; }
.arr.nbc { stroke: #1d4ed8; }
.arr.selfc { stroke: #166534; }
.arr.relu { stroke: #a51d1d; }
.plus { fill: #f8fafc; stroke: #6b7280; stroke-width: 1.5; }
.rbox { fill: #fef2f2; stroke: #fca5a5; }
.rplot { fill: none; stroke: #a51d1d; stroke-width: 2.5; }
.raxis { stroke: #9ca3af; stroke-width: 1; stroke-dasharray: 3 2; }
.fade { opacity: 0; transition: opacity 0.5s ease; }
.fade.on { opacity: 1; }
.cap { text-align: center; font-size: 0.9rem; min-height: 1.5rem; color: #111827; }
.no { display: inline-block; width: 1.3rem; height: 1.3rem; line-height: 1.3rem; text-align: center; border-radius: 50%;
  background: #1e3a8a; color: #fff; font-size: 0.7rem; margin-right: 0.45rem; }
.fx { text-align: center; font-size: 1.05rem; font-family: 'Be Vietnam Pro', sans-serif; color: #9ca3af; }
.fx .p { transition: all 0.3s; }
.fx .p.on { font-weight: 700; }
.fx .p.on:not(.nbc):not(.selfc):not(.relu) { color: #111827; }
.fx .p:not(.on) { color: #9ca3af; }
</style>
