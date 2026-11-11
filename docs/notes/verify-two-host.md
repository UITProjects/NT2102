# Next action: verify the `ws-etw-1` model and the correlation layer

Status (2026-11-11): **§2 steps 1–3 done, model not accepted; §1 and §3 not started.** Results are in `docs/PROGRESS.md` (top section).
No check here needs new attack code.

Keep three claims apart: "it runs" (selftests, done), "it works" (below), and detection of real cross-host movement, which nothing
here proves.

## 1. Deploy

```bash
cd src/ansible && ansible-playbook -i proxmox.ini ubuntu.yml --tags project      # from main: PR #14 and #17 are merged
```

Run it with `--check --diff` first. The trainer is on `retrain-to-avoid-false-positive` `b4c1c29` with a hand-edited unit and no
`/etc/nt2102-hosts.json`; this run reconciles both and restarts `ocr-live`.

Then on the trainer: `/etc/nt2102-hosts.json` lists both VMs, `lab status` says `converter_check match` for exp08, and
`ocr-live` still scores `ws-etw-0` only: nothing changes until `lab hosts` is run.

```bash
lab promote exp10 --host ws-etw-0
lab promote <ws-etw-1 model> --host ws-etw-1      # none has passed the gate yet (exp11-ws1: NET fails)
lab hosts
```

## 2. The `ws-etw-1` model

`exp11-ws1` (55 h of idle, 2026-11-11) fails the gate on NET; `exp09-ws1` (10 h) was provisional. Before step 4, two things are open:
the NET stopping-rule decision, and the owner of the once-a-minute `C:\Lab` task on `ws-etw-1` (PROGRESS). Steps:

1. Retrain on about 3 days, `training-runbook.md`. Done: 11-06 20:30Z → 11-09 10:10Z, the last clean idle before the VM was changed.
2. Pass the gate in that runbook. Watch `best_ep` on NET and PROCESS, and τ across seeds.
3. Held-out idle: train only on the earlier part of the range and replay the later part (`retrain-2day-fp-lesson.md`). Count red windows,
   stating the label source. Replay windows the model trained on prove nothing.
4. Live soak: both VMs idle for 24 h. Check each red window against known activity.

Passing shows idle stays green on `ws-etw-1`. It does not show detection: no attack has targeted that VM.

## 3. The correlation layer

In order, each on real events. Levels are in `src/live/README.md`.

1. **Baseline.** On a clean range: `lab flows --start <t> --end <t>`. Read the printed list. Few or no unicast flows are expected:
   idle sampling found only NetBIOS broadcast, which is dropped. Anything else needs a reason before it joins the baseline.
2. **Regression.** Replay a saved `ws-etw-0` session that still has `raw/` through a server started with one `--loki-host` and through one
   started with both (`--port 8010 --sessions-dir <dir>`, no `--loki-url`; `PORT=8010 ./replay.sh <session>`). Replays feed the first
   host. `lab compare --baseline <dir1> --candidate <dir2>` must show identical windows.
3. **Two-VM mode.** `lab hosts ws-etw-0 ws-etw-1 && sudo systemctl restart ocr-live`. Both timelines fill, the UI shows each "trained for" line.
   Watch **private memory** in Grafana "Trainer resources" through the catch-up, up to an hour of lines per host.
4. **`link`.** An operator opens a normal connection between the VMs, for example a file share. Expect one `link`, `seen by` both hosts,
   nothing red, no LLM call.
5. **`suspected`.** The same connection in a window where `ws-etw-0` is red from the existing DVWA kill chain. The chain's subnet sweep may
   create a link by itself [INFERENCE]; note it if so.
6. **`confirmed`.** The other end red too. An RDP login is expected to redden an idle model [INFERENCE], which tests the rule with benign activity.
7. **LLM document.** Open `sessions/campaigns/<id>/doc.txt`: `[network]` lines first, then lines tagged with each host.
8. **Switch back.** `lab hosts ws-etw-0`, restart, and check the banner, `/healthz`, and that assignments stayed.

Also check clock skew: compare the two ends' `first_ms` for one flow. Windows join by clock, and skew was never measured.

## What this does not prove

Steps 5 and 6 test the rule on real events, not detection: the connection is benign and only happens to sit near a red window. Showing that
real cross-host movement is caught needs an attack that crosses hosts. That is the user's decision and is not planned here.
A hop from a host that stayed green only ever shows as `link`.
