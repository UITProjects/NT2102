# Next action: add a Defense Evasion (remove-trace) phase to the kill chain

Provenance: live exp08 validation runs on the Proxmox lab, 2026-11-05 (17:10Z) and 2026-11-06
(00:45/00:50Z). Decision owner: Akira. Status: **planned, not yet built.**

## Why this is next

exp08 now detects the DVWA→RCE kill chain live (two independent runs, below). The detector side is
working; the next improvement is on the **attack side** — make the scenario more realistic by adding a
phase the attack currently lacks: the adversary *removing its own traces* after acting. In ATT&CK terms
this is **Defense Evasion (TA0005), Indicator Removal (T1070)**. It is a standard late-stage APT
behaviour and a good stress test: does the detector still fire when the attacker tries to clean up, and
do the cleanup actions themselves show up as anomalous nodes?

> We already have `cleanup-all.ps1`, but that is **lab teardown run OUTSIDE the capture window** (it even
> says so) — it resets the VM between runs and is deliberately not labelled or observed. The next action is
> different: a *real attacker phase inside the kill chain*, inside the capture window, self-labelled, so the
> detector sees it. Do not confuse the two.

## Findings so far (what the 2026-11-06 run established)

### 1. exp08 detects the attack live — for the right reason (two runs now)

| Run | Window | sg_score | level | alert | kept subgraph is | LLM |
|-----|--------|---------:|-------|:-----:|------------------|-----|
| 2026-11-05 | 17:10Z | 70.17 | Moderate | ✓ | attack (89 flagged) | attack 0.85 |
| 2026-11-06 | 00:45Z | 19.24 | Moderate | ✓ | nmap `:22` subnet scan + `nmap/ncat/psexec64/rclone.exe` | **attack 0.72** |
| 2026-11-06 | 00:50Z | 37.62 | Moderate | ✓ | C2 socket `10.21.4.30:8080` | uncertain 0.4 |

Idle windows stayed green in both runs (sg 0.0). The kept subgraphs are the **actual attack nodes**
(scan, staged tools, C2) — not the benign TiWorker/watchdog clusters that caused the exp07 false positive.
The C2 socket, which flipped to in-distribution (−1.50) and was missed on exp07, is **flagged again** on
exp08. See [[subgraph-score-fragility]].

### 2. The LLM can be talked out of a true alert by our own lab labelling file

On 2026-11-06 the C2 window (00:50) alerted RED correctly, but the LLM returned **`uncertain` (0.4)**, not
`attack`. Its own rationale:

> *"The file name `attack-labels.jsonl` and the lab-like path suggest this may be a test harness or
> labeling script rather than real adversary activity […] too ambiguous to confirm."*

Cause: the kill chain writes ground truth to `C:\lab-labels\attack-labels.jsonl` (LABELING.md), that FILE
node landed in the subgraph, and the LLM read the literal string **"attack-labels"** and inferred *lab test
harness → probably benign*. This is a **lab-artifact leak into the LLM evidence**, not a detector miss:

- The anomaly detector (RGCN + SG-Det) is unaffected — it keys on `ip:port`/path **hashes** and per-node
  features, never on the readable filename, so the RED alert stands.
- Only the LLM overlay (Alg. 2, which reads human-readable docs) is fooled, and only because a lab-only
  file is visible to it. In a real deployment that file would not exist.

Recorded in DEVIATIONS D9. **Fix (small, do alongside the next action):** extend the own-tooling filter to
drop `c:\lab-labels\*` from live scoring + the LLM doc, exactly like the `start-live.ps1` watchdog
(`etw2nodlink.is_own_tooling_path`). It is lab plumbing, not attacker or benign activity.

## The next action — spec

Add a final phase to `src/atomic-attack-pack/scenarios/rce-killchain.ps1`, after Persistence (T1053.005,
line ~238) and before the completion banner. Self-label each step with `Write-AttackLabel` /
`Invoke-LabeledStep` so it joins the ground truth, and keep it inside the ≤ 5-min capture window.

**Phase: Defense Evasion — Indicator Removal (TA0005 / T1070)**

| Step | ATT&CK | PowerShell (explain each to the reader) | Observable as (graph nodes) |
|------|--------|------------------------------------------|------------------------------|
| Delete dropped tools | T1070.004 File Deletion | `Remove-Item C:\Users\Public\tools\*` and `...\downloaded_tool.bin` | FILE delete ops by the RCE `powershell` / a new `cmd.exe` |
| Remove the scheduled task | T1070.009 (clear persistence) | `schtasks /delete /tn UpdaterSync /f` | `schtasks.exe` process node under the RCE shell |
| Clear event logs | T1070.001 Clear Windows Event Logs | `wevtutil cl Security` (and `System`) — **destructive to logs; lab only** | new unseen `wevtutil.exe` process node (a strong signal) |
| (optional) Timestomp | T1070.006 Timestomp | set `(Get-Item f).LastWriteTime` on a dropped file | FILE write/attribute op |

PowerShell notes for the reader:
- `Remove-Item <path>` = `rm`; `-Recurse -Force` deletes a directory and its contents without prompting.
- `wevtutil cl <log>` = **cl**ear the named event log. This is genuinely destructive (it wipes the host's
  Security/System logs) — safe here only because the VM is disposable and snapshot-restored. Never run on a
  real host.
- `schtasks /delete /tn <name> /f` = force-delete a scheduled task by name.

### What this tests (the point)

1. **Does the alert survive cleanup?** The attacker deletes files and clears logs — but our telemetry is
   ETW streamed to Loki *off-box in real time*, so clearing the host's own event logs should **not** erase
   what the detector already ingested. Expect the window to stay RED. Confirms the live-off-box design is
   robust to T1070.001 (a nice defensive point for the report).
2. **Do the cleanup actions add their own anomalous nodes?** `wevtutil.exe` and a deletion `cmd.exe` are
   processes the idle baseline has likely never seen → they should flag, *raising* the score, not lowering
   it. An attacker "covering tracks" in a provenance-graph world often makes itself **more** visible.
3. **LLM tactic coverage:** the triage should now surface **TA0005 Defense Evasion / T1070**, broadening the
   tactic list beyond Execution/Discovery/C2/Persistence.

### Guardrails (keep it honest + safe)

- Lab-only, disposable snapshot-restored VM, Defender off, isolated subnets — same envelope as the rest of
  the chain (SCENARIOS.md "honesty"). `wevtutil cl` is destructive; gate it behind an explicit
  `-ClearLogs` switch (default on in the lab, documented), and keep the README warning.
- Self-label every step so it is attributable ground truth, not silent noise.
- Do **not** re-tune any detector threshold to this phase. If it does not raise the score, report that
  as-is (a real finding), do not fit the test.
- Update SCENARIOS.md phase table, LABELING.md (new technique rows), and add a DEVIATIONS line if the phase
  changes what is captured. `cleanup-all.ps1` still runs *after* the window for teardown.

## Related
- [[subgraph-score-fragility]] — the exp07 FP root cause + the exp08 fix validated above.
- DEVIATIONS **D9** (LLM lab-artifact leak), **D6** (self-labelling), **D8** (SG-Det / live path).
- SCENARIOS.md (current phase table), LABELING.md, `cleanup-all.ps1` (teardown, not this phase).
- [[live-detector-benign-fp]] — FP history exp02→exp08.
