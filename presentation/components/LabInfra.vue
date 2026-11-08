<script setup lang="ts">
// The lab for the demo: Proxmox VMs on two SDN VNets. One scored Windows host (ws-etw-0).
// Clicks: 1 Kali exploits DVWA · 2 ETW logs ship to Loki · 3 ocr-live serves the alert UI.
import { computed } from 'vue'
import { useSlideContext } from '@slidev/client'

const { $clicks } = useSlideContext()
const step = computed(() => $clicks.value)
</script>

<template>
  <svg viewBox="0 0 760 392" class="li">
    <!-- Proxmox outer frame -->
    <rect x="8" y="24" width="744" height="360" rx="12" class="pve" />
    <text x="22" y="44" class="pve-l">Proxmox hypervisor (home lab)</text>

    <!-- VNet it2102 -->
    <rect x="24" y="56" width="712" height="150" rx="9" class="vnet" />
    <text x="38" y="76" class="vnet-l">VNet it2102 · 10.21.3.0/24</text>

    <!-- ws-etw-0 -->
    <g class="vm">
      <rect x="40" y="90" width="160" height="104" rx="8" />
      <text x="120" y="112" class="vm-t">ws-etw-0 · .10</text>
      <text x="120" y="131" class="vm-s">Windows Server 2022</text>
      <text x="120" y="150" class="vm-b">DVWA (XAMPP :3000)</text>
      <text x="120" y="167" class="vm-b">etwcollect (ETW →</text>
      <text x="120" y="183" class="vm-b">Kernel Proc/File/Net)</text>
    </g>

    <!-- ubuntu-trainer -->
    <g class="vm det">
      <rect x="268" y="90" width="180" height="104" rx="8" />
      <text x="358" y="112" class="vm-t">ubuntu-trainer · .20</text>
      <text x="358" y="131" class="vm-s">Loki · Grafana</text>
      <text x="358" y="150" class="vm-b">huấn luyện + scaler</text>
      <text x="358" y="167" class="vm-b">ocr-live (FastAPI +</text>
      <text x="358" y="183" class="vm-b">OCRGCN · Alg.1 · LLM)</text>
    </g>

    <!-- Demo UI -->
    <g class="vm out">
      <rect x="556" y="104" width="160" height="78" rx="8" />
      <text x="636" y="130" class="vm-t">Demo UI (:8000)</text>
      <text x="636" y="150" class="vm-b">trình duyệt web</text>
      <text x="636" y="167" class="vm-b">SSE · cảnh báo</text>
    </g>

    <!-- VNet it2102k -->
    <rect x="24" y="220" width="320" height="150" rx="9" class="vnet katk" />
    <text x="38" y="240" class="vnet-l">VNet it2102k · 10.21.4.0/24</text>
    <g class="vm atk">
      <rect x="40" y="252" width="160" height="100" rx="8" />
      <text x="120" y="278" class="vm-t">kali · .30</text>
      <text x="120" y="300" class="vm-s">Kali attacker</text>
      <text x="120" y="322" class="vm-b">launch_exploit.py</text>
    </g>

    <!-- nt2102-vpn (context, no data-path arrow) -->
    <g class="vm">
      <rect x="560" y="250" width="156" height="84" rx="8" />
      <text x="638" y="278" class="vm-t">nt2102-vpn · .50</text>
      <text x="638" y="300" class="vm-b">OpenVPN</text>
      <text x="638" y="318" class="vm-b">(truy cập từ xa)</text>
    </g>

    <!-- flow 1: exploit kali -> ws-etw-0 -->
    <g :class="['flow', 'atk', { on: step >= 1 }]">
      <path d="M 120 252 L 120 196" fill="none" marker-end="url(#li-atk)" />
      <text x="132" y="214" class="fl" text-anchor="start">① exploit → RCE</text>
    </g>
    <!-- flow 2: logs ws-etw-0 -> trainer -->
    <g :class="['flow', 'det', { on: step >= 2 }]">
      <path d="M 200 142 L 268 142" fill="none" marker-end="url(#li-det)" />
      <text x="234" y="132" class="fl">② log ETW</text>
    </g>
    <!-- flow 3: alert trainer -> UI -->
    <g :class="['flow', 'out', { on: step >= 3 }]">
      <path d="M 448 142 L 556 142" fill="none" marker-end="url(#li-out)" />
      <text x="502" y="132" class="fl">③ cảnh báo</text>
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
.vm-t { font-size: 12.5px; font-weight: 700; text-anchor: middle; fill: #111827; }
.vm-s { font-size: 11px; text-anchor: middle; fill: #475569; }
.vm-b { font-size: 10.5px; text-anchor: middle; fill: #4b5563; font-family: 'JetBrains Mono', monospace; }
.flow { opacity: 0; transition: opacity 0.5s ease; }
.flow.on { opacity: 1; }
.flow.atk path { stroke: #a51d1d; stroke-width: 2.4; }
.flow.det path { stroke: #1e40af; stroke-width: 2.4; }
.flow.out path { stroke: #166534; stroke-width: 2.4; }
.fl { font-size: 11px; font-weight: 700; text-anchor: middle; paint-order: stroke; stroke: #fff; stroke-width: 3.5px; }
.flow.atk .fl { fill: #a51d1d; }
.flow.det .fl { fill: #1e40af; }
.flow.out .fl { fill: #166534; }
</style>
