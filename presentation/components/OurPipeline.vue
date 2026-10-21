<script setup lang="ts">
// Our live pipeline, redrawn from fig:our-pipeline in latex/contents.tex (same TikZ coordinates).
// A dot = one log line: SilkETW -> Alloy -> Loki -> steps 1-4 -> UI, looping. Numbers match the paper's
// 5 steps (Fig. 1), so each block can be compared with its paper counterpart.
const SX = 64
const SY = 56
const X = (x: number) => 100 + x * SX
const Y = (y: number) => 52 - y * SY
const W = 176
const H = 44

interface B { id: string, x: number, y: number, c: string, t: string, s: string, n?: number }
const boxes: B[] = [
  { id: 'etw', x: 0, y: 0, c: 'prep', t: 'SilkETW', s: 'kernel log: process, file, network' },
  { id: 'alloy', x: 0, y: -1.8, c: 'prep', t: 'Grafana Alloy', s: 'gửi nguyên văn từng dòng' },
  { id: 'loki', x: 3.6, y: 0, c: 'prep', t: 'Loki', s: 'kho log (nhãn: run, provider)' },
  { id: 's1', x: 7.0, y: 0, c: 'prep', t: 'Graph + window 5 phút', s: 'lược đồ NODLINK W10', n: 1 },
  { id: 's2', x: 10.4, y: 0, c: 'prep', t: '15 behavior feature', s: 'thang đo của tập train', n: 2 },
  { id: 's3', x: 10.4, y: -1.8, c: 'det', t: 'OCRGCN theo loại node', s: 'chấm điểm từng node', n: 3 },
  { id: 's4', x: 7.0, y: -1.8, c: 'det', t: 'Alg. 1 + lọc điểm', s: 'subgraph ≥ 30?', n: 4 },
  { id: 's5', x: 3.6, y: -1.8, c: 'inv', t: 'LLM: phân loại + báo cáo', s: '(tuỳ chọn)', n: 5 },
  { id: 'ui', x: 7.0, y: -3.6, c: 'out', t: 'Giao diện web', s: 'window xanh / đỏ' },
]
const at = Object.fromEntries(boxes.map(b => [b.id, b]))
const cx = (id: string) => X(at[id].x)
const cy = (id: string) => Y(at[id].y)

// Arrows between box borders (straight segments, as in the TikZ figure).
const arrows = [
  { d: `M ${cx('etw')} ${cy('etw') + H / 2} V ${cy('alloy') - H / 2}` },
  { d: `M ${cx('alloy') + W / 2} ${cy('alloy') - 6} L ${cx('loki') - W / 2} ${cy('loki') + 6}`, label: 'HTTP', lx: X(3.6) - W / 2 - 14, ly: 72 },
  { d: `M ${cx('loki') + W / 2} ${cy('loki')} H ${cx('s1') - W / 2}` },
  { d: `M ${cx('s1') + W / 2} ${cy('s1')} H ${cx('s2') - W / 2}` },
  { d: `M ${cx('s2')} ${cy('s2') + H / 2} V ${cy('s3') - H / 2}` },
  { d: `M ${cx('s3') - W / 2} ${cy('s3')} H ${cx('s4') + W / 2}` },
  { d: `M ${cx('s4') - W / 2} ${cy('s4')} H ${cx('s5') + W / 2}`, opt: true },
  { d: `M ${cx('s4')} ${cy('s4') + H / 2} V ${cy('ui') - H / 2}` },
  { d: `M ${cx('s5')} ${cy('s5') + H / 2} V ${cy('ui')} H ${cx('ui') - W / 2}`, opt: true },
]
// Path the "log line" dot follows (box centres).
const flow = ['etw', 'alloy', 'loki', 's1', 's2', 's3', 's4', 'ui'].map(id => `${cx(id)},${cy(id)}`).join(' L ')

const frames = [
  { l: X(0) - W / 2 - 10, t: Y(0) - H / 2 - 22, r: X(0) + W / 2 + 10, b: Y(-1.8) + H / 2 + 10, label: 'ws-etw (Windows)' },
  { l: X(3.6) - W / 2 - 10, t: Y(0) - H / 2 - 22, r: X(10.4) + W / 2 + 10, b: Y(-3.6) + H / 2 + 10, label: 'ubuntu-trainer' },
]
</script>

<template>
  <svg viewBox="0 0 870 300" class="pl">
    <defs>
      <marker id="pl-arr" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="6" markerHeight="6" orient="auto-start-reverse">
        <path d="M0,0 L10,5 L0,10 z" fill="#1f2937" />
      </marker>
    </defs>
    <g v-for="f in frames" :key="f.label">
      <rect :x="f.l" :y="f.t" :width="f.r - f.l" :height="f.b - f.t" rx="8" class="frame" />
      <text :x="f.l + 8" :y="f.t + 14" class="flabel">{{ f.label }}</text>
    </g>
    <g v-for="(a, i) in arrows" :key="i">
      <path :d="a.d" :class="['arr', { opt: a.opt }]" marker-end="url(#pl-arr)" />
      <text v-if="a.label" :x="a.lx" :y="a.ly" class="alabel" text-anchor="end">{{ a.label }}</text>
    </g>
    <g v-for="b in boxes" :key="b.id">
      <rect :x="X(b.x) - W / 2" :y="Y(b.y) - H / 2" :width="W" :height="H" rx="5" :class="['box', b.c]" />
      <text :x="X(b.x)" :y="Y(b.y) - 3" class="bt">{{ b.t }}</text>
      <text :x="X(b.x)" :y="Y(b.y) + 13" class="bs">{{ b.s }}</text>
      <g v-if="b.n">
        <circle :cx="X(b.x) - W / 2" :cy="Y(b.y) - H / 2" r="9" class="num" />
        <text :x="X(b.x) - W / 2" :y="Y(b.y) - H / 2 + 4" class="numt">{{ b.n }}</text>
      </g>
    </g>
    <circle r="5" class="dot">
      <animateMotion dur="7s" repeatCount="indefinite" :path="`M ${flow}`" keyPoints="0;1" keyTimes="0;1" calcMode="linear" />
    </circle>
  </svg>
</template>

<style scoped>
.pl { width: 100%; height: auto; font-family: 'Be Vietnam Pro', sans-serif; }
.frame { fill: none; stroke: #9ca3af; stroke-dasharray: 5 4; }
.flabel { font-family: 'JetBrains Mono', monospace; font-size: 11px; fill: #6b7280; }
.box { stroke: #374151; stroke-width: 1.1; }
.box.prep { fill: #eeeeee; }
.box.det { fill: #dbe4fb; }
.box.inv { fill: #fde3c7; }
.box.out { fill: #d6f0d6; }
.bt { font-size: 12px; font-weight: 600; text-anchor: middle; fill: #111827; }
.bs { font-size: 10px; text-anchor: middle; fill: #4b5563; }
.arr { fill: none; stroke: #1f2937; stroke-width: 1.5; }
.arr.opt { stroke-dasharray: 5 4; }
.alabel { font-size: 11px; fill: #1f2937; font-weight: 600; }
.num { fill: #1e3a8a; }
.numt { font-size: 11px; font-weight: 700; fill: #fff; text-anchor: middle; }
.dot { fill: #a51d1d; opacity: 0.7; }
</style>
