<script setup lang="ts">
// One RGCN layer for rundll32, one click per step (same toy numbers as the "Vì sao RGCN" slide).
// [AUTHOR CODE] OCR-APT/src/pygod/nn/rgcn_conv.py: aggr='mean' inside each edge type, results summed across
// types, plus the node's own term (root = W0), then ReLU (pygod/nn/ocrgcn.py l.139).
import { computed } from 'vue'
import { useSlideContext } from '@slidev/client'

const { $clicks } = useSlideContext()
const k = computed(() => $clicks.value)

const C = { x: 325, y: 175 }              // rundll32
const load = [{ name: 'stage.dll', h: 2, x: 105, y: 95 }, { name: 'kernel32.dll', h: 4, x: 105, y: 175 }]
const send = { name: '203.0.113.50', h: 10, x: 545, y: 135 }
const path = (n: { x: number, y: number }) => `M${n.x},${n.y} L${C.x},${C.y}`

const steps = [
  'Chia nhóm neighbor theo loại edge',
  'Trung bình (mean) trong từng nhóm',
  'Nhân với trọng số của loại edge đó; chính nó nhân W₀',
  'Cộng (sum) tất cả các phần',
  'ReLU: số âm thành 0 → giá trị mới',
]
</script>

<template>
  <div class="wrap">
    <svg viewBox="0 0 650 360" class="rg">
      <!-- groups (click 1) -->
      <g :class="['fade', { on: k >= 1 }]">
        <rect x="20" y="40" width="190" height="245" rx="10" class="grp load" />
        <text x="22" y="32" class="gl load">nhóm Load</text>
        <rect x="445" y="40" width="190" height="245" rx="10" class="grp send" />
        <text x="447" y="32" class="gl send">nhóm Send</text>
      </g>

      <!-- edges -->
      <line v-for="n in load" :key="n.name" :x1="n.x" :y1="n.y" :x2="C.x" :y2="C.y" class="e load" />
      <line :x1="send.x" :y1="send.y" :x2="C.x" :y2="C.y" class="e send" />
      <text x="225" y="118" class="el load">Load</text>
      <text x="372" y="152" class="el send">Send</text>

      <!-- messages travelling to rundll32 (clicks 1-2) -->
      <g v-if="k >= 1 && k <= 2">
        <circle v-for="(n, i) in load" :key="'m' + i" r="6" class="msg load">
          <animateMotion :path="path(n)" dur="1.6s" repeatCount="indefinite" :begin="`${i * 0.3}s`" />
        </circle>
        <circle r="6" class="msg send">
          <animateMotion :path="path(send)" dur="1.6s" repeatCount="indefinite" />
        </circle>
      </g>

      <!-- neighbour nodes -->
      <g v-for="n in load" :key="'n' + n.name">
        <rect :x="n.x - 28" :y="n.y - 20" width="56" height="40" rx="4" class="nb" />
        <text :x="n.x" :y="n.y + 6" class="val">{{ n.h }}</text>
        <text :x="n.x" :y="n.y - 28" class="nm">{{ n.name }} · <tspan class="ty">FILE</tspan></text>
      </g>
      <polygon :points="`${send.x},${send.y - 30} ${send.x + 30},${send.y} ${send.x},${send.y + 30} ${send.x - 30},${send.y}`" class="nb" />
      <text :x="send.x" :y="send.y + 6" class="val">{{ send.h }}</text>
      <text :x="send.x" :y="send.y - 38" class="nm">{{ send.name }} · <tspan class="ty">NET</tspan></text>

      <!-- self loop (click 3) -->
      <g :class="['fade', { on: k >= 3 }]">
        <path :d="`M${C.x - 18},${C.y - 33} C${C.x - 45},${C.y - 95} ${C.x + 45},${C.y - 95} ${C.x + 18},${C.y - 33}`" class="e self" />
        <text :x="C.x" :y="C.y - 98" class="calc self">1 × W₀ (0.5) = 0.5</text>
      </g>

      <!-- rundll32 -->
      <circle :cx="C.x" :cy="C.y" r="34" :class="['me', { upd: k >= 5 }]" />
      <text :x="C.x" :y="C.y + 7" class="val big">{{ k >= 5 ? '0.1' : '1' }}</text>
      <text :x="C.x" :y="C.y + 54" class="nm b">rundll32 · <tspan class="ty">PROCESS</tspan></text>

      <!-- mean (click 2) -->
      <g :class="['fade', { on: k >= 2 }]">
        <text x="115" y="232" class="calc load">(2 + 4) / 2 = 3</text>
        <text x="540" y="232" class="calc send">10 / 1 = 10</text>
      </g>
      <!-- weight (click 3) -->
      <g :class="['fade', { on: k >= 3 }]">
        <text x="115" y="264" class="calc load">3 × W_load (−0.2) = −0.6</text>
        <text x="540" y="264" class="calc send">10 × W_send (0.02) = 0.2</text>
      </g>

      <!-- sum (click 4) and ReLU (click 5) -->
      <g :class="['fade', { on: k >= 4 }]">
        <rect x="120" y="302" width="410" height="42" rx="8" class="sum" />
        <text x="325" y="329" class="calc sumt">
          <tspan class="self">0.5</tspan> + <tspan class="load">(−0.6)</tspan> + <tspan class="send">0.2</tspan> = <tspan font-weight="700">0.1</tspan>
          <tspan v-if="k >= 5"> → ReLU(0.1) = <tspan font-weight="700">0.1</tspan></tspan>
        </text>
      </g>
    </svg>

    <div class="steps">
      <div v-for="(s, i) in steps" :key="i" :class="['st', { cur: k === i + 1, done: k > i + 1 }]">
        <span class="no">{{ i + 1 }}</span>{{ s }}
      </div>
      <div :class="['fade', 'note', { on: k >= 5 }]">Nếu tổng âm, ReLU đưa về 0.</div>
      <div class="legend">○ PROCESS · ▭ FILE · ◇ NET</div>
      <div class="foot">Node đang cập nhật: <b>rundll32 (PROCESS)</b>; neighbor là 2 FILE và 1 NET. Kết quả được chấm bởi mô hình PROCESS.<br/>Minh hoạ: mỗi node 1 số; thật ra là vectơ 15 số và W là ma trận. Chỉ vẽ Load và Send.</div>
    </div>
  </div>
