<script setup lang="ts">
// The lab: Proxmox VMs on two SDN VNets. Two scored Windows hosts, one model each (D12):
// ws-etw-0 (DVWA, main recorded demo, exp08; Domain Controller since 11-09) and ws-etw-1 (react2shell Next.js app, exp15-ws1).
// The Demo UI is drawn inside ubuntu-trainer (ocr-live serves it); the VPN VM itself is left out, only the VPN attacker is shown.
// Static (no clicks): ① Kali exploits DVWA · ② ETW logs ship to Loki · ③ ocr-live serves the alert UI · ④ real react2shell attack over VPN.
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
      <rect x="40" y="104" width="150" height="82" rx="8" />
      <text x="115" y="128" class="vm-t">ws-etw-0</text>
      <text x="115" y="147" class="vm-ip">10.21.3.10</text>
      <text x="115" y="168" class="vm-s">DVWA · DC</text>
    </g>

    <!-- ws-etw-1 -->
    <g class="vm">
      <rect x="206" y="104" width="150" height="82" rx="8" />
      <text x="281" y="128" class="vm-t">ws-etw-1</text>
      <text x="281" y="147" class="vm-ip">10.21.3.11</text>
      <text x="281" y="168" class="vm-s">Next.js (react2shell)</text>
    </g>

    <!-- ubuntu-trainer: Loki + one model per host + ocr-live, which serves the Demo UI (nested inside) -->
    <g class="vm det">
      <rect x="400" y="98" width="320" height="94" rx="8" />
      <text x="468" y="126" class="vm-t">ubuntu-trainer</text>
      <text x="468" y="145" class="vm-ip">10.21.3.20</text>
      <text x="468" y="164" class="vm-s">Loki · ocr-live</text>
      <text x="468" y="180" class="vm-s">OCRGCN × 2 host</text>
    </g>

    <!-- Demo UI, served by ocr-live on the trainer -->
    <g class="vm out">
      <rect x="556" y="112" width="148" height="66" rx="8" />
      <text x="630" y="140" class="vm-t">Demo UI</text>
      <text x="630" y="160" class="vm-s">window xanh / đỏ</text>
    </g>

    <!-- VNet it2102k -->
    <rect x="24" y="220" width="182" height="138" rx="9" class="vnet katk" />
    <text x="115" y="353" class="vnet-l" text-anchor="middle">it2102k · 10.21.4.0/24</text>
    <g class="vm atk">
      <rect x="40" y="248" width="150" height="86" rx="8" />
      <text x="115" y="274" class="vm-t">kali</text>
      <text x="115" y="293" class="vm-ip">10.21.4.30</text>
      <text x="115" y="314" class="vm-s">kịch bản của nhóm</text>
    </g>

    <!-- remote attacker over the VPN (outside the lab VNets) -->
    <g class="vm atk vpn">
      <rect x="216" y="252" width="150" height="86" rx="8" />
      <text x="291" y="278" class="vm-t">thành viên (VPN)</text>
      <text x="291" y="297" class="vm-ip">10.8.0.3</text>
      <text x="291" y="318" class="vm-s">tấn công thật</text>
    </g>

    <!-- flow 1: exploit kali -> ws-etw-0 -->
    <g class="flow atk">
      <path d="M 115 248 L 115 188" fill="none" marker-end="url(#li-atk)" />
      <text x="125" y="214" class="fl" text-anchor="start">① exploit DVWA</text>
    </g>
    <!-- flow 4: remote attacker -> ws-etw-1 -->
    <g class="flow atk">
      <path d="M 291 252 L 291 188" fill="none" marker-end="url(#li-atk)" />
      <text x="301" y="214" class="fl" text-anchor="start">④ react2shell RCE</text>
    </g>
    <!-- flow 2: logs from both hosts -> trainer -->
    <g class="flow det">
      <path d="M 356 145 L 400 145" fill="none" marker-end="url(#li-det)" />
      <path d="M 115 104 L 115 88 L 430 88 L 430 98" fill="none" marker-end="url(#li-det)" />
      <text x="378" y="136" class="fl">②</text>
      <text x="300" y="91" class="fl">② log ETW (cả hai host)</text>
    </g>
    <!-- flow 3: alert ocr-live -> Demo UI -->
    <g class="flow out">
      <path d="M 506 145 L 556 145" fill="none" marker-end="url(#li-out)" />
      <text x="531" y="136" class="fl">③</text>
    </g>

    <!-- legend for the empty bottom-right area -->
    <text x="560" y="262" class="lg">① ② ③ demo chính (video 1)</text>
    <text x="560" y="284" class="lg">④ ② ③ kết quả thứ hai (video 2)</text>
    <text x="560" y="306" class="lg">mỗi host một mô hình</text>

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
.vm.vpn rect { stroke-dasharray: 4 3; }
.vm.out rect { fill: #f0fdf4; stroke: #74c58a; }
.vm-t { font-size: 13px; font-weight: 700; text-anchor: middle; fill: #111827; }
.vm-s { font-size: 11.5px; text-anchor: middle; fill: #475569; }
.vm-ip { font-size: 11px; text-anchor: middle; fill: #334155; font-family: 'JetBrains Mono', monospace; }
.lg { font-size: 11.5px; fill: #475569; }
.flow.atk path { stroke: #a51d1d; stroke-width: 2.4; }
.flow.det path { stroke: #1e40af; stroke-width: 2.4; }
.flow.out path { stroke: #166534; stroke-width: 2.4; }
.fl { font-size: 11px; font-weight: 700; text-anchor: middle; paint-order: stroke; stroke: #fff; stroke-width: 3.5px; }
.flow.atk .fl { fill: #a51d1d; }
.flow.det .fl { fill: #1e40af; }
.flow.out .fl { fill: #166534; }
</style>
