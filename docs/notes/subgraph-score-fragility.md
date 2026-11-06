# Lesson: why a live attack went RED but was a false positive (SG-Det + RGCN embedding fragility)

Written 2026-11-05. Label source: **operator assertion** (we know when the attack ran). Counts are counts,
not rates. Two live DVWA→RCE runs on the same model **exp07** are compared; the only thing that differs
between them is the window's graph context, not the model or the thresholds.

## What happened

A live DVWA→RCE kill chain (session `live-20261105T120407Z`) was delivered from Kali and fully captured
(the Kali IP, all four downloaded+executed tools, the C2 beacons, the SSH scan). Both windows it spanned
went **RED**, but the alert was a **false positive and the attack was missed**:

| window | flagged | sg_score | kept subgraph (what alerted) | LLM |
|---|---|---|---|---|
| 15:15 | 48 | 33.8 Moderate | `TiWorker.exe` — Windows servicing manifests | false_positive 0.95 |
| 15:20 | 38 | 26.6 Moderate | `powershell.exe` — **our own `start-live.ps1` watchdog** | false_positive 0.72 |

The attack's own nodes scored **in-distribution** (flagged = score > τ was False for all of them):

| attack node | score | τ | flagged |
|---|---|---|---|
| `rclone.exe` (process) | −1.40 | −0.210 | False |
| `PsExec64.exe` (process) | −1.46 | −0.210 | False |
| C2 socket `10.21.4.30:8080` | −1.50 | −0.523 | False |
| scan socket `10.21.4.30:22` | −1.56 | −0.523 | False |

## Root cause

**1. The subgraph score counts flagged nodes; it does not weigh attack severity.** `src/pipeline/alg1.py`
(`construct`, l.170):

$$ \mathrm{sg\_score} \;=\; \sum_{n \in \text{subgraph} \,\cap\, \text{flagged}} \mathrm{prob}(n), \qquad
   \mathrm{prob}(n) = \mathrm{clamp}\!\left(\frac{\mathrm{score}(n) - t_{\min}}{t_{\max}-t_{\min}},\,0,\,1\right) $$

Each flagged node adds up to 1.0, so **sg_score is essentially the number of flagged nodes** in the
connected cluster. A 46-node benign cluster (servicing manifest reads, or the watchdog's .NET/module
startup reads) sums to ~34 and always outranks a small attack cluster. Level is read straight off this sum
(Moderate ≥ 10, [PAPER] §7.2.1). In the persistent history (`sessions/history.jsonl`), **every alerting
window is an LLM-confirmed false positive except one** — the 2026-11-05 00:50Z attack, which had the
*fewest* flagged nodes (18) but the right ones. [AUTHOR CODE] defines the sum-of-prob; the consequence is
[OURS]/[INFERENCE].

**2. The attack's detection rests on a single fragile NET node.** The attack only ever reaches Moderate when
the **C2 beacon socket** is flagged and seeds a subgraph that pulls in the connected attack nodes. Same
model, same τ, two runs:

| C2 socket `10.21.4.30:8080` | own score | flagged | own features (in_recv) |
|---|---|---|---|
| 00:50Z (detected) | **+1.165** | True → seeds the attack subgraph | 0.999978 |
| 15:20Z (missed) | **−1.50** | False → in-distribution | 0.999987 |

The socket's **own feature vector is nearly identical** in both, with the **same owning processes** (rclone +
powershell, both themselves unflagged). The anomaly score is a **3-hop `RGCNConv` embedding** (D5), not a
function of the node's 15 features alone, so it moves with the whole window's graph context: between the two
runs the owner embeddings drifted (rclone −0.90→−1.40, powershell −1.35→−1.43) and the socket's embedding
rode down inside the hypersphere. Nothing in the feature set encodes **peer-IP novelty** (a never-seen
external host); the NET features are only Send/Recv action counts + idle stats, which look in-distribution.

## What we changed (2026-11-05)

- **Watchdog excluded at the converter** (`etw2nodlink.own_tooling_pids` / `filter_own_tooling_csv`, called
  from `build_experiment.py` before windowing and from `engine._score` live). Kernel ETW has no command
  line, so the watchdog `powershell.exe` is identified by the one stable signal — it reads our own
  `\start-live.ps1` or anything under `c:\programdata\etwlab\`. Validated on the real windows: the exact
  process that formed the 15:20 FP subgraph (p2944) is dropped with its 265 edges; only that one process is
  caught (the attack's RCE PowerShell is retained). Fixes the 15:20-type FP, not the 15:15 TiWorker one.
- **`exp08`: a 3-day idle baseline** (`range-3d-20261105`, 11-02 15:38Z → 11-05 15:50Z, attacks/RDP/canaries
  excluded via `src/artifacts/exclusions/proxmox-20261105.json`). A longer baseline is what addresses the
  TiWorker / servicing FPs (more servicing cycles seen in training), as the 2-day retrain did for daily jobs.
- **`exp08`: RGCN 3→2 layers** (`--layers 2`), a gradual step (user, 2026-11-05) to make each node's score
  depend on a shallower neighbourhood and reduce embedding drift. 1 layer (`exp09`) is the next step only if
  2 helps but not enough. Honest expectation: fewer layers may cut the benign FP without making the attack
  fire — the C2 socket's 2-hop neighbourhood (owner proc) is still in-distribution. The durable fix for
  detection is a peer-novelty NET feature (a new [OURS] deviation), not layer tuning.

Related: `retrain-2day-fp-lesson.md`, `real-tool-download-exec.md`, DEVIATIONS D5, D8, PROGRESS,
[[live-detector-benign-fp]].
