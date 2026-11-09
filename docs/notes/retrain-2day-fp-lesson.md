# Rules for an idle baseline

What retraining taught us, as rules. Steps are in `training-runbook.md`. Results are in `docs/PROGRESS.md`. Counts here come from one lab
and operator-asserted labels: read them as counts, not rates (D2).

## 1. Cover the longest cycle you care about

Windows runs jobs on long cycles. Each looks like "an unfamiliar process plus a burst of DLLs", which is also what an attack looks like.

| Cycle | Seen at | What ran |
|---|---|---|
| Daily | ~05:05Z | .NET `ngen`, `taskhostw`, `compattelrunner` |
| Daily | ~11:15Z | `wlidsvc`, crypto/identity files |
| ~10 h | 07:05, 17:15, 03:15Z | `microsoftedgeupdate` loading 32-bit DLLs |

Use at least 24 h. Time only helps because it brings variety: more minutes of the same activity fix nothing. Moving from 3 h to about 2 days
took held-out idle reds from 4 to 0 (200 windows, 3 seeds).

## 2. Test on data the model has not seen

Replaying a range the model trained on proves nothing.

1. Train on the earlier part only, then replay the later part, so each recurring job falls on both sides.
2. Replay the old model on the same windows.
3. Train the final model on everything, then test it on excluded windows and new live data.

Check first that a replay matches live scores, or the comparison means nothing.

## 3. "Idle" contains more than idle

- **Attacks and human sessions:** exclude whole windows, `lab train --exclude-file`. A window is never trimmed: its features only mean
  something as one 5-minute graph.
- **Our canary:** `etwctl.ps1` runs `cmd.exe /c "whoami > nul"` at each 6-h rotation. It is attack-shaped, so exclude those windows.
- **Our watchdog:** dropped automatically (`subgraph-score-fragility.md`).

Interactive sessions go red on an idle model. That is correct: expect alerts when someone logs in.

## 4. Passing a gate does not mean the model trained

A model passed `emb_std ≠ 0` and `val_fpr` 0 while nearly untrained: `best_ep` 4, FILE τ +4.94 instead of about −0.1. It flagged almost nothing,
which looks exactly like "false positives fixed".

Cause: early on τ is huge, so validation TNR (true negative rate, the share of benign validation nodes under τ) is exactly 1. As the sphere
tightens, a few nodes cross τ, and "highest TNR" then always picks the earliest epoch.

Fix: `--tnr-tolerance`, where anything within tolerance of the peak counts as a tie, so the later epoch is kept. With 0.01:
- Peak 1.0000, epoch 38 has 0.9983: 0.9983 ≥ 0.99, a tie, so the later epoch is kept.
- Peak 1.0000, an epoch has 0.9850: 0.9850 < 0.99, a real decline, so it is not kept.

With few validation nodes this has a floor: 1 of 89 nodes is 0.011, above the tolerance. That alone is not the cause when `--patience` changes
nothing: then the tail holds behaviour training never saw (`training-runbook.md`, gate).

## 5. Operations

- Loki kept 72 h until 2026-11-09, now 90 days (`logged-on-session-fp-lesson.md`). Pull anything worth keeping before it expires.
- `lab pull` names experiments `range-<UTC start>[-<vm>]` and refuses an existing folder; `--force` replaces it.
- `systemctl` shows `failed` after a plain stop of `ocr-live`: exit 143 on SIGTERM, not a crash.

See DEVIATIONS D2, D5.
