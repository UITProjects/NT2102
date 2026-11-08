<script setup lang="ts">
// Behaviour feature = counting what each node does. The same 17-event trace as
// docs/explainers/edges-to-node-features.html and slide "② Kết quả" (chrome / powershell / rundll32).
// Layout: graph on top, the EXACT trained feature columns (out_<a> / in_<a>) counting below.
// Feature schema mirrors src/pipeline/encode_to_features.py:30-33 (ACTIONS, ACTION_COLS).
// Clicks: 1 chrome browses · 2 a macro spawns powershell · 3 rundll32 beacons out · 4 the 15-số note.
import { computed } from 'vue'
import { useSlideContext } from '@slidev/client'

const { $clicks } = useSlideContext()
const step = computed(() => $clicks.value)

type Shape = 'proc' | 'file' | 'net'
const nodes: Record<string, { x: number; y: number; t: string; s: Shape; w: number }> = {
  explorer:  { x: 60,  y: 150, t: 'explorer',          s: 'proc', w: 78 },
  chrome:    { x: 210, y: 64,  t: 'chrome',            s: 'proc', w: 72 },
  powershell:{ x: 210, y: 236, t: 'powershell',        s: 'proc', w: 92 },
  rundll32:  { x: 548, y: 286, t: 'rundll32',          s: 'proc', w: 78 },
  kernel32:  { x: 600, y: 120, t: 'kernel32.dll',      s: 'file', w: 56 },
  invoice:   { x: 352, y: 150, t: 'invoice.docm',      s: 'file', w: 56 },
  stage:     { x: 440, y: 212, t: 'stage.dll',         s: 'file', w: 50 },
  flowA:     { x: 430, y: 30,  t: '198.51.100.23:443', s: 'net',  w: 66 },
  flowB:     { x: 368, y: 322, t: '203.0.113.50:8080', s: 'net',  w: 66 },
}

// group g = which click reveals the edge. Each raw event adds +1 out_<a> at src and +1 in_<a> at dst.
const events: { src: string; dst: string; a: string; g: number }[] = [
  { src: 'explorer', dst: 'chrome', a: 'start', g: 1 },
  { src: 'chrome', dst: 'kernel32', a: 'load', g: 1 },
  { src: 'chrome', dst: 'flowA', a: 'send', g: 1 },
  { src: 'chrome', dst: 'flowA', a: 'recv', g: 1 },
  { src: 'chrome', dst: 'flowA', a: 'send', g: 1 },
  { src: 'chrome', dst: 'flowA', a: 'recv', g: 1 },
  { src: 'chrome', dst: 'flowA', a: 'recv', g: 1 },
  { src: 'chrome', dst: 'invoice', a: 'write', g: 1 },
  { src: 'explorer', dst: 'powershell', a: 'start', g: 2 },
  { src: 'powershell', dst: 'invoice', a: 'read', g: 2 },
  { src: 'powershell', dst: 'flowB', a: 'send', g: 2 },
  { src: 'powershell', dst: 'flowB', a: 'recv', g: 2 },
  { src: 'powershell', dst: 'stage', a: 'write', g: 2 },
  { src: 'powershell', dst: 'rundll32', a: 'start', g: 2 },
  { src: 'rundll32', dst: 'stage', a: 'load', g: 3 },
  { src: 'rundll32', dst: 'kernel32', a: 'load', g: 3 },
  { src: 'rundll32', dst: 'flowB', a: 'send', g: 3 },
]

// one drawn line per (src,dst) pair, label = the distinct action types on it
const links = computed(() => {
  const m = new Map<string, { src: string; dst: string; acts: Set<string>; g: number }>()
  for (const e of events) {
    const k = e.src + '>' + e.dst
    if (!m.has(k)) m.set(k, { src: e.src, dst: e.dst, acts: new Set(), g: e.g })
    const l = m.get(k)!
    l.acts.add(e.a[0].toUpperCase() + e.a.slice(1))
    l.g = Math.min(l.g, e.g)
  }
  return [...m.values()]
})

// column order = src/pipeline/encode_to_features.py ACTIONS (matches slide "② Kết quả")
const ACTIONS = ['write', 'read', 'send', 'recv', 'start', 'load']
// rows chosen to exercise BOTH directions: processes (out_*, in_start), a file (in_write/in_load), a flow (in_send/in_recv)
const rowIds = ['chrome', 'powershell', 'rundll32', 'stage', 'flowB']

// cumulative out_<a> / in_<a> counts up to the current click (every edge: +1 out at src, +1 in at dst)
const counts = computed(() => {
  const rows: Record<string, Record<string, number>> = {}
  for (const id of rowIds) {
    rows[id] = {}
    for (const a of ACTIONS) { rows[id]['out_' + a] = 0; rows[id]['in_' + a] = 0 }
  }
  for (const e of events) {
    if (e.g > step.value) continue
    if (rows[e.src]) rows[e.src]['out_' + e.a] += 1
    if (rows[e.dst]) rows[e.dst]['in_' + e.a] += 1
  }
  return rows
})

