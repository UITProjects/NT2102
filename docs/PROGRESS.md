# PROGRESS (status as of 2026-11-07)

Replace lines in place when status changes; no history. "Verified" = checked in code/data.
Branch: **`main`** (retrain merged, PR #9). All work on the Proxmox VMs.

## Next action (user, 2026-11-07)

**Verify the `ws-etw-1` model and the correlation layer:** `docs/notes/verify-two-host.md`. Retrain data must be pulled by about
2026-11-09 16:00Z. Steps for training: `docs/notes/training-runbook.md`.

## 2026-11-07 one model per host + cross-host correlation: built, NOT deployed (selftests + UI check only)

On branch `trainer-monitoring-guard`, not pushed, not on the trainer. Built: `lab promote --host`, `lab hosts`, `lab flows`, one
engine per VM, `src/live/correlate.py` (D13), multi-host LLM report, UI ("X is trained for Y" line, VM tabs, one timeline row per
VM, Cross-host activity panel, History VM column). Checked: `./selftest.sh` 19/19; real `live_server.py` startup in one- and
two-host mode; UI in Playwright on fake data (one-VM, two-VM, no baseline, host not recorded). A selftest confirms a VM's
windows match a plain engine. Not yet: real two-VM run, trainer memory with two engines, a replay of a saved `ws-etw-0` session
in both modes, `lab flows` on real data.

To go live: push, `ansible-playbook -i proxmox.ini ubuntu.yml --tags project -e repo_branch=trainer-monitoring-guard`, then on the
trainer `lab promote exp08 --host ws-etw-0`, `lab promote exp09-ws1 --host ws-etw-1`, `lab flows --start ... --end ...`,
`lab hosts ws-etw-0 ws-etw-1`, `sudo systemctl restart ocr-live`, and watch memory in Grafana during catch-up. Until `lab hosts` is
run, `ocr-live` stays on exp08 and `ws-etw-0`.

## 2026-11-07 exp09-ws1 trained for `ws-etw-1`: PROVISIONAL, not promoted (verified on the trainer)

One model per host (user, 2026-11-07): `ws-etw-0` keeps exp08, `ws-etw-1` gets its own. Experiment
`range-20261106T170000Z-ws-etw-1`, 11-06 17:00Z to 11-07 03:00Z, 298 windows, 20 excluded (the two 6-h rotation canaries:
`src/artifacts/exclusions/ws-etw-1-20261107.json`). Label source: operator assertion, backed by a scan of all 495 process
starts: no RDP, WinRM, installer or choco; the 19:05Z kfile burst is Defender (`MpCmdRun`), kept as normal OS work.
Settings as exp08 (`--layers 2 --val-windows 60 --tnr-tolerance 0.01`), 3 seeds, converter match.

| Type | n_train (exp08) | best_ep seed 360 / 361 / 362 | τ seed 360 / 361 / 362 | val_fpr seed 360 |
|---|---|---|---|---|
| file | 13,809 (147,021) | 99 / 99 / 99 | 0.008 / 0.196 / -0.001 | 0.0020 |
| process | 1,692 (15,970) | 16 / 99 / 21 | 0.277 / -0.081 / -0.055 | 0.0089 |
| net | 374 (5,107) | 13 / 4 / 3 | -0.036 / 1.196 / 2.187 | 0.0225 |

FILE is fine. PROCESS and NET stop early and NET's τ is seed-dependent, so this does not meet exp08's standard (best_ep 99 on all
types, val_fpr 0). `--patience 20` gave identical results, so it is not an early-stop setting. Cause: the 2 NET outliers
(+3.47, +2.25; train max -0.00) are one benign job, `mousocoreworker.exe` -> `57.154.63.210:443` at 01:20Z, which appears only in
the validation tail. 9 of 17 validation NET peers are absent from training. The baseline is too short; do not tune around it.

Next: retrain on about 3 days. Loki keeps 72 h, so pull before the 11-06 17:00Z data expires: about 11-09 16:00Z, new
exclusions for later rotations. Until then exp09-ws1 can stand in for building Part B; expect false alarms on `ws-etw-1`.
`lineage.json` `hosts` is null for single-host builds: Part B's `lab promote --host` guard needs that recorded.

## 2026-11-07 trainer resized, monitoring live and protected from training (verified on the trainer)

Trainer resized by the user to 8 cores / 12 GB (`main.tf`, applied; the VM rebooted itself, about 09:42 +07) with 50 GiB of swap.
Role `ubuntu_monitoring`: Prometheus, node-exporter and cAdvisor in their own compose project, plus the Grafana dashboard
**Trainer resources**. Checked: 3 targets up, every panel query returned data, re-run `changed=0`, ports 9090/9100/8088
closed from the workstation.

Guard (`src/ansible/README.md`): SSH work capped in `user.slice` (50 % RAM, 40 GiB swap, 7 of 8 cores); monitoring has 864 MiB
reserved, no swap, OOM priority -500. Training peak measured at 2,524 MiB (3-day range, 1 seed, 3 epochs).
Stress test from an SSH session (9 GiB allocation + 8 CPU burners, about 48 s): `user.slice` held at 5.4 GB, 3.9 GB went to
swap, Grafana and Prometheus answered in under 0.03 s, no missed scrape, 0 monitoring swap, 0 OOM kills, `ocr-live` 200.

Not tested: the kill at the swap limit, RAM pressure across the whole VM (4.8 GB stayed available, so the reservation never
had to act), a sustained load, and whether monitoring `swap.max` = 0 survives a reboot. Grafana first came up with `max` despite
a correct Docker config (cause unknown; a recreate fixed it).

## 2026-11-06 exp08 live alert count, first 24 h (verified in `history.jsonl`)

11-05 16:00Z to 11-06 15:55Z: 288 distinct windows, 279 fully scored, **7 alerts**. 4 are attack windows from 3 live runs
(17:10 sg 70.17; 00:45 19.24; 00:50 37.62; 15:35 54.42). **3 are false alarms**, all LLM `false_positive`: 22:25 (21.20, lone
powershell loading runtime DLLs), 15:00 (10.82) and 15:50 (46.97), the last two during Ansible/git/choco work on the VM.
So "benign windows green" below holds around each attack run, not for every window. Label source: operator knowledge of run
times + LLM verdicts, not ground truth. History rows repeat across session restarts, so dedupe by `start_utc` before counting.

## 2026-11-06 branch `attack-t1070-and-llm-leak-fix`: LLM-leak fix + T1070 phase (VERIFIED on VMs; awaiting merge)

Two changes on this branch, deployed to the VMs from the feature branch and verified live before the
merge to `main`. **Deploy done** (trainer `831895f` + ocr-live restarted, both collectors + Kali pack
updated, verify-windows.yml pass, terraform plan clean, selftest 18/18). **Verified:**
- Leak fix (offline re-judge of the 11-06T00:14Z session, exp08): the `c:\lab-labels\*` node is dropped
  from the kept subgraph AND the LLM document; the main attack window flips LLM `uncertain`(0.4)→`attack`
  (0.94); the beacon-tail window stays `uncertain` on its own thin evidence (beacon + mysql), which the
  detector reds regardless (LLM is an overlay, D9). alg1 re-judge path also patched (`831895f`).
- Live attack + T1070 (window `20261106T153500Z`, exp08): **RED, sg 54.42, Moderate, 1 kept subgraph,
  LLM `attack` 0.9**; benign windows green. Kept subgraph = C2 `10.21.4.30:8080`, `mysql.exe` dump,
  executed tools (PsExec/PSEXESVC, rclone, nmap, ncat). T1070 ran to completion (T1070.001 + T1070.004
  logged last); `wevtutil.exe` captured but **in-distribution** (not flagged) → not in the subgraph, does
  not lift the alert (per-node feature limit, D5/D8). See DEVIATIONS D6.

Two changes on this branch:
- **LLM-leak fix** (`b53c41e`, `src/pipeline/lab_filters.py`): drops our own `c:\lab-labels\attack-labels.jsonl`
  FILE node (keeping the attacker's writing process) from the training build **and live scoring**, before
  Alg. 1 + the LLM doc. Fixes the 2026-11-05 C2 case where the detector alerted RED but the LLM read
  "attack-labels" as a harness and returned `uncertain` (D9). Post-conversion ⇒ converter sha still
  `cbbf6dc`, **exp08 not invalidated, no retrain**.
- **T1070 anti-forensics phase** added to `scenarios/rce-killchain.ps1` after persistence: T1070.001 clear
  Windows event logs (`wevtutil.exe` → new unseen process + `.evtx` FILE nodes) and T1070.004 delete the
  downloaded ingress tools. Safe for collection (etwcollect reads live ETW, not `.evtx`).

Deploy/verify sequence: push the branch → `ubuntu.yml --tags project` + `windows.yml` + `kali.yml` with
`-e repo_branch=attack-t1070-and-llm-leak-fix` → `verify-windows.yml` → offline `rejudge.sh` of a 11-05 C2
session (expect LLM `uncertain`→`attack`, node gone) + a live attack run (expect RED + `attack` + the
`wevtutil.exe` node). Then open a PR to `main` for the user to merge; re-point the VMs at `main` after.

## 2026-11-06 second Windows VM: live and pushing to Loki (verified)

`ubuntu-etw` is replaced by `ws-etw-1` (VM 2107, 10.21.3.11, manual install, collector only, no DVWA) and `ws-etw` is
renamed `ws-etw-0` in place. Verified 13:49Z: Loki carries kproc, kfile and knet from both hosts, `verify-windows.yml`
passes on both (DVWA checked on `ws-etw-0`), `ocr-live` runs `--loki-host ws-etw-0` and stayed on that run with both
hosts pushing, `lab pull --list` shows both. Added `lab --host` (one host or both) and `merge_experiments()` (D12):
selftests only, not yet run on real two-host data. Superseded 2026-11-07 by one model per host (D12).

Open: the trainer checkout is on branch `provide-a-second-windows`, so after merging set it back to `main`, or pass
`-e repo_branch=` on `ubuntu.yml`. Keep 13:33-13:47Z (11-06, provisioning) out of idle training pulls. Lines before
13:34Z are labelled `host="ws-etw"`. The attack has not been re-run since `.11` became Windows.

## 2026-11-05 live attack run (exp07): RED but FALSE POSITIVE, attack missed — root-caused + exp08 retrain (IN PROGRESS)

A live DVWA→RCE run (session `live-20261105T120407Z`, exp07) delivered cleanly and was fully captured, but
both attack windows (15:15, 15:20) went RED **for the wrong reason**: the kept subgraph was benign (TiWorker
servicing at 15:15; our own `start-live.ps1` powershell watchdog at 15:20), LLM `false_positive` 0.95/0.72,
while the attack's own nodes scored in-distribution (rclone −1.40, PsExec −1.46, C2 socket −1.50, scan −1.56;
all flagged=False). Not a reproduction of the 00:50Z success.

**Root cause (verified against the 00:50Z success, same exp07/τ):** (1) `sg_score = Σ prob over flagged
nodes` (`alg1.py:170`) ⇒ score ∝ flagged-node **count**, so big benign clusters outrank the attack — in
`history.jsonl` every alerting window is an LLM-confirmed FP except the one 18-node 00:50 success. (2) The
attack rests on one NET node (C2 socket) whose 3-hop RGCN embedding flipped +1.165 (flagged) → −1.50
(in-distribution) with near-identical own features, because no feature encodes peer-IP novelty. Full write-up
`docs/notes/subgraph-score-fragility.md`, DEVIATIONS D8/D5.

**Changes (branch `fix/sg-det-fragility-exp08`):** (a) watchdog excluded in the converter
(`etw2nodlink.own_tooling_pids` / `filter_own_tooling_csv`, from `build_experiment` + `engine._score`;
validated on the real 15:20 window — drops the p2944 watchdog + 265 edges, keeps the attack PowerShell);
(b) **exp08** on a 3-day idle baseline (`range-3d-20261105`, 11-02 15:38Z → 11-05 15:50Z, attacks/RDP/canaries
excluded via `exclusions/proxmox-20261105.json`) with the RGCN stepped **3→2 layers** (`--layers 2`; 1 layer
`exp09` only if 2 helps but not enough).

**Result (2026-11-06, exp08 PROMOTED + live on the trainer): the retrain detects the attack exp07 missed,
and removes the watchdog FP.** Build: `range-3d-20261105` (11-02 17:10Z → 11-05 15:49Z, 2118 windows, 2.59 M
edges, check ok); watchdog filter dropped 252,427 edges / 907 process instances from the baseline. Gate PASS
(seed 360, `--layers 2`): best_ep 99 all types, τ file −0.070 / net −0.132 / process −0.163, val_fpr 0,
emb_std 0.093 / 0.098 / 0.117, converter_check match. **Offline re-score of the 2026-11-05 15:xx attack
windows (deterministic):** the attack nodes flip from in-distribution (exp07) to **flagged** under exp08 —
C2 socket `10.21.4.30:8080` −1.50→**+1.20**, scan `:22` −1.56→**+0.66**, `rclone.exe` −1.40→**+1.71**,
`PsExec64.exe` −1.46→**−0.12 (flagged)** — and the 15:15 kept subgraph flips from benign Windows-servicing
packages (exp07) to **powershell + rclone + mysql (the attack + DVWA DB)**; at 15:20 flagged dropped 38→23
(watchdog gone). Live idle windows under exp08 stay **green** (16:15–17:00Z all 0.00). Caveat: all three
changes (3-day baseline, watchdog filter, 2-layer) were made at once (user: one combined model), so the
single cause of the flip is not isolated; the durable detection fix is still a peer-novelty NET feature (D8).
**Fresh live DVWA→RCE run confirmed end to end (2026-11-06 17:10Z window):** RED, max_sg **70.17** Moderate,
alert=True, 89 flagged (47 process / 36 file / 6 net), **LLM `attack` 0.85** with tactics Execution/Discovery/
C2/Lateral/Exfiltration (TA0002/07/11/08/10); surrounding idle windows green. So on exp08 the live attack is
detected (not the exp07 coincidental-red FP). See `docs/notes/subgraph-score-fragility.md`.

**Deploy note:** exp08 is promoted via the trainer's local pointer (`current.local.json`) and the three
changed pipeline files (`etw2nodlink.py`, `build_experiment.py`, `engine.py`) were copied to the trainer
checkout directly (not via Ansible) — branch `fix/sg-det-fragility-exp08`, **committed `adb602e`
(2026-11-06), pushed**. To make it durable: deploy with `ubuntu.yml --tags project` so the checkout + sha
match. The exp08 run (`src/artifacts/runs/exp08/`: models, scaler, metadata, `train_scores.csv`) is tracked;
its bulk training CSVs (`train_edges.csv`, `features/features_*.csv`) stay on the trainer.

**2026-11-06 — second live run confirms exp08 + a new LLM finding + next action.** A fresh DVWA→RCE run
(session `live-20261106T001407Z`, 00:48:41–00:52:16Z) went RED in both windows it straddled, idle green:
00:45Z sg 19.24 Moderate (kept subgraph = the nmap `:22` subnet scan + `nmap/ncat/psexec64/rclone.exe`,
LLM **attack 0.72**), 00:50Z sg 37.62 Moderate (kept subgraph = C2 socket `10.21.4.30:8080`). The kept
subgraphs are the real attack nodes (not the exp07 benign-FP clusters), and the C2 socket is flagged again
(exp07 missed it). **New finding:** the 00:50 LLM hedged to `uncertain` (0.4) because our self-labelling
file `c:\lab-labels\attack-labels.jsonl` was in the subgraph and the LLM read "attack-labels" as a *lab test
harness* — a lab-artifact leak into the LLM evidence, not a detector miss (SG-Det still alerted). DEVIATIONS
D9. **Next action (planned):** add a Defense Evasion / Indicator-Removal phase (TA0005 / T1070 — delete
dropped tools, clear event logs, remove the task) to the kill chain to test whether the alert survives
cleanup and whether cleanup adds anomalous nodes; and drop `c:\lab-labels\*` from live scoring + the LLM doc
alongside it.

## exp07: retrained on ~41 h of idle (2026-11-04, PROMOTED + live on the Proxmox trainer)

exp06 raised 16 windows ≥ 10 in 431 (all 11 LLM-judged = false_positive). Cause: its 3 h baseline misses jobs on daily
and ~10 h cycles (11 windows); the other 5 were RDP sessions over the team VPN. Full account:
`docs/notes/retrain-2day-fp-lesson.md`. Experiment `range-2d-20261104` (11-02 12:30Z → 11-04 11:44Z, 1,415 windows) minus
174 excluded (attack, RDP sessions, rotation canary): `src/artifacts/exclusions/`. Attack recording saved as
`attack-20261102`.

Replay of the same 228 windows, cut-off 10, label source operator assertion, LLM off. exp06's replay matched live in
214/215 windows and the attack exactly. Held-out models trained to 11-03 17:25Z (seeds 360/361/362):

| Label | Windows | exp06 red | Held-out red per seed | exp07 red |
|---|---|---|---|---|
| idle | 200 | 4 | 0 / 0 / 0 (0.0 ± 0.0) | 0 (in-sample, not evidence) |
| human session | 21 | 5 | 3 / 1 / 1 (1.7 ± 1.2) | 5 |
| attack | 7 | 0 | 2 / 0 / 0 (0.7 ± 1.2) | 2 (35.3, 33.5; exp06 9.0, 2.0) |

**Idle FP fixed on unseen data; attack red on exp07 but seed-dependent** (only seed 360; 361/362 stay ≤ 4.9), and it is one
recorded attack, not a new live run. exp07 gate: `best_ep` 99 all types, τ file −0.178 / net −0.523 / process −0.210, `n_val`
10102 / 220 / 712, `val_fpr` 0.003 / 0 / 0, `emb_std` 0.073 / 0.110 / 0.104, converter match. New: `lab train --exclude-file`,
`lab compare`, trainer `--tnr-tolerance` (D5). A first model was rejected (`best_ep=4`, τ +4.94, passed the old gate).

**Not yet:** ≥ 24 h live soak across the ~05:05Z and ~11:15Z jobs (the only idle test of exp07 itself). Open: teammates to
confirm the RDP sessions; `powershell.exe` is in every window because our 5-min watchdog starts it (likely why the attack
scores weakly, [INFERENCE], untested). Merged (PR #9) and deployed from `main` (`2fd8b51`, `ubuntu.yml --tags project`, re-run `changed=0`).

## ocr-live: paper level scale + persistent history (2026-11-03, DEPLOYED + verified on the Proxmox trainer)

User decision: SG-Det uses the **paper's scale** (Moderate from 10, [PAPER] §7.2.1; retires the [OURS] 30), and
`ocr-live` keeps every window on disk, queryable by time range (`src/live/history.py` → `sessions/history.jsonl`
+ `windows/<wid>/detail.json`; `GET /api/history?from=&to=&alert=`; UI **History** panel; restarts and run
rotations no longer empty the UI). Retention: the hourly cron now prunes only `<session>/raw/`
(`live_raw_keep_hours` 12), window results are kept. Ansible: `live_sg_moderate_from` (default 10) in the unit.
`./selftest.sh` 17/17 (new `history.py --selftest`); local server smoke test on copied Oct-25 sessions: range
query, alerts-only, detail rebuilt from disk, path traversal refused, UI renders. Measured on the trainer BEFORE
the switch (193 scored exp06 idle windows, 11-02 23:14Z → 11-03 15:25Z): **6 red at ≥ 10 vs 1 at ≥ 30**. The
2026-11-02 attack session was already pruned by the old 12 h cron (its recorded max 9.0 / 2.0 stays green at 10
too). All three trainer sessions now hold a `.keep` so their raw copies survive.
Deploy (`ubuntu.yml --tags project -e repo_branch=live-paper-levels-history`, `8d6f668`): `--check --diff` showed
only the git checkout, the cron line and the unit (`--sg-moderate-from 10`); applied failed=0, re-run `changed=0`.
Verified 15:47Z: `/healthz` 200, `/api/status` `moderate_from 10.0`; backfill indexed 199 windows from the 3 sessions;
`/api/history` from the workstation returns them (12 restart/rotation duplicates hidden; 1 alert = the 05:00Z 129.0
window, LLM false_positive, judged at 30); the restart's `.r2` re-read scored 11 idle windows at cut-off 10, all
0.0 green, each with a history line + `detail.json`. Note: a plain `ubuntu.yml` run (default `repo_branch: main`)
rolls this back until the branch is merged.

## Proxmox demo run (2026-11-03, exp06): idle GREEN verified, attack detected at node level but NO alert

User cut training scope 48 h → **3 h** and set a hard 04:00 +07 (21:00Z) auto-shutdown. Done within budget:

- **exp06 trained + promoted** on a clean 3 h idle Loki range of the Proxmox ws-etw
  (`range-20261102T123000Z`, **12:30–15:30Z**; burst scan rejected the 11:30–12:00Z boot/backlog and a 16:00Z
  knet spike). Build: 88 windows, 86,943 edges, 866 nodes, `check.json` ok. Contamination EDA clean (no
  wsmprovhost/mstsc/rdpclip/msiexec; only OS idle: Windows Update servicing, Defender, XAMPP idling). Gate
  PASS: models for FILE/PROCESS/NET, `emb_std ≠ 0` all (file 0.070, process 0.106, net 0.089), val_fpr 0,
  train_flag_rate ≈ contamination. NET thin (n_train=262, n_val=0 so its val_fpr is vacuous — SG-Det absorbs).
  `lab promote exp06` converter_check = match; `ocr-live` restarted on exp06 (healthz 200, DeepSeek LLM on).
  Backed up to the workstation `src/artifacts/runs/exp06/`.
- **Idle GREEN (end-goal half 1 met):** 4 consecutive idle live windows 17:10–17:25Z all `alert False`,
  0 flagged, subgraph 0.0. exp01 red-everything on Proxmox data is fixed.
- **Attack delivered live (Vagrant Kali exploit origin BLOCKED → stage served from an it2102 box):** the local
  Vagrant Kali reaches DVWA :3000 via VBox NAT, but ws-etw CANNOT call back to Kali's stage (10.0.2.15/10.21.2.x
  are laptop-internal; ws-etw→192.168.1.0/24 is DROPPED by the it2102 firewall). ufw on the trainer also blocks
  inbound 8080. Working path: serve the stage + run `launch_exploit.py --attacker-ip 10.21.3.50` from
  **nt2102-vpn** (no ufw, iptables INPUT ACCEPT, same subnet as ws-etw). ws-etw fetched attack-lib/killchain/
  tool.bin (200) and the **full chain ran on the endpoint**: httpd→cmd→powershell cradle, `downloaded_tool.bin`
  (T1105), and the complete discovery set (whoami/systeminfo/hostname/tasklist/ipconfig/net/net1/schtasks).
- **Result — NOT red (end-goal half 2 NOT met):** attack windows 17:30Z (two attempts, one a failed-callback
  dry run) = 32 flagged / 5 subgraphs / **max 9.0 Minor / alert False**; 17:40Z (one clean attempt) = 4 flagged
  / **max 2.0 Minor / alert False**. Idle vs attack separate cleanly (0 vs 4–32 flagged) so detection *works at
  the node level*, but the subgraph score never approaches Moderate=30 (vs exp05's 106.96).
- **Diagnosis (the real finding):** only 5/44 processes flag; the discovery binaries score BELOW τ and, with no
  flagged parent linking them, Alg.1 fragments the attack into small disconnected subgraphs instead of one big
  one. Root cause = the attack is **partly in-distribution**: exp06's 3 h idle baseline already contains
  `httpd`/`mysqld`/`powershell` idling (XAMPP + scheduled tasks), so the RCE's reuse of those looks normal.
  More attack activity → more flags (double-attempt 9.0 > single 2.0). **The longer, cleaner baseline that made
  idle reliably green is the same thing that made the attack under-detected — the exp04 lesson, quantified.**
- **Open decision (do NOT silently tune):** to get a true red honestly, options are (a) retrain on a THINNER
  idle slice like exp05's ~16 min (fewer idle binaries in-distribution → attack separates, but more benign-FP
  risk); (b) recalibrate `--sg-moderate-from` for exp06 — but idle-only data (0.0) gives no benign ceiling, so
  lowering it now is the thin-baseline trap; (c) accept node-level detection and report it. Left at
  moderate_from=30 pending the user. See [[live-detector-benign-fp]], DEVIATIONS D2/D8. **Superseded 2026-11-03:**
  the user chose the paper's 10 (section above); (a)/(c) remain open for getting the attack red.

## Current focus

**Move training + attack to the home Proxmox lab (user, 2026-11-01).** Infrastructure verified: `infrastructure/`
(Terraform) built ws-etw 10.21.3.10 + ubuntu-trainer 10.21.3.20 on VNet `it2102`; `src/ansible/proxmox.ini`
provisioned both (ubuntu.yml + windows.yml with vulnlab: first run failed=0, re-runs `changed=0`, verify-windows
passes, DVWA :3000 = 200, Grafana = 200, Loki receives kproc/kfile/knet from ws-etw, collector 0 lost events).
Open: the first-run kfile ship backlog (~2M install events) was draining; LLM on (DeepSeek key saved on the
trainer); `ocr-live` on exp07 (top section).

**Kali on Proxmox — base infra APPLIED; one firewall fix pending apply (2026-11-03).** `infrastructure/main.tf`
adds a Kali attacker on its OWN SDN VNet `it2102k` / subnet **10.21.4.0/24** (gw 10.21.4.1, SNAT), VM 2106
`kali` 10.21.4.30 (2 cores, 4 GB, 40 GB), from the **installer ISO** (`kali-2026.3`, manual install, no
cloud-init). A dedicated `proxmox_sdn_applier.kali` applies the new VNet (the main applier does not re-run
for a newly added VNet). The cross-subnet attack path is opened by an `IN ACCEPT ← 10.21.4.0/24` rule added
to `it2102_rules` (ws-etw accepts the DVWA exploit; Kali accepts the callback via the existing 10.21.3.0/24
accept). The 7 base resources are now in state (user ran `apply`). **Fix pending apply:** creating `it2102k`
gave pve-master a new IP `10.21.4.1`, so every VM could reach the node UI/SSH there, bypassing the existing
`10.21.3.1` drop — added an `OUT DROP → 10.21.4.1` to the shared `it2102_rules` (user, 2026-11-03). `plan` =
**0 add / 5 change / 0 destroy** (the drop added to all 5 VMs' rules; still **pending user `terraform apply`**).
Ansible (branch `provision-kali-ansible`): `proxmox.ini` `[attacker]` group + `kali.yml` + `kali_attack` role
(copies `src/atomic-attack-pack` → `/opt/attack-pack`, launcher selftest). Auth = Kali's public default
`kali` / `kali` hardcoded in `proxmox.ini` (user, 2026-11-04; not a secret on the lab subnet, like
vagrant/vagrant), no vault. **Kali VERIFIED (2026-11-04):** ping ok, `kali.yml` first run failed=0 / re-run
changed=0, pack in place, selftest passes on the VM; Kali→DVWA on ws-etw:3000 = 200 (cross-subnet path works).

**Live scripted attack run on exp07 (2026-11-04 14:55Z) — detected at node level, NO alert.** Launched the
DVWA→RCE kill chain from the Proxmox Kali (`launch_exploit.py --target http://10.21.3.10:3000`): HTTP 200,
ws-etw fetched lib/killchain/tool.bin (200) and the chain ran (httpd→cmd→powershell, discovery, T1105, schtask
`UpdaterSync`). Result in `ocr-live` (session `live-20261104T114407Z.r5`, `moderate_from 10`): idle windows
14:30–14:50Z green (0 flagged); attack window **14:55Z = 9 flagged / 1 subgraph / max_sg 8.49 / Minor /
alert False**. `detail.json` shows the flagged subgraph holds the real attack nodes (Kali C2 `10.21.4.30`,
powershell, cmd.exe, schtasks, whoami, systeminfo) — so detection works at the node level, but the subgraph
score (8.49) fell **just below Moderate=10**, so no alert and no LLM investigation. Same under-detection as
exp06 (partly in-distribution idle baseline) and consistent with exp07's seed-dependent replay red. Not tuned.
LLM budget raised 10→15 beforehand (`9fb38ff`) but unused (LLM is alert-only). Cleanup ran (`cleanup-all.ps1`:
`UpdaterSync` + `downloaded_tool.bin` removed, shells killed; task confirmed gone). See `infrastructure/README.md`
"kali"; getting the attack honestly red is the open D2 decision (thinner baseline, etc.).

**Richer attack + NET-boost rollback (2026-11-04, branch `provision-kali-ansible`).** After the 8.49 near-miss
we briefly added an [OURS] NET ×2 SG-Det weight, then **rolled it back** (offline preview only reached 9.42 with
one flagged NET node; detector tuning the user rejected) and instead made the *attack* realistic/high-volume so it
clears Moderate honestly: `rce-killchain.ps1` now does T1046 loud subnet sweep (10.21.3.1-50 + Kali, 7 ports) +
T1105 five INERT tool markers (mimikatz/psexec/nc/nmap/rclone) + read-only MySQL enumerate/dump (`mysql -uroot`,
no password) + T1071 beacon loop (`--attack-seconds` default 180) + T1053.005. Benign/lab-only; disclosed [OURS]
in SCENARIOS/manifest/DEVIATIONS D8. Launcher serves the markers + `/beacon`; cleanup clears the tools dir.
**Live result (15:50Z, exp07, detector paper-faithful — no net weight):** pre-attack idle 15:45Z green; attack
window **15:50Z = 20 flagged (13 file + 7 net) / 2 subgraphs / max_sg 11.70 / Moderate / alert True** — the kept
subgraph holds the real attack NET nodes (C2 `10.21.4.30:8080`, DB `10.21.3.10:3306`, scanned SSH `.11/.20/.30/.50:22`)
+ networking DLLs. **So the richer attack DOES raise a true alert — the scan's NET breadth (not weighting) pushed it
over 10.** HONEST caveats: (1) margin is thin (11.70 vs 10) and NOT attack-specific — my own bulk WinRM/admin
activity minutes earlier (15:40Z) also went red at 11.03 (idle model is thin, the known FP mode); (2) the LLM
triage returned **`uncertain`** (not a confident `attack` like exp05's 0.85), so the LLM layer did not confirm it;
(3) the NET signal depends on the Linux hosts' SSH being up (open ports = the flagged scan nodes). Cleanup ran;
idle-green re-confirmation pending (my cleanup WinRM copy dirties 15:55Z). Repo selftests 17/17; attack-pack commit
staged (GPG cold, handed to user). See [[live-detector-benign-fp]], DEVIATIONS D8.

**Real tool download+EXEC live run (2026-11-05 00:50Z, exp07, paper-faithful SG-Det) — RED, LLM-confirmed, the best
result to date.** The T1105 phase now downloads AND executes real benign tools: Sysinternals **PsExec** + **rclone**
(real PEs staged on Kali by `roles/kali_attack` into `tools/win/`, gitignored, 833 KB + 85 MB; nmap/ncat best-effort,
OFF by default -> markers + the TcpClient sweep). Rationale: the graph uses no content feature, so real binaries add
nothing over markers UNLESS executed; the signal is the tool's process + sockets (D6/D8). Live from the Proxmox Kali (`launch_exploit.py --target http://10.21.3.10:3000 --attack-seconds 180`, launched at
the 00:50:00Z boundary): exploit HTTP 200; ws-etw fetched PsExec64.exe + rclone.exe (real) + nmap/ncat markers + 8
beacons. Result (session `live-20261104T235407Z`, moderate_from 10): windows 00:25–00:45Z green (00:45Z = 1 benign file
flag, sg 0.0); attack **00:50Z = 18 flagged (11 file + 7 net) / 1 KEPT subgraph / max_sg 15.40 / Moderate / alert
True**. The single connected subgraph holds the REAL attack nodes: `c:\users\public\tools\rclone.exe` (process), C2
`10.21.4.30:8080`, scan hits `10.21.3.11/.20/.50/.30:22`, DB `10.21.3.10:3306`, + the RAS/proxy & .NET DLLs the
downloads pulled in. **LLM triage = `attack`, conf 0.85** (TA0011 C2 / TA0010 Exfil / TA0002 Exec); the report names
PowerShell spawning rclone from a user-writable dir and exfil to the C2. Better than the 11-04 run on every axis:
higher margin (15.40 vs 11.70), ONE connected subgraph (not fragmented), **LLM-CONFIRMED** (vs "uncertain"), and
attack-**SPECIFIC** (only 00:50Z red — my deploy was Kali-side, ws-etw stayed idle; no admin-burst FP this time).
Honest notes: (1) PsExec DID run (its `PsExec64.exe -> PSEXESVC.exe -> cmd.exe -> whoami.exe` tree is in node_attrs)
but scored below τ, so rclone + the scan/C2/DB carried the score — PsExec's contribution was nil; (2) nmap/ncat were
markers, so the NET breadth came from the PowerShell TcpClient sweep (the designed fallback); (3) still a thin 5-min
idle model — Moderate, not Significant. **Cleanup DONE** (user ran it from RDP on ws-etw, 2026-11-05: `UpdaterSync`
task deleted, tools dir + legacy marker removed, PSEXESVC/tool processes stopped). Repo selftests 17/17; attack-pack +
ansible + docs changes committed signed (handed to the user). See [[live-detector-benign-fp]], DEVIATIONS D6/D8.

**nt2102-vpn (2026-11-04):** VM 2105 at 10.21.3.50, OpenVPN running since 11-02, team client certs in use. External TCP 2102 → home
router → pve-master DNAT (`src/ansible/proxmox-firewall.yml`) → 10.21.3.50:1194, verified over TCP. The VM rule
and the DNAT comment now say TCP (were UDP): needs `terraform apply` + a `proxmox-firewall.yml` re-run.

**ubuntu-etw (2026-11-02):** third Proxmox VM, VM 2104 at 10.21.3.11 (Ubuntu 24.04 cloud image + cloud-init, like
the trainer), direct SSH over the routed path. auditd → `auditship` (`src/audit-python`) → trainer Loki
`{job="auditd", host="ubuntu-etw"}` verified: exec/net/file records arrive in seconds, no feedback loop, 20/20
events after a 40 s Loki outage, `target.yml` re-runs changed=0 (D11). Raw logs only: no converter/model for Linux.

**`ocr-live` hardened for 24/7 (2026-11-02, code + selftests; Proxmox deploy/soak: see Next steps).** Fixed four
failure modes: the Loki thread died silently on any non-OSError (UI frozen, systemd unaware) → catch-all +
watchdog exit + `Restart=always`; a restart appended a 2nd raw copy into the same session folder → new
`<run>.rN` folder; an unbounded catch-up read (≤ 1 h backlog in one step, OOM risk on the 6 GB trainer) → ≤ 20k
lines/provider/poll with the clock held for the lagging provider; the UI never re-synced after a reconnect →
re-fetch on every SSE open. Added `/healthz`, `OOMScoreAdjust=500`, per-event converter guard, and the [OURS]
**daily LLM budget** `--llm-max-per-day 10` (user, 2026-11-02; D9). `./selftest.sh` 16/16. src/live/README §5.
Deployed on the Proxmox trainer (`7dadd1a`, `-e repo_branch=provide-infrastructure -e live_llm_args=`; the LLM
was turned on later: DeepSeek key saved, then `ubuntu.yml --tags project` with the inventory's `live_llm_args`) and
verified there: `/healthz` 200; deploy restart → `.r2`, SIGKILL → systemd restart → `.r3`, both 0 duplicate raw
lines and 0 late drops during catch-up; Loki container restart → error shown, recovered in < 10 s, no process
restart. Costs seen: a restart re-reads up to 1 h (catch-up 6–16 min at ~1.6–4k lines/s, the UI is that far
behind meanwhile; "event lag" shows ≈ −backlog then); memory 2.7 GB after catch-up (peak 3.1 GB, VM 5.9 GB).
NOT yet: a soak across a 6-h run rotation (memory trend). Seen live: a ws-etw burst (≤ 7,660 kfile ev/s,
11:18–11:21Z) put the etwcollect shipper (1 MiB per file per second ≈ 1,800 kfile lines/s) minutes behind, so
211k kfile lines were dropped as late in `.r2`; the `.r3` re-read later had them (11:30 window 21k → 109k edges).

**The core concept demo WORKS end to end (2026-10-31, exp05, benign = idle pulled from Loki).** The path to
it: exp03 (broken SilkService data → all red) → exp04 (etwcollect scripted-workload capture: benign green but
attack MISSED, because the scripted workload's netcheck/download/cmd scenarios reused the attack's
living-off-the-land binaries) → user switched to **benign = idle, pulled directly from Loki, no capture
script** → **exp05**.

- **exp05**: trained on a 16-min idle Loki range (`range-20261031T124500Z`, pre-attack idle, pulled with
  `lab pull --start … --end … --split test`). Thin (FILE 339 / PROCESS 34 / NET 18 node-copies; net
  `val_fpr=1.0`), but the per-subgraph SG-Det cut-off absorbs the per-node noise.
- **Result (session `live-20261031T134030Z`, fresh idle run):** idle windows **green** (13:45 = 13 flagged
  but subgraph max 8.0 Minor, alert False; 13:50 green). Live DVWA→RCE attack (13:55Z, 2314 edges) **RED**:
  201 flagged, subgraph **106.96 Significant, alert True**; flagged nodes = the Kali stage IP
  `10.21.2.30:8080`, `c:\users\public\downloaded_tool.bin` (T1105), the RCE `powershell.exe`, DVWA MariaDB
  `127.0.0.1:3306`. **LLM triage `attack` (0.85)** + IOCs (full discovery chain) + report. Offline re-judge
  (`rejudge.sh`) reproduces the subgraph + serialized attack document. End goal met.

Note the subgraph correctly captured the discovery tools (whoami/net/systeminfo/schtasks) via their flagged
powershell parent even though each scored below τ individually — OCR-APT subgraph expansion working.

**Cleanup done (2026-10-31):** both capture scripts removed (`06-benign-workload.ps1`,
`07-build-train-dataset.ps1`), `win_benign` role + `collect-train.yml` deleted, `windows.yml` /
`verify-windows.yml` / docs / memory updated. Benign = idle pulled from Loki (D2).

DONE this session (2026-10-22, verified live):
- **exp03** trained on the 1-h capture and **PROMOTED** (gate passed: 77-min kfile span, all 6 edge types
  non-zero, `emb_std ≠ 0` per type; Read 517,951 · Send 35,485 · Write 18,308 · Recv 10,109 · Load 4,474 ·
  Start 76). `ocr-live` restarted on exp03. The `0x380` kfile fix held at 1-h scale.
- ~~Live attack detected~~ (session `live-20261022T155035Z`, 37.76 / 56.0): **NOT valid** (verified
  2026-10-25): its windows have zero Read/Write edges (training ~14k Read per window). DEVIATIONS D8.
- **LLM investigator** (`alg2.py`, paper Alg. 2 l.3–15 incl. the serialize+reduce phase that collapses the
  DLL-load flood) already implemented; **added OpenAI-compatible support** (`--llm-api-key`, keyless-dummy
  for a custom `--llm-base-url`) across `alg2.py` / `engine.py` / `live_server.py`; selftest passes.

DONE 2026-10-25 (verified live):
- **Always-on live capture**: SYSTEM task `etwlab-live` runs the `start-live.ps1` watchdog at boot + every
  5 min (rotate 6 h, raw `live-*` kept 1 day, `07` pauses it via a hold file). Verified: ws-etw reboot →
  new run by itself; a tick during `-Pause` left it stopped; `-Resume` restarted in 11 s. The `07` hold
  path is NOT yet exercised by a real train capture.
- **LLM on `ocr-live`**: DeepSeek, thinking off, `--llm-debug`; key saved once on the trainer
  (`alg2.py --ping`, no-TTY ping 0.8 s). Trainer root LV grown 31 → 61 GB; sessions pruned after 12 idle h
  (`.keep` on `live-20261022T155035Z`); Loki retention 3 d.
- **Full-flow run** `live-20261025T073042Z` (exp03, attack 14:45–14:47 local): benign 14:35 / 14:40 green;
  attack window red (33.87) but ONLY from a benign msedge launch: the attack's processes reached the graph
  and none was flagged. LLM judged that red window `false_positive` (0.9): correct. Laptop replay
  (`replay-live-20261025T073042Z`, bundle with the model in `src/live/sessions/live-20261025T073042Z/model/`)
  reproduces the live windows that were not affected by late kfile lines, and the same LLM verdict.

OPEN (the real limitation):
- **exp03 vs new data**: exp03 was trained on SilkService captures (wrong NET owners, `N/A` process names,
  thinned kfile). On etwcollect data every live window is red (sg 62–96 during generated DLL-hashing load, not
  a detection result). Needs a new benign capture + `exp04` (Next 3 steps).
- Live engine vs Loki push outages (left as is, user 2026-11-02): after a ws-etw → Loki push stall longer than `--loki-idle-s`, the backlog reaches the engine
  after its windows closed and is dropped as late (522k lines after a 2-min outage on 2026-10-26). Files and
  Loki keep every line, so a re-build/replay is complete; only the live windows of the outage are thin.
- etwcollect lag values go to −3.6 s over 32 min: ETW event timestamps run a few seconds ahead of the system
  clock (status compares against wall time). Harmless for windows; not investigated further.
- **Browser benign FP**: an Edge-browsing benign window hit **31.0** (Moderate, false alarm). msedge's
  many per-instance processes are unseen in benign training → the *same* mechanism as the cradle. The fix
  is benign **variety** (training that covers browsing), **not** more capture time. DEVIATIONS D8,
  [[live-detector-benign-fp]].
- LLM attack path (triage `attack` → IOCs → report) never run live: the exp04 attack formed no subgraph
  (all attack nodes below τ), so the LLM never triggered. Pending exp05.

## Done

- **Own ETW collector `etwcollect`** (`src/etw-python`, 2026-10-26, verified on `ws-etw`, DEVIATIONS D10):
  stdlib + ctypes, one real-time session (Kernel-Process 0x50 / File 0x380 / Network 0x30), TDH decode, own
  I/O dropped in the callback, 1 s batched writes, Loki shipper with saved offsets (replaces Alloy), ledger push
  for 07. SYSTEM task `etwlab-collector` started/stopped by `etwctl`; local source = Vagrant mount `C:\etw-python`,
  cloud = git clone. Checks:
  - `etwcollect selftest` (any OS) + `selftest --live` on ws-etw (struct sizes, child process + canary file +
    curl network event captured, nothing from our PID written, converter gives Start/Load/Read/Write/Send/Recv);
  - parity vs SilkService side by side (20 s): 100 % of events matched for kproc 620, kfile 88,612, knet 26, 0
    field mismatches; SilkETW-only = our own 76 events;
  - 32-min live run (~1,300 kfile ev/s generated activity): kfile lag within ±4 s, `EventsLost` 0, CPU ~10 %
    of a core; Loki down 2 min → 69 MB backlog shipped within ~7 min; Loki counts = file lines (2,629,621);
    live engine `late_dropped` 0 outside the outage, ~374k Read edges per window;
  - Ansible: SilkService + Alloy uninstalled, `win_alloy` role deleted, 2nd `windows.yml` run `changed=0`,
    `verify-windows.yml` passes (DVWA too). Repo-wide `./selftest.sh` 15/15.
- **NET attribution fix** (2026-10-26): 77 % of knet events have header PID ≠ payload PID; the converter now
  takes the owner from `_PID` (SilkETW) / `PID` (etwcollect), selftest case added. Old captures are fixed on
  re-conversion; models trained before (exp01–exp03) used the wrong owners.

- Windows 10 ETW lab VM + collector + converter, end to end (Ansible, `src/ansible/`) + `src/scripts/06, 07`.
- Benign train dataset, experiment `20261009T145632Z` (verified, `src/out/etw/experiments/20261009T145632Z/`):
  20-min capture, workload seed 42, launch mode explorer-com; 8 windows (5 min / 2 min stride);
  37,294 edges in → 68,319 windowed edges (484 outside any window); 6,810 node-window copies
  (995 PROCESS / 2,558 FILE / 3,257 NET); `check.json` ok, all 6 edge types non-zero
  (Recv 27,503 · Load 24,831 · Send 13,900 · Write 954 · Start 645 · Read 486).
- Behavior features for that dataset: `src/artifacts/features/self-dataset/` (15 features, scaler fit on train).
- Behavior features for NODLINK SimulatedW10: `src/artifacts/features/paper/` (from `dataset/nodlink/SimulatedW10/`).
- Trainer code with selftest: `src/pipeline/train_gnn_models.py`.
- Valid OCRGCN training run `src/artifacts/runs/exp01` (verified; rerun 2026-10-11 on the matching
  experiment edges + features): no missing feature rows; split 5 train / 2 purged (w0005, w0006) /
  1 val (w0007); emb_std 0.05–0.19 for every type and seed; thresholds differ across seeds;
  train_flag_rate ≈ contamination (process 0.0017 = 1/575). Model saved from run 0 (seed 360).
- Live path code (with `--selftest` each): `src/pipeline/score_window.py` (shared scorer),
  `src/live/` (etwship.py shipper/replay, engine.py, live_server.py + UI, compare_replay.py). See `src/live/README.md`, DEVIATIONS D8.
- Live path end to end (verified): session `live-20261011T105718Z` showed converter sha256 `match`,
  dos_map `\device\harddiskvolume4 → c:` from the VM, shipper PID excluded. First scored window
  (`live-20261011T112141Z`, 11:25–11:30Z, VM running the old server-install workload): 7,813 edges, 835 nodes, 19 flagged
  (2.3 %), all FILE (445 file nodes; process 0/147, net 0/243). 18 of the 19 are syswow64 DLLs loaded once
  by a short-lived `updater` process (0 `updater` processes in training; the DLLs themselves are in
  training). Identical score 0.3220 vs τ_file 0.2297 (train max 0.2312), so prob = 1 each (clamped).
- Alg. 1 + SG-Det (code + selftest, verified on real data 2026-10-12): `src/pipeline/alg1.py` (in memory,
  [AUTHOR CODE] score = Σ clamped min-max prob, keep ≥ Moderate), used by `engine.py`; flags in
  `live_server.py`; UI shows level/score and greys subgraphs below the cut-off. The saved exp01 models
  reproduce `train_scores.csv` (max |diff| 1.3e-5), so the prob range is valid. Replay of
  `live-20261011T112141Z` through the live server: 11 scored windows, 5 with nodes above τ → 1 red
  (11:25 `updater`, 18 DLLs, score 18.0, Moderate). Other windows ≤ Minor (max 5.83). `alg1.py
  --session-dir` re-judges saved sessions without the VM.
- [OURS] live level scale (2026-10-13, DEVIATIONS D8): Moderate from sg_score 30 (`--sg-moderate-from`,
  [AUTHOR CODE] > 10). Re-judged replay: 11:25 `updater` = Minor → 0 red windows of 10.
- VM crash 2026-10-11 18:02 diagnosed (verified in `journalctl -k`, `VBox.log.1`): not OOM, a VirtualBox
  VMSVGA segfault in `libnvidia-glcore.so.595.84` (3D acceleration). Fixed: `accelerate3d off` on
  `w10-etw` and in `src/Vagrantfile`.
- LLM step after SG-Det (code + selftests, 2026-10-13): `src/pipeline/alg2.py`. [OURS] triage verdict
  attack / false_positive / uncertain, then the paper's Alg. 2 l.3–15 prompts for attacks (DEVIATIONS D9).
  Runs on its own thread in `engine.py`, `--llm-provider` in `live_server.py`, badge + report card in the UI.
  Plumbing verified with the keyword mock on a replay of `live-20261011T112141Z` (before the scale
  change; now needs `--sg-moderate-from 10.0001`): 1 red window (11:25 `updater`), verdict attached,
  `llm/` files written. Provider chosen (user, 2026-10-25): DeepSeek API `deepseek-flash`
  (`--llm-provider deepseek`, replaces the dropped Cloudflare attempt); code + offline selftest done.
  First real run (user-verified 2026-10-25): re-judged replay `live-20261011T112141Z` 11:25 `updater` window
  → `false_positive` (conf 0.88, 1 call, 3.1 s, 2159 tokens, default thinking). One benign window = one data
  point, not a rate (D9).
- Attack pack is now ONE attack: the RCE kill chain (`src/atomic-attack-pack/scenarios/rce-killchain.ps1`),
  delivered from a Kali attacker VM (`tools/launch_exploit.py`; local-only Vagrant box, pack mounted at
  `/opt/attack-pack`, not Ansible-managed). Kali exploits DVWA (OS command injection) and the RCE shell
  runs Discovery (T1033/T1082/T1057/T1016) → T1105 → T1053.005,
  self-logging labels (`LABELING.md`, D6). Launcher `--selftest` passes on the host; the old 01–10
  scenarios + orchestrator + local sink are removed.
- Windows collector provisioned + verified on `ws-etw` via Ansible (2026-10-18): `ansible-playbook
  windows.yml -e vulnlab_enabled=true` installs every tool — SilkETW/Python/converter/etwctl, Alloy,
  `start-live.ps1`, benign `06`/`07`, and DVWA. New `src/ansible/verify-windows.yml` asserts all
  present and DVWA serving on :3000. **Idempotent: a 2nd run is
  `changed=0`** (downloads now `force: no` + SilkETW/Python checksums pinned). DVWA (`win_vulnlab`):
  XAMPP (Apache + PHP + MariaDB) via Chocolatey, DVWA cloned to `C:\lab\DVWA` at the Apache DocumentRoot
  on :3000, Apache + MariaDB installed as services, and the schema built once via `setup.php`
  (admin/password, security=low).
- Alloy → Loki data path verified end to end on `ws-etw` → `ubuntu-trainer` (2026-10-18): a 37 s test
  capture shipped **losslessly** — Loki's per-provider counts equal the raw files (kproc 3372 / knet 223 /
  kfile 28224), with labels job/host/split/run/provider correct and line JSON intact. (The provisioning-time
  "Loki not reachable" warning was just Loki still initializing ~1 min after a trainer reboot; the trainer's
  host-only NIC also needed a `vagrant reload` once.)
- Log pipeline Alloy → Loki (code + selftests, 2026-10-17; Ubuntu side verified on `ubuntu-trainer`: lossless
  pull, host build = VM build, live via Loki = offline 8/8 windows; Windows side verified 2026-10-18:
  capture ships to Loki losslessly, counts match): `win_alloy` / `ubuntu_loki` roles, `src/pipeline/loki_pull.py` (interactive / flags) →
  `build_experiment.py`, `src/live/loki_source.py` (`live_server.py --loki-url`), `src/live/loki_push.py`
  (test feed). Lab moved to Windows Server 2022 + Ubuntu trainer (Ansible, `src/ansible/`).
- Improve loop (code + selftest, 2026-10-17): `lab` CLI (`src/lab/cli.py`): status / pull / train / loop /
  promote; one run folder per model; current-model pointer read by the live server, `serve.sh`,
  `rejudge.sh` and `ocr-live`.
- Concepts: provenance graphs; GNN message passing.
- kfile-truncation root cause + fix (2026-10-21, verified): the old captures' `kfile 1/21m` was a Kernel-File
  **FileName-rundown storm** — keyword `0x390` includes bit `0x10` (FILENAME), which makes ETW dump a Name
  event for every already-open file handle at trace start (~1.35M events all stamped at t0 on this host),
  starving the real-time consumer so genuine Create/Read/Write after the first minutes are lost. Fix: drop
  `0x10` → kfile keyword **`0x380`** (Create+Read+Write) in `etwlab.json.j2:15` + the VM's live `etwlab.json`.
  5-min smoke `x-20261021T164839Z`: kfile spans the full 5 min (`check.json` minutes 5.0), edge_types Read
  23309 / Write 1703 / Load 1675 / Send 3403 / Recv 2795 / Start 52 (all non-zero); converter resolves FILE
  names from Create events (721 FILE nodes). Cost = `unresolved Write 18090 / Read 10706` (files opened
  before t0, now dropped) — recorded in DEVIATIONS D2. Converter name resolution: `etw2nodlink.py:214-232`
  (`on_fcreate` fileobject→path; Read/Write resolve via `fobj` or `fkey`).
- Trainer VM synced to `main` (2026-10-22): `repo_branch` flipped `ansible-two-vm-provisioning → main`
  (`group_vars/all.yml:71`), `ansible-playbook -i inventory.local.ini ubuntu.yml` moved `/home/vagrant/NT2102`
  to main @ 64d1f0b (canonical `loki_pull.py`/`build_experiment.py`), `uv sync`, `ocr-live` restarted. exp02
  model + `current.local.json` pointer preserved (both untracked/gitignored).

## Invalid / needs redo

- (none)

## Not done

- Benign train capture (07) with the NEW collector (D10), pulled from Loki, train `exp04`, promote,
  calibrate, attack-test (Next 3 steps; the collector is ready). Gate on `lab status` per-provider coverage (kfile ≈ full
  span) and Read/Write per window before trusting it.

- Concepts 2 (rest) to 9. Phase 0 toy exercises; Phase 1 one-page paper summary + hyperparameter list.
- LLM container; hardware recorded.
- Replay equivalence check (`compare_replay.py`); user-confirmed runs of the selftests of
  score_window.py / engine.py / etwship.py.
- Test split capture, node labels, test scoring, NODLINK W10 detector baseline.
- LLM: DeepSeek on a red window from a real attack (the RCE kill chain live), to see the attack path
  (triage → IOCs → report). Alg. 2 l.16–25
  not built.
- SG-Det cut-off not checked against an attack: our scale (Moderate ≥ 30 ≈ at least 30 anomalous nodes
  in one subgraph) was set from one benign window; a small Atomic scenario may be filtered out.

## Open items

Data validation:
- Edge direction vs NODLINK; converter `excluded` / `unresolved` counts (2026-10-07 run: 761 unresolved
  Writes, 164 Reads; check the 10-09 summary in `converted/SimulatedW10_train_summary.json`).
- Verify ETW keyword bitmasks and event IDs against the provider manifest dumps.
- Short test captures to record: real JSON field names; whether Read/Write events carry a file name
  or only a file object; whether network events show PID 4 at top level; events/min per provider.
- Verify generator-launched apps have explorer as parent in converted edges.
- `build_node_labels.py`: fill in the TODO column names against real `*_node_attrs.csv`.

Model:
- Early stopping reacts to noise: benign val TNR at the 0.999 quantile is ~1.0, so one val node
  crossing τ starts the patience counter. Saved process model (seed 360) has `best_ep=3` (≈2 real
  steps after warmup); best_ep ranges 3–99 across seeds. Try `--patience 20` / compare seeds' flag rates.
- Reported `val_fpr = 0` is optimistic: the same validation split selects the epoch.
- τ comes from training scores, so it sits at the training maximum (τ_file 0.2297 vs max 0.2312);
  any benign file in an unseen neighbourhood crosses it. Benign coverage is one 20-min scripted capture,
  so background jobs (e.g. the 32-bit `updater`) are unseen and get flagged.

Data validation (LLM input):
- NET attrs are SilkETW signed integers (`-1,707,025,385:-17663` = `23.220.64.154:443`); stored raw, decoded
  for display in the LLM document (`alg2.py`) and the live UI (raw shown beside it). Process attrs are only the image name (`updater`), no path or command line, so the
  LLM sees little context for processes.

Live tooling:
- `etwship.py --replay --speed 0` never advances the virtual clock (step = interval × speed = 0), so it
  hangs; at 600× each POST carries 10 min of events and the shipper times out. 120× works (52 min in 29 s).
- Replayed window 11:35 has 13,045 edges vs 12,516 live: the live run dropped late events (lateness 20 s).

Code bugs found in the 2026-10-13 docs pass (verified in code, not fixed yet):
- `serve.sh` help prints `sed -n '2,12p'`, one line too many (includes `set -euo pipefail`).
- `etwship.py --speed` help says "0 = no sleep", but 0 hangs (see Live tooling); default is 60, `replay.sh` uses 120.
- `build_node_labels.py` expects `node_id,type,name,timestamp`; the converter writes `node,node_type,node_attr`
  (no timestamp, types `PROCESS/FILE/NET`), and NET is checked as `network`. It cannot run as is.
- `selftest.sh` skips `encode_to_features.py --selftest`.
- `live_server.py --sg-moderate-from` hard-codes 30.0 instead of `alg1.MODERATE_FROM`.
- Scenarios 05 (lsass handle access) and 09 (timestomp) may be invisible to the collector's file keywords
  (0x390 = name/create/read/write) [INFERENCE]; check on a capture.

Repo hygiene:
- `.gitmodules` still declares submodule `dataset/Simulated-Data` (PKU-ASAL), but `/dataset/` is
  gitignored and the data sits in `original-dataset/Simulated-Data`; fix or drop the entry.
- `src/out/etw/SimulatedW10_train_edges.csv` (old un-windowed capture) sits next to the experiment
  folders and is easy to pick by mistake (it caused the first, collapsed exp01 run).

## Next steps (2026-11-01: Proxmox)

0. **Soak the 24/7 fix** (deployed + fault-tested 2026-11-02): RSS across one 6-h run rotation (next at
   ~17:04Z). Option: raise the etwcollect shipper rate (more than one 1 MiB batch per file per second) so kfile
   bursts stop being dropped as late live.

1. **Train on Proxmox (exp06):** runbook `docs/notes/training-runbook.md`. Idle range from
   2026-11-02 11:45Z (11:05–11:40Z is a boot + fault-test burst, not idle) up to the trigger (user: ~2 days);
   no WinRM/RDP/Ansible on ws-etw until then. `lab pull --start … --end … --split test` → `lab train
   --benign-split test --model exp06` → gate → `lab promote exp06`. Check that idle stays green live.
2. **Attack on Proxmox:** add a Kali VM on it2102 (planned 10.21.3.30; Terraform + attack pack delivery),
   run the DVWA→RCE chain live, expect red + an LLM verdict.
3. **Real tool download + execution** in the T1105 phase (user, 2026-11-04; implement next session):
   download + RUN benign real tools (nmap `-sT` / ncat / PsExec; rclone optional) so tool processes +
   sockets generate new flagged nodes — the signal is execution, not file content. Done: see D6. Branch `provision-kali-ansible`.

Earlier backlog (2026-10-31, after the exp05 demo win):

1. **Live UI: render the LLM report as HTML** (user, 2026-10-31). The web UI currently shows the Alg. 2
   report (`sg3_report.md`) as raw markdown text — add markdown→HTML rendering in `src/live/` (the UI /
   `live_server.py`).
2. **Steady the idle baseline:** exp05's idle set is thin (net `val_fpr=1.0`, 6 net train nodes). Pull a
   larger idle range from Loki (e.g. rotate a fresh idle run, let it sit ~30 min, `lab pull --latest
   --split test`) and retrain for a less noisy per-node net model. The demo already works; this hardens it.
3. Concepts 2–4 (GNN basics, RGCN, one-class loss) for the write-up; replay equivalence via
   `compare_replay.py` on the host (the trainer-side `rejudge.sh` already reproduced the attack subgraph).

Note: benign = idle pulled from Loki (no capture script). To rebuild benign: `lab pull --start <t>
--end <t> --split test --yes` on a clean idle range, then `lab train --experiment <id> --model <name>
--benign-split test` (a Loki idle pull builds `_test_` files; the flag is an explicit opt-in recorded in
`lineage.json`, added 2026-11-02, replacing the old copy-to-`_train_` workaround).
