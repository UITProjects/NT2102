<script setup lang="ts">
// The lab for the demo: Proxmox VMs on two SDN VNets. One scored Windows host (ws-etw-0).
// The Demo UI is drawn inside ubuntu-trainer (ocr-live serves it); the VPN VM is left out (not part of the demo).
// Static (no clicks): ① Kali exploits DVWA · ② ETW logs ship to Loki · ③ ocr-live serves the alert UI.
// Each box = name + one short description; implementation details live in the speaker notes and the report.
</script>

<template>
  <svg viewBox="0 0 760 372" class="li">
    <!-- Proxmox outer frame -->
    <rect x="8" y="24" width="744" height="340" rx="12" class="pve" />
    <text x="22" y="44" class="pve-l">Proxmox hypervisor</text>

    <!-- VNet it2102 -->
    <rect x="24" y="56" width="712" height="150" rx="9" class="vnet" />
    <text x="38" y="76" class="vnet-l">VNet it2102 · 10.21.3.0/24</text>

    <!-- ws-etw-0 -->
    <g class="vm">
      <rect x="40" y="98" width="160" height="88" rx="8" />
      <text x="120" y="124" class="vm-t">ws-etw-0</text>
      <text x="120" y="144" class="vm-ip">10.21.3.10</text>
      <text x="120" y="166" class="vm-s">Windows · DVWA</text>
    </g>

    <!-- ubuntu-trainer: Loki + model + ocr-live, which serves the Demo UI (nested inside) -->
    <g class="vm det">
      <rect x="268" y="92" width="448" height="100" rx="8" />
      <text x="358" y="124" class="vm-t">ubuntu-trainer</text>
      <text x="358" y="144" class="vm-ip">10.21.3.20</text>
      <text x="358" y="166" class="vm-s">Loki · OCRGCN · ocr-live</text>
    </g>

    <!-- Demo UI, served by ocr-live on the trainer -->
    <g class="vm out">
      <rect x="532" y="108" width="168" height="68" rx="8" />
      <text x="616" y="137" class="vm-t">Demo UI</text>
      <text x="616" y="158" class="vm-s">window xanh / đỏ</text>
    </g>

    <!-- VNet it2102k -->
    <rect x="24" y="220" width="320" height="130" rx="9" class="vnet katk" />
    <text x="332" y="240" class="vnet-l" text-anchor="end">VNet it2102k · 10.21.4.0/24</text>
    <g class="vm atk">
      <rect x="40" y="250" width="160" height="88" rx="8" />
      <text x="120" y="276" class="vm-t">kali</text>
      <text x="120" y="296" class="vm-ip">10.21.4.30</text>
      <text x="120" y="318" class="vm-s">máy tấn công</text>
    </g>

    <!-- flow 1: exploit kali -> ws-etw-0 -->
    <g class="flow atk">
      <path d="M 120 250 L 120 188" fill="none" marker-end="url(#li-atk)" />
      <text x="132" y="214" class="fl" text-anchor="start">① exploit → RCE</text>
    </g>
    <!-- flow 2: logs ws-etw-0 -> trainer -->
    <g class="flow det">
      <path d="M 200 142 L 268 142" fill="none" marker-end="url(#li-det)" />
      <text x="234" y="132" class="fl">② log ETW</text>
    </g>
    <!-- flow 3: alert ocr-live -> Demo UI -->
    <g class="flow out">
      <path d="M 450 142 L 532 142" fill="none" marker-end="url(#li-out)" />
      <text x="491" y="132" class="fl">③ cảnh báo</text>
    </g>

    <defs>
      <marker v-for="m in [['li-atk', '#a51d1d'], ['li-det', '#1e40af'], ['li-out', '#166534']]" :key="m[0]"
        :id="m[0]" viewBox="0 0 10 10" refX="9" refY="5" markerUnits="userSpaceOnUse"
        markerWidth="10" markerHeight="10" orient="auto-start-reverse">
        <path d="M0,0 L10,5 L0,10 z" :fill="m[1]" />
      </marker>
    </defs>
  </svg>
</template>

<style scoped>
.li { width: 100%; height: auto; font-family: 'Be Vietnam Pro', sans-serif; }
.pve { fill: #fbfcfe; stroke: #cbd5e1; stroke-width: 1.3; stroke-dasharray: 5 4; }
.pve-l { font-size: 12px; fill: #64748b; font-weight: 700; }
.vnet { fill: #f4f7fd; stroke: #bcccea; stroke-width: 1.2; }
.vnet.katk { fill: #fdf5f5; stroke: #e7c3c3; }
.vnet-l { font-size: 11.5px; fill: #475569; font-weight: 700; }
.vm rect { fill: #fff; stroke: #374151; stroke-width: 1.3; }
.vm.det rect { fill: #eff4fe; stroke: #6f8fdf; }
.vm.atk rect { fill: #fef2f2; stroke: #d98888; }
.vm.out rect { fill: #f0fdf4; stroke: #74c58a; }
.vm-t { font-size: 13px; font-weight: 700; text-anchor: middle; fill: #111827; }
.vm-s { font-size: 11.5px; text-anchor: middle; fill: #475569; }
.vm-ip { font-size: 11px; text-anchor: middle; fill: #334155; font-family: 'JetBrains Mono', monospace; }
.flow.atk path { stroke: #a51d1d; stroke-width: 2.4; }
.flow.det path { stroke: #1e40af; stroke-width: 2.4; }
.flow.out path { stroke: #166534; stroke-width: 2.4; }
.fl { font-size: 11px; font-weight: 700; text-anchor: middle; paint-order: stroke; stroke: #fff; stroke-width: 3.5px; }
.flow.atk .fl { fill: #a51d1d; }
.flow.det .fl { fill: #1e40af; }
.flow.out .fl { fill: #166534; }
</style>
