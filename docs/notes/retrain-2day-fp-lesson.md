# Lesson: retraining on 2 days of idle data (exp06 → exp07)

Written 2026-11-04. Label source for every count below: **operator assertion** that no attack ran in the idle
ranges, and the attack's own run time for the attack windows. Cut-off: Moderate from 10 ([PAPER] §7.2.1) for all
models. Counts are small, so read them as counts, not rates. Not comparable with the paper's tables (DEVIATIONS D2).

## What happened

exp06 ran live for two days and raised too many alerts: of 431 scored windows (11-02 23:15Z → 11-04 11:30Z), 16
reached a subgraph score ≥ 10. All 11 that the LLM judged came back `false_positive`, and they used up the daily LLM
budget. The user's reading was that the model had not seen these activities in training. That was right for most.

## Result

Held-out test: three models trained only up to 11-03 17:25Z (`exp07v-s360/361/362`), then exp06 and all three
replayed the same 228 windows. The idle and human-session windows came after the training cut-off and the attack
windows were excluded, so none of the three had seen them.

| Label | Windows | exp06 red | seed 360 | seed 361 | seed 362 | mean ± std |
|---|---|---|---|---|---|---|
| idle | 200 | 4 | 0 | 0 | 0 | 0.0 ± 0.0 |
| human session | 21 | 5 | 3 | 1 | 1 | 1.7 ± 1.2 |
| attack | 7 | 0 | 2 | 0 | 0 | 0.7 ± 1.2 |

The promoted **exp07** (all clean data, ~41 h, seed 360) on the same replay:

| Label | Windows | exp06 red | exp07 red | Is this a fair test of exp07? |
|---|---|---|---|---|
| attack | 7 | 0 | 2 (35.3, 33.5; exp06 9.0, 2.0) | Yes: excluded from training |
| human session | 21 | 5 | 5 | Yes: excluded from training |
| idle | 200 | 4 | 0 | **No**: exp07 trained on these windows |

What this supports: more coverage removed the recurring-job false positives, on windows the models had not seen.

What it does not support:
- **Attack detection is not stable across seeds.** Only seed 360 goes red (41.1 and 38.9 in `exp07v`, 35.3 and 33.5 in
  exp07); seeds 361 and 362 stay ≤ 4.9. exp07 kept seed 360 because it is the project default, not because of this
  result. Choosing a seed by its attack score would fit the model to the test.
- **It is one recorded attack**, the 11-02 run, replayed. No new live attack has been run against exp07.
- **Idle on exp07 itself is only tested by the live soak**, which is still running.

## Lesson 1: a baseline must cover the longest cycle you care about

exp06 trained on 3 h (12:30–15:30Z). Windows runs jobs on much longer cycles, so a 3-hour slice cannot contain them:

| Cycle | Seen at | What ran |
|---|---|---|
| Daily | ~05:05Z | .NET `ngen`, `taskhostw`, `compattelrunner` (maintenance) |
| Daily | ~11:15Z | `wlidsvc` and crypto/identity files, an exe from `windows\systemtemp` |
| ~10 h | 07:05, 17:15, 03:15Z | `microsoftedgeupdate` loading 32-bit `syswow64` DLLs |

Each job looks exactly like "an unfamiliar process plus a burst of DLLs", which is also what an attack looks like.

Duration matters only because it brings **variety**. PROGRESS 2026-10-22 said more minutes of the same workload would
not fix a browser false positive, and that is still true. Two days helped here because they contain jobs three hours
do not. The rule: pick a range that spans the cycle, at least 24 h for daily jobs.

## Lesson 2: replay on data the model has not seen

Retraining on a range and replaying that range proves nothing: the model has memorised it. So the check has three parts:

1. Train only on the earlier part, and replay the later part. Each recurring job appears once on each side.
2. Replay the old model on the same windows, so both are judged on identical data.
3. Train the final model on everything, then test it on what it could not have seen: excluded windows and new live data.

