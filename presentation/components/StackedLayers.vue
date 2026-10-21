<script setup lang="ts">
// The same graph on stacked planes, one plane per layer. Node colours are real message passing:
// colour in layer l = mean of its own + neighbours' colours in layer l-1 (as on the previous slide),
// so A only picks up E's colour at layer 3. Dark dashed lines = everything A's final vector depends on.
import { computed } from 'vue'
import { useSlideContext } from '@slidev/client'

const { $clicks } = useSlideContext()
const k = computed(() => Math.min($clicks.value, 3))

type Id = 'A' | 'B' | 'C' | 'D' | 'E'
const ids: Id[] = ['A', 'B', 'C', 'D', 'E']
// (u, v) position on the plane; hops from A: B, C = 1, D = 2, E = 3
const pos: Record<Id, [number, number]> = { A: [0.78, 0.5], B: [0.5, 0.15], C: [0.6, 0.86], D: [0.15, 0.38], E: [0.18, 0.85] }
const edges: [Id, Id][] = [['A', 'B'], ['A', 'C'], ['B', 'D'], ['D', 'E']]
const nb = (i: Id) => edges.flatMap(([a, b]) => (a === i ? [b] : b === i ? [a] : []))
const hop: Record<Id, number> = { A: 0, B: 1, C: 1, D: 2, E: 3 }

// colours: layer 0 = own colour, then mean over self + neighbours
const base: Record<Id, number[]> = { A: [245, 158, 11], B: [59, 130, 246], C: [16, 185, 129], D: [239, 68, 68], E: [139, 92, 246] }
const layers: Record<Id, number[]>[] = [base]
for (let l = 1; l <= 3; l++) {
  const prev = layers[l - 1]
  const next = {} as Record<Id, number[]>
  for (const i of ids) {
    const group = [i, ...nb(i)]
    next[i] = [0, 1, 2].map(c => group.reduce((s, j) => s + prev[j][c], 0) / group.length)
  }
  layers.push(next)
}
const rgb = (c: number[]) => `rgb(${c.map(Math.round).join(',')})`

// plane geometry: (u, v) -> (x, y) on a tilted parallelogram
const GAP = 225, OX = 75, OY = 34, W = 150, SY = 36, SX = -36, H = 158
const P = (l: number, u: number, v: number) => ({ x: OX + l * GAP + u * W + v * SX, y: OY + u * SY + v * H })
const plane = (l: number) => [[0, 0], [1, 0], [1, 1], [0, 1]].map(([u, v]) => { const p = P(l, u, v); return `${p.x},${p.y}` }).join(' ')
const at = (l: number, i: Id) => P(l, pos[i][0], pos[i][1])

const titles = ['feature (input)', 'hidden layer 1', 'hidden layer 2', 'hidden layer cuối = embedding']

// links from layer l-1 (j) into layer l (i); "cone" = links A's layer-k vector depends on
const links = computed(() => {
  const out: { l: number, i: Id, j: Id, cone: boolean }[] = []
  for (let l = 1; l <= 3; l++)
    for (const i of ids)
      for (const j of [i, ...nb(i)])
        out.push({ l, i, j, cone: l <= k.value && hop[i] <= k.value - l })
  return out
})

// from Layer 2 on: the chain that carries the newly reached node's colour to A, one segment per layer
// (click 2: D -> B -> A, click 3: E -> D -> B -> A), drawn in that node's own colour
const STEP = 0.6  // seconds per segment
const trail = computed(() => {
  const src = ids.find(i => hop[i] === k.value)
  if (k.value < 2 || !src) return null
  const chain: Id[] = [src]
  while (chain[chain.length - 1] !== 'A') {
    const cur = chain[chain.length - 1]
    chain.push(nb(cur).find(j => hop[j] === hop[cur] - 1)!)
  }
  // arcs above the planes, so a segment never runs over another node
  const arc = (a: { x: number, y: number }, b: { x: number, y: number }) =>
    `M${a.x},${a.y} Q${(a.x + b.x) / 2},${Math.min(a.y, b.y) - 115} ${b.x},${b.y}`
  const segs = chain.slice(0, -1).map((j, l) => ({ d: arc(at(l, j), at(l + 1, chain[l + 1])), delay: l * STEP }))
  return { src, chain, segs, color: rgb(base[src]), done: segs.length * STEP }
})
// while a chain is shown, fade every node that is not on it (A stays: it is the subject)
const off = (l: number, i: Id) => !!trail.value && i !== 'A' && trail.value.chain[l] !== i

const caps = [
  'Layer 0 = feature: mỗi node chỉ biết chính nó (mỗi node một màu riêng)',
  'Layer 1: A lấy từ chính nó + B, C → hidden layer 1 (A thấy 1 bước)',
  'Đường đỏ: thông tin của D đi D → B (lớp 1) → A (lớp 2). A biết D dù không nối trực tiếp',
  'Đường tím: E → D → B → A. Cần 3 lớp thì thông tin của E mới tới A',
]
</script>

