<script setup lang="ts">
// An attack seen as a provenance graph, redrawn from fig:pg-example in latex/contents.tex.
// Node positions are the TikZ ones (cm) mapped by X()/Y(); edges are routed by hand so Send / Recv /
// Write leave PowerShell at different heights and never cross. Clicks 1-4 reveal the red attack steps in
// time order; the gray Load edge is ordinary activity that every process has.
import { computed } from 'vue'
import { useSlideContext } from '@slidev/client'

const { $clicks } = useSlideContext()
const step = computed(() => $clicks.value)

const X = (x: number) => 70 + x * 62
const Y = (y: number) => 120 - y * 62

const proc = [
  { x: 0, t: 'node.exe', w: 92 },
  { x: 3.6, t: 'cmd.exe', w: 92 },
  { x: 7.2, t: 'powershell.exe', w: 124 },
]
// x1,y1 -> x2,y2 in px; label position; step badge position (s = 0: benign, no badge)
const edges = [
  { x1: 118, y1: 120, x2: 245, y2: 120, l: 'Start', s: 1, lx: 182, ly: 112, nx: 182, ny: 88 },
  { x1: 341, y1: 120, x2: 452, y2: 120, l: 'Start', s: 2, lx: 397, ly: 112, nx: 397, ny: 88 },
  { x1: 580, y1: 108, x2: 688, y2: 66, l: 'Send', s: 3, lx: 622, ly: 80, nx: 606, ny: 62 },
  { x1: 580, y1: 132, x2: 694, y2: 172, l: 'Write', s: 4, lx: 626, ly: 168, nx: 608, ny: 186 },
  { x1: 293, y1: 139, x2: 293, y2: 205, l: 'Load', s: 0, lx: 318, ly: 176, nx: 0, ny: 0 },
]
</script>

<template>
  <svg viewBox="0 28 860 222" class="ag">
    <defs>
      <marker id="ag-red" viewBox="0 0 10 10" refX="9" refY="5" markerUnits="userSpaceOnUse" markerWidth="11" markerHeight="11" orient="auto-start-reverse">
        <path d="M0,0 L10,5 L0,10 z" fill="#a51d1d" />
      </marker>
      <marker id="ag-gray" viewBox="0 0 10 10" refX="9" refY="5" markerUnits="userSpaceOnUse" markerWidth="9" markerHeight="9" orient="auto-start-reverse">
        <path d="M0,0 L10,5 L0,10 z" fill="#9ca3af" />
      </marker>
    </defs>

    <!-- edges -->
    <g v-for="(e, i) in edges" :key="i" :class="['edge', e.s ? 'atk' : 'ben', { on: !e.s || step >= e.s, now: e.s && step === e.s }]">
      <line :x1="e.x1" :y1="e.y1" :x2="e.x2" :y2="e.y2" :marker-end="e.s ? 'url(#ag-red)' : 'url(#ag-gray)'" />
      <text :x="e.lx" :y="e.ly" class="elab">{{ e.l }}</text>
      <g v-if="e.s">
        <circle :cx="e.nx" :cy="e.ny" r="9" class="step" />
        <text :x="e.nx" :y="e.ny + 4" class="stepn">{{ e.s }}</text>
      </g>
    </g>
    <!-- Recv: the C2 answer, drawn back towards PowerShell (data flow), below the Send line -->
    <g :class="['edge', 'atk', { on: step >= 3 }]">
      <path d="M 744 88 Q 680 124 584 121" fill="none" marker-end="url(#ag-red)" />
      <text x="694" y="128" class="elab">Recv</text>
    </g>

    <!-- nodes: process = rounded box, file = ellipse, network flow = diamond -->
    <g v-for="n in proc" :key="n.t">
      <rect :x="X(n.x) - n.w / 2" :y="Y(0) - 17" :width="n.w" height="34" rx="6" class="node" />
      <text :x="X(n.x)" :y="Y(0) + 5" class="ntxt">{{ n.t }}</text>
    </g>
    <polygon :points="`${X(11) - 62},${Y(0.9)} ${X(11)},${Y(0.9) - 22} ${X(11) + 62},${Y(0.9)} ${X(11)},${Y(0.9) + 22}`" class="node" />
    <text :x="X(11)" :y="Y(0.9) + 5" class="ntxt">10.0.0.5:443</text>
    <ellipse :cx="X(11)" :cy="Y(-0.9)" rx="56" ry="18" class="node" />
    <text :x="X(11)" :y="Y(-0.9) + 5" class="ntxt">payload.exe</text>
    <ellipse :cx="X(3.6)" :cy="Y(-1.7)" rx="48" ry="17" class="node" />
    <text :x="X(3.6)" :y="Y(-1.7) + 5" class="ntxt">ntdll.dll</text>
  </svg>
</template>

<style scoped>
.ag { width: 100%; height: auto; font-family: 'JetBrains Mono', monospace; }
.node { fill: #fff; stroke: #374151; stroke-width: 1.3; }
.ntxt { font-size: 13px; text-anchor: middle; fill: #111827; }
.elab { font-family: 'Be Vietnam Pro', sans-serif; font-size: 12px; text-anchor: middle; fill: #374151; paint-order: stroke; stroke: #fff; stroke-width: 4px; }
.edge { transition: opacity 0.5s ease; opacity: 0; }
.edge.on { opacity: 1; }
.edge.atk line, .edge.atk path { stroke: #a51d1d; stroke-width: 2.6; }
.edge.ben line { stroke: #9ca3af; stroke-width: 1.6; }
/* The newest attack step draws itself once. */
.edge.now line { stroke-dasharray: 400; stroke-dashoffset: 400; animation: draw 0.8s ease forwards; }
@keyframes draw { to { stroke-dashoffset: 0; } }
.step { fill: #a51d1d; }
.stepn { font-family: 'Be Vietnam Pro', sans-serif; font-size: 11px; font-weight: 700; fill: #fff; text-anchor: middle; }
</style>