Check the replay first. exp06's replay matched its live scores in 214 of 215 windows and the attack exactly (9.0 / 2.0),
which is what makes the comparison trustworthy. The one difference was a burst where live had dropped late lines.

## Lesson 3: "idle" data contains more than idle

- **Attack and human sessions.** The range held the 11-02 attack and three RDP sessions over the team VPN, so whole
  windows are excluded (`src/artifacts/exclusions/proxmox-20261104.json`, `lab train --exclude-file`). Training on them
  would teach the model that `cmd`, `nslookup`, `ping` and the attack's tools are normal. A window is dropped whole, not
  trimmed, because its features only mean something as one 5-minute graph.
- **Our own canary.** `etwctl.ps1:78` runs `cmd.exe /c "whoami > nul"` at every 6-h rotation, an attack-shaped process
  tree. Rotation windows are excluded too.
- **Our own watchdog.** `etwlab-live` starts `powershell.exe` from `svchost` every 5 minutes (563 of 583 gaps are 5 min),
  so PowerShell is in every training window (758 of 758). The attack's RCE runs through PowerShell. [INFERENCE] This
  explains the weak attack score better than the earlier "XAMPP idling" reading: `httpd`/`mysqld` appear in only 3
  windows. **Not tested yet.** A watchdog that doesn't use PowerShell would need a new baseline.

Interactive sessions still go red on exp07 (5 of 21 windows). That is correct for an idle model. Expect alerts whenever
someone logs in, and ask teammates to note the time.

## Lesson 4: passing a gate doesn't mean the model trained

The first model passed every gate: `emb_std ≠ 0`, `val_fpr` 0, thresholds written. It was also nearly untrained:
`best_ep=4`, and FILE τ was +4.94 instead of about −0.1. It would have flagged almost nothing, which looks exactly like
"false positives fixed".

Cause: the trainer keeps the epoch with the highest validation TNR (true negative rate, the share of benign validation
nodes scored below τ). Early in training τ is huge, so TNR is exactly 1. As the sphere tightens, a few real nodes cross
τ, TNR drops slightly and never returns. So "highest TNR" always picks the earliest epoch. exp06 escaped by luck: its
148 validation nodes stayed at 1.0, and ties keep the later epoch. Raising `--val-windows` to 60 (10,338 FILE nodes)
exposed it.

Fix: `--tnr-tolerance`, a value within tolerance of the best TNR so far counts as a tie. Worked examples with 0.01:

- Peak 1.0000, epoch 38 has 0.9983: 0.9983 ≥ 1.0000 − 0.01 = 0.99, a tie, so the later epoch is kept.
- Peak 1.0000, an epoch has 0.9850: 0.9850 < 0.99, a real decline, so it isn't kept and the patience counter rises.

The gate now also checks `best_epoch` and that τ is on the expected scale. Validation data only was used to set this.

## Lesson 5: operational notes

- **Loki keeps 72 h.** The attack's raw session copy had been pruned, and Loki was the only copy left. Pull anything you
  want to keep first (`attack-20261102`).
- **Pick a new experiment id.** `lab pull`'s default id is `range-<UTC start>`. It refuses an existing folder, and only
  `--force` replaces it, so a same-start pull would stop or, with `--force`, replace the old model's experiment. Use
  `loki_pull.py --id`, because `lab pull` has no `--id`.
- **Cost on the trainer** (4 CPU cores, 5.8 GB): build 2 min; 839k edges 10:30 and 2.7 GB; 1.29M edges 17:43 and 3.9 GB.
  Replay is paced by the 120× speed, about 12 min per model. `ocr-live` must stay stopped throughout, so live history
  has a gap of about 1 h (12:05–13:07Z on 11-04). Those windows can be replayed from Loki while retained.
- `systemctl` shows `failed` after a plain stop: `ocr-live` exits with status 143 on SIGTERM. It is not a crash.

Related: `proxmox-idle-training.md`, DEVIATIONS D2, D5, D8, PROGRESS.