</template>

<style scoped>
.wrap { display: grid; grid-template-columns: 1.75fr 1fr; gap: 1rem; align-items: center; }
.rg { width: 100%; height: auto; font-family: 'Be Vietnam Pro', sans-serif; }
.grp { fill: none; stroke-width: 1.5; stroke-dasharray: 6 4; }
.grp.load { stroke: #1d4ed8; fill: #eff4fe; }
.grp.send { stroke: #c2410c; fill: #fff7ed; }
.gl { font-size: 12px; font-weight: 700; }
.e { stroke-width: 2.5; fill: none; }
.load { stroke: #1d4ed8; fill: #1d4ed8; }
.send { stroke: #c2410c; fill: #c2410c; }
.self { stroke: #166534; fill: #166534; }
text.load, text.send, text.self, tspan.load, tspan.send, tspan.self { stroke: none; }
path.e.self { fill: none; }
.el { font-size: 13px; font-weight: 700; }
.msg { stroke: #fff; stroke-width: 1.5; }
.nb { fill: #f8fafc; stroke: #6b7280; stroke-width: 1.5; }
.me { fill: #dbe4fb; stroke: #1e3a8a; stroke-width: 2.5; transition: fill 0.4s; }
.me.upd { fill: #d6f0d6; stroke: #166534; animation: pulse 1.2s ease-in-out 2; transform-box: fill-box; transform-origin: center; }
@keyframes pulse { 0%, 100% { transform: scale(1); } 50% { transform: scale(1.15); } }
.val { font-size: 17px; font-weight: 700; text-anchor: middle; fill: #111827; }
.val.big { font-size: 20px; }
.nm { font-size: 12px; text-anchor: middle; fill: #374151; }
.nm.b { font-weight: 700; font-size: 13px; }
.ty { font-weight: 700; fill: #6b7280; font-size: 11px; letter-spacing: 0.04em; }
.legend { font-size: 0.75rem; color: #374151; }
.calc { font-size: 13px; text-anchor: middle; font-weight: 600; }
.sum { fill: #f8fafc; stroke: #9ca3af; }
.sumt { font-size: 16px; fill: #111827; }
.fade { opacity: 0; transition: opacity 0.5s ease; }
.fade.on { opacity: 1; }
.steps { font-size: 0.85rem; display: flex; flex-direction: column; gap: 0.45rem; }
.st { padding: 0.35rem 0.6rem; border-radius: 6px; border: 1px solid #e5e7eb; color: #9ca3af; transition: all 0.3s; }
.st.done { color: #374151; }
.st.cur { color: #111827; background: #eff4fe; border-color: #a5b8ef; font-weight: 600; }
.no { display: inline-block; width: 1.3rem; height: 1.3rem; line-height: 1.3rem; text-align: center; border-radius: 50%;
  background: #1e3a8a; color: #fff; font-size: 0.7rem; margin-right: 0.45rem; }
.note { font-size: 0.75rem; color: #6b7280; }
.foot { font-size: 0.7rem; color: #6b7280; margin-top: 0.3rem; }
</style>
