<script setup lang="ts">
// Stack of hidden layers for one node: X (15) -> H1 (32) -> H2 (32) -> H3 = Z (32) -> one-class.
// [PAPER §7.2.1] 3 RGCN layers, 32-d; [OURS] src/pipeline/train_gnn_models.py dims = [in_dim] + [hid_dim] * num_layers.
// Click k reveals layer k; click 4 hands Z to the one-class step (next slide).
import { computed } from 'vue'
import { useSlideContext } from '@slidev/client'

const { $clicks } = useSlideContext()
const k = computed(() => $clicks.value)

const CELL = 6, W = 40, TOP = 62
// deterministic "values" so the cells look like a vector without being random each render
const shade = (col: number, i: number) => 0.15 + 0.8 * Math.abs(Math.sin(col * 3.1 + i * 1.7))
const cols = [
  { x: 60, n: 15, title: 'Input X', sub: '15 số từ ②', hop: 'chỉ chính nó', kind: 'in' },
  { x: 230, n: 32, title: 'Hidden layer 1', sub: '32 số', hop: 'thấy neighbor 1 bước', kind: 'hid' },
  { x: 400, n: 32, title: 'Hidden layer 2', sub: '32 số', hop: 'thấy 2 bước', kind: 'hid' },
  { x: 570, n: 32, title: 'Hidden layer 3 = Z', sub: '32 số = embedding', hop: 'thấy 3 bước', kind: 'z' },
]
const H = 32 * CELL
</script>

<template>
  <div>
    <svg viewBox="0 0 900 330" class="hlayers">
      <defs>
        <marker id="hl-arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto">
          <path d="M0,0 L10,5 L0,10 z" fill="#374151" />
        </marker>
      </defs>

      <g v-for="(c, ci) in cols" :key="c.title" :class="['fade', { on: k >= ci }]">
        <!-- arrow from the previous column -->
        <g v-if="ci > 0">
          <line :x1="cols[ci - 1].x + W + 8" :y1="TOP + H / 2" :x2="c.x - 10" :y2="TOP + H / 2" class="arr" marker-end="url(#hl-arrow)" />
          <text :x="(cols[ci - 1].x + W + c.x) / 2" :y="TOP + H / 2 - 10" class="al">1 lớp RGCN</text>
        </g>
        <text :x="c.x + W / 2" :y="TOP - 30" :class="['ct', c.kind]">{{ c.title }}</text>
        <rect :x="c.x - 3" :y="TOP - 3" :width="W + 6" :height="c.n * CELL + 6" rx="4" :class="['frame', c.kind]" />
        <rect v-for="i in c.n" :key="i" :x="c.x" :y="TOP + (i - 1) * CELL" :width="W" :height="CELL - 1"
              :class="['cell', c.kind]" :style="{ opacity: shade(ci, i) }" />
        <text :x="c.x + W / 2" :y="TOP + H + 24" class="sub">{{ c.sub }}</text>
        <text :x="c.x + W / 2" :y="TOP + H + 42" class="hop">{{ c.hop }}</text>
      </g>

      <!-- "last hidden layer" callout (click 3) -->
      <g :class="['fade', { on: k >= 3 }]">
        <text :x="cols[3].x + W / 2" :y="TOP - 12" class="callout">hidden layer cuối</text>
      </g>

      <!-- hand-off to one-class (click 4) -->
      <g :class="['fade', { on: k >= 4 }]">
        <line :x1="cols[3].x + W + 8" :y1="TOP + H / 2" x2="700" :y2="TOP + H / 2" class="arr z" marker-end="url(#hl-arrow)" />
        <text x="655" :y="TOP + H / 2 - 10" class="al">đầu vào</text>
        <rect x="705" :y="TOP + 10" width="180" :height="H - 20" rx="10" class="oc" />
        <text x="795" :y="TOP + 34" class="oct">One-class learning</text>
        <circle cx="780" :cy="TOP + 110" r="44" class="sph" />
        <circle cx="780" :cy="TOP + 110" r="3" fill="#111" />
        <circle v-for="(p, i) in [[-14, -10], [10, 14], [-20, 16], [18, -18], [4, -4]]" :key="i"
                :cx="780 + p[0]" :cy="TOP + 110 + p[1]" r="3" fill="#8b8f97" />
        <circle cx="850" :cy="TOP + 70" r="5" class="zpt" />
        <line x1="780" :y1="TOP + 110" x2="850" :y2="TOP + 70" class="dist" />
        <text x="795" :y="TOP + 175" class="ocs">điểm s = xa center bao nhiêu</text>
      </g>
    </svg>

    <div class="cap">
      <b>Hidden layer</b> = vectơ trung gian của node: không có trong log, do mô hình tự tính qua từng lớp RGCN.
      Chỉ <b>lớp cuối (Z)</b> được dùng tiếp, làm đầu vào cho One-class learning.
      <span class="tag paper">PAPER §7.2.1</span>
    </div>
    <div class="foot">Mọi node (PROCESS, FILE, NET) đi qua cùng các lớp; mỗi loại node có hypersphere riêng.</div>
  </div>
</template>

<style scoped>
.hlayers { width: 100%; height: auto; font-family: 'Be Vietnam Pro', sans-serif; }
.cell.in { fill: #6b7280; }
.cell.hid { fill: #1d4ed8; }
.cell.z { fill: #166534; }
.frame { fill: none; stroke: #d1d5db; stroke-width: 1; }
.frame.z { stroke: #166534; stroke-width: 2.5; }
.ct { font-size: 14px; font-weight: 700; text-anchor: middle; fill: #111827; }
.ct.z { fill: #166534; }
.sub { font-size: 12px; text-anchor: middle; fill: #374151; }
.hop { font-size: 11px; text-anchor: middle; fill: #6b7280; }
.callout { font-size: 12px; font-weight: 700; text-anchor: middle; fill: #166534; }
.arr { stroke: #374151; stroke-width: 1.8; }
.arr.z { stroke: #166534; stroke-width: 2.5; }
.al { font-size: 11px; text-anchor: middle; fill: #6b7280; }
.oc { fill: #f0fdf4; stroke: #86efac; stroke-width: 1.5; }
.oct { font-size: 14px; font-weight: 700; text-anchor: middle; fill: #166534; }
.sph { fill: #dbe4fb; stroke: #1e3a8a; stroke-width: 1.5; }
.zpt { fill: #a51d1d; }
.dist { stroke: #a51d1d; stroke-width: 1.3; stroke-dasharray: 4 3; }
.ocs { font-size: 11px; text-anchor: middle; fill: #374151; }
.fade { opacity: 0; transition: opacity 0.5s ease; }
.fade.on { opacity: 1; }
.cap { font-size: 0.85rem; margin-top: 0.3rem; text-align: center; }
.foot { font-size: 0.72rem; color: #6b7280; text-align: center; margin-top: 0.2rem; }
</style>