<template>
  <div>
    <svg viewBox="0 0 900 258" class="sl">
      <g v-for="l in [0, 1, 2, 3]" :key="'p' + l" :class="['fade', { on: l <= k }]">
        <polygon :points="plane(l)" class="plane" />
        <text :x="OX + l * GAP + 45" :y="OY - 18" class="ttl">Layer {{ l }}</text>
        <text :x="OX + l * GAP + 40" :y="OY + H + SY + 22" :class="['sub', { emb: l === 3, hid: l === 1 || l === 2 }]">{{ titles[l] }}</text>
        <line v-for="([a, b], e) in edges" :key="'e' + l + e" :x1="at(l, a).x" :y1="at(l, a).y" :x2="at(l, b).x" :y2="at(l, b).y" class="edge" />
      </g>

      <!-- links between consecutive layers -->
      <line v-for="(t, n) in links" :key="'x' + n" v-show="t.l <= k"
        :x1="at(t.l - 1, t.j).x" :y1="at(t.l - 1, t.j).y" :x2="at(t.l, t.i).x" :y2="at(t.l, t.i).y"
        :class="['link', { cone: t.cone, dim: t.cone && trail }]" />

      <!-- the highlighted chain: draws segment by segment, then keeps flowing towards A -->
      <g v-if="trail" :key="'trail' + k">
        <g v-for="(sg, n) in trail.segs" :key="n">
          <path :d="sg.d" pathLength="100" class="draw"
            :style="{ stroke: trail.color, animationDelay: sg.delay + 's' }" />
          <path :d="sg.d" class="flow"
            :style="{ stroke: trail.color, animationDelay: trail.done + 's, ' + trail.done + 's' }" />
        </g>
      </g>

      <!-- receptive field of A in layer 0 -->
      <circle v-for="i in ids" :key="'r' + i + k" v-show="hop[i] <= k && k > 0" :cx="at(0, i).x" :cy="at(0, i).y" r="19"
        :class="['ring', { src: trail && trail.src === i, off: off(0, i) }]" :style="trail && trail.src === i ? { stroke: trail.color } : {}" />

      <g v-for="l in [0, 1, 2, 3]" :key="'n' + l" :class="['fade', { on: l <= k }]">
        <g v-for="i in ids" :key="i">
          <g :class="{ off: off(l, i) }">
          <circle :key="i + l + k" :cx="at(l, i).x" :cy="at(l, i).y" :r="i === 'A' ? 14 : 12" :fill="rgb(layers[l][i])"
            :class="['node', { me: i === 'A', arrive: trail && i === 'A' && l === k }]"
            :style="trail && i === 'A' && l === k ? { animationDelay: trail.done + 's', stroke: trail.color } : {}" />
          <text :x="at(l, i).x" :y="at(l, i).y + 4" class="nl">{{ i }}</text>
          </g>
        </g>
      </g>
    </svg>
    <div class="cap"><span class="no">{{ k }}</span>{{ caps[k] }}</div>
  </div>
</template>

<style scoped>
.sl { width: 100%; height: auto; font-family: 'Be Vietnam Pro', sans-serif; }
.plane { fill: #f8fafc; fill-opacity: 0.85; stroke: #cbd5e1; stroke-width: 1.2; }
.ttl { font-size: 15px; font-weight: 700; fill: #111827; text-anchor: middle; }
.sub { font-size: 12px; fill: #374151; text-anchor: middle; }
.sub.hid { fill: #1e3a8a; font-weight: 700; }
.sub.emb { fill: #166534; font-weight: 700; }
.edge { stroke: #9ca3af; stroke-width: 1.5; }
.link { stroke: #cbd5e1; stroke-width: 1; stroke-dasharray: 3 3; }
.link.cone { stroke: #111827; stroke-width: 1.6; stroke-dasharray: 4 3; }
.link.cone.dim { opacity: 0.3; }
.off { opacity: 0.25; transition: opacity 0.4s; }
.ring { fill: none; stroke: #a51d1d; stroke-width: 2; stroke-dasharray: 4 2; }
.ring.src { stroke-width: 3.5; animation: pulse 0.9s ease-in-out 2; transform-box: fill-box; transform-origin: center; }
.draw, .flow { fill: none; }
.draw { stroke-width: 3.5; stroke-linecap: round; stroke-dasharray: 100; stroke-dashoffset: 100; animation: draw 0.6s linear forwards; }
.flow { stroke-width: 3.5; stroke-dasharray: 7 6; opacity: 0; animation: show 0.01s forwards, flow 1s linear infinite; }
.node.arrive { stroke-width: 4; animation: pulse 0.9s ease-in-out 2; transform-box: fill-box; transform-origin: center; }
@keyframes draw { to { stroke-dashoffset: 0; } }
@keyframes show { to { opacity: 1; } }
@keyframes flow { to { stroke-dashoffset: -13; } }
@keyframes pulse { 0%, 100% { transform: scale(1); } 50% { transform: scale(1.25); } }
.node { stroke: #374151; stroke-width: 1.2; transition: fill 0.4s; }
.node.me { stroke: #111827; stroke-width: 3; }
.nl { font-size: 11px; font-weight: 700; text-anchor: middle; fill: #fff; paint-order: stroke; stroke: #111827; stroke-width: 2.5px; }
.fade { opacity: 0.12; transition: opacity 0.5s ease; }
.fade.on { opacity: 1; }
.cap { text-align: center; font-size: 0.9rem; color: #111827; }
.no { display: inline-block; width: 1.3rem; height: 1.3rem; line-height: 1.3rem; text-align: center; border-radius: 50%;
  background: #1e3a8a; color: #fff; font-size: 0.7rem; margin-right: 0.45rem; }
</style>