const cx = (id: string) => nodes[id].x
const cy = (id: string) => nodes[id].y
const mid = (l: { src: string; dst: string }) => ({
  x: (cx(l.src) + cx(l.dst)) / 2,
  y: (cy(l.src) + cy(l.dst)) / 2 - 6,
})
</script>

<template>
  <div class="bc">
    <!-- top half: the provenance graph -->
    <svg viewBox="0 0 680 352" class="g">
      <g v-for="(l, i) in links" :key="i" :class="['edge', { on: step >= l.g, now: step === l.g }]">
        <line :x1="cx(l.src)" :y1="cy(l.src)" :x2="cx(l.dst)" :y2="cy(l.dst)" marker-end="url(#bc-arrow)" />
        <text :x="mid(l).x" :y="mid(l).y" class="elab">{{ [...l.acts].join('·') }}</text>
      </g>
      <defs>
        <marker id="bc-arrow" viewBox="0 0 10 10" refX="9" refY="5" markerUnits="userSpaceOnUse"
          markerWidth="9" markerHeight="9" orient="auto-start-reverse">
          <path d="M0,0 L10,5 L0,10 z" fill="#6b7280" />
        </marker>
      </defs>
      <g v-for="(n, id) in nodes" :key="id">
        <rect v-if="n.s === 'proc'" :x="n.x - n.w / 2" :y="n.y - 15" :width="n.w" height="30" rx="6" class="node" />
        <ellipse v-else-if="n.s === 'file'" :cx="n.x" :cy="n.y" :rx="n.w / 2" ry="15" class="node" />
        <polygon v-else
          :points="`${n.x - n.w / 2},${n.y} ${n.x},${n.y - 16} ${n.x + n.w / 2},${n.y} ${n.x},${n.y + 16}`"
          class="node" />
        <text :x="n.x" :y="n.y + 4" class="ntxt">{{ n.t }}</text>
      </g>
    </svg>

    <!-- bottom half: the exact trained feature columns, counting live -->
    <div class="feat-wrap">
      <div class="lead">Mỗi event: <b>+1</b> vào <code>out_&lt;action&gt;</code> của source, <b>+1</b> vào <code>in_&lt;action&gt;</code> của destination — đây là <b>đúng các feature</b> đem huấn luyện.</div>
      <table class="feat">
        <thead>
          <tr><th rowspan="2">node</th><th colspan="6" class="g">out_* (node là source)</th><th colspan="6" class="g">in_* (node là destination)</th></tr>
          <tr><th v-for="a in ACTIONS" :key="'o' + a">{{ a }}</th><th v-for="a in ACTIONS" :key="'i' + a">{{ a }}</th></tr>
        </thead>
        <tbody>
          <tr v-for="id in rowIds" :key="id" :class="{ hl: id === 'rundll32' }">
            <td class="n">{{ nodes[id].t }}</td>
            <td v-for="a in ACTIONS" :key="'o' + a" :class="{ z: counts[id]['out_' + a] === 0 }">{{ counts[id]['out_' + a] || '·' }}</td>
            <td v-for="a in ACTIONS" :key="'i' + a" :class="{ z: counts[id]['in_' + a] === 0 }">{{ counts[id]['in_' + a] || '·' }}</td>
          </tr>
        </tbody>
      </table>
      <div class="foot" :class="{ show: step >= 4 }">
        12 cột <code>out_</code>/<code>in_</code> (đang đếm) + 3 cột <b>thời gian nghỉ</b> (từ timestamp) = <b>15 số</b> mỗi node → chuẩn hoá <b>L2</b> ở bước sau.
      </div>
    </div>
  </div>
</template>

<style scoped>
.bc { display: flex; flex-direction: column; gap: 0.35rem; }
.g { width: 100%; height: auto; max-height: 198px; font-family: 'JetBrains Mono', monospace; }
.node { fill: #fff; stroke: #374151; stroke-width: 1.3; }
.ntxt { font-size: 11px; text-anchor: middle; fill: #111827; }
.elab { font-family: 'Be Vietnam Pro', sans-serif; font-size: 10px; text-anchor: middle; fill: #4b5563;
  paint-order: stroke; stroke: #fff; stroke-width: 3.5px; }
.edge { opacity: 0; transition: opacity 0.5s ease; }
.edge.on { opacity: 1; }
.edge line { stroke: #9ca3af; stroke-width: 1.8; }
.edge.now line { stroke: #1e3a8a; stroke-width: 2.4; stroke-dasharray: 300; stroke-dashoffset: 300;
  animation: bcdraw 0.7s ease forwards; }
@keyframes bcdraw { to { stroke-dashoffset: 0; } }
.feat-wrap { font-size: 0.8rem; }
.lead { margin-bottom: 0.35rem; line-height: 1.35; }
.foot { margin-top: 0.3rem; font-size: 0.75rem; color: #6b7280; opacity: 0; transition: opacity 0.4s ease; }
.foot.show { opacity: 1; }
/* table.feat visual styling comes from the global styles/index.css (shared with slide ② Kết quả) */
</style>
