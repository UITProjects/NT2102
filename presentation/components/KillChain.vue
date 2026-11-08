<script setup lang="ts">
// The one RCE kill chain, delivered from Kali to DVWA on ws-etw-0.
// Steps + ATT&CK ids from src/atomic-attack-pack/scenarios/rce-killchain.ps1 (header).
// Clicks reveal the steps in order; each names the graph nodes it creates.
import { computed } from 'vue'
import { useSlideContext } from '@slidev/client'

const { $clicks } = useSlideContext()
const step = computed(() => $clicks.value)

const steps = [
  { id: 'T1190 · T1059.001', t: 'Initial access / Execution', d: 'Khai thác DVWA OS-command-injection → RCE',
    n: 'httpd.exe → cmd.exe → powershell.exe' },
  { id: 'T1033 T1082 T1057 T1016', t: 'Discovery', d: 'whoami, systeminfo, tasklist, net, ipconfig',
    n: 'nhiều short-lived PROCESS node' },
  { id: 'T1046', t: 'Network service scan', d: 'Quét subnet 10.21.3.0/24 + Kali, nhiều cổng',
    n: 'nhiều NET node (…:22)' },
  { id: 'T1105 · T1569.002', t: 'Ingress tool transfer + run', d: 'Tải & CHẠY PsExec, rclone (PE thật)',
    n: 'PROCESS + socket mới' },
  { id: 'T1552 · T1213', t: 'Credential access / DB', d: 'MySQL root không mật khẩu: enumerate + dump',
    n: 'mysql.exe + 127.0.0.1:3306' },
  { id: 'T1071', t: 'C2 beacon', d: 'Gọi về định kỳ, lặp để kéo dài',
    n: 'NET node <attacker>:8080' },
  { id: 'T1053.005', t: 'Persistence', d: 'Scheduled task',
    n: 'UpdaterSync' },
  { id: 'T1070.001 · T1070.004', t: 'Defense evasion', d: 'Xoá event log (wevtutil), xoá tool đã tải',
    n: 'wevtutil.exe + .evtx' },
]
</script>

<template>
  <div class="kc">
    <div v-for="(s, i) in steps" :key="i" :class="['row', { on: step >= i + 1, now: step === i + 1 }]">
      <div class="badge">{{ s.id }}</div>
      <div class="body">
        <span class="t">{{ s.t }}</span>
        <span class="d">{{ s.d }}</span>
      </div>
      <div class="nodes">→ {{ s.n }}</div>
    </div>
  </div>
</template>

<style scoped>
.kc { display: flex; flex-direction: column; gap: 0.3rem; }
.row {
  display: grid; grid-template-columns: 168px 1fr 215px; align-items: center; gap: 0.6rem;
  padding: 0.28rem 0.6rem; border-left: 3px solid #e5e7eb; background: #fafbfd;
  opacity: 0.28; transform: translateX(-8px);
  transition: opacity 0.4s ease, transform 0.4s ease, border-color 0.4s ease, background 0.4s ease;
}
.row.on { opacity: 1; transform: none; border-left-color: #a51d1d; background: #fef5f5; }
.row.now { background: #fde3e3; box-shadow: 0 1px 6px rgba(165, 29, 29, 0.18); }
.badge {
  font-family: 'JetBrains Mono', monospace; font-size: 0.66rem; font-weight: 700; color: #7f1d1d;
  background: #fde3e3; border-radius: 4px; padding: 0.12rem 0.3rem; text-align: center; line-height: 1.25;
}
.body { font-size: 0.82rem; }
.body .t { font-weight: 700; color: #111827; }
.body .d { display: block; font-size: 0.74rem; color: #475569; }
.nodes { font-family: 'JetBrains Mono', monospace; font-size: 0.68rem; color: #166534; text-align: right; }
</style>
