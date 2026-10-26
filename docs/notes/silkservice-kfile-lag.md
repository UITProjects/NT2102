# Live windows have no file events: the SilkService kfile lag

Status as of 2026-10-26: cause found in the code. **Decision (user, 2026-10-26): replace SilkService and
Alloy with our own Python collector `src/etw-python`** (see "Decision" below, DEVIATIONS D10); not built yet. Measurements are from `ws-etw` on
2026-10-25 (DEVIATIONS D8 correction, PROGRESS "OPEN"). This note explains why the events go missing.
Fix options are at the end.

Tags: [OURS] what this project does · [INFERENCE] a guess · **SilkService code** = mandiant/SilkETW tag
`v0.8` (the version we deploy, `src/ansible/group_vars/all.yml:48`), file `SilkService/ETWCollector.cs`
(identical on `master` @ 093f57201525).

```
Windows activity ──► ETW Kernel-File ──► SilkService ──► kfile.json ──► Alloy ──► Loki ──► loki_source ──► engine
                                         (1 core, the                                                  (drops lines older
                                          bottleneck)                                                  than watermark)
```

## Symptom

- Every live window of `live-20261022T155035Z` has **zero Read and Write edges**. The exp03 training
  windows have about 14,000 Read and 500 Write edges each.
- The live engine's `late_dropped` counter ≈ every kfile line.
- So the Oct 22 "attack detected" result is not valid, and the Oct 25 rerun missed the attack (D8).

There are two separate parts. Part 1 is how the engine drops the lines. Part 2 is why they are late.

## Part 1: why the engine drops late lines (`src/live/engine.py`)

Variables:

| piece | type | meaning |
|---|---|---|
| `S` | `dict` | the current session's state |
| `t` | `int` | one event's timestamp, epoch milliseconds |
| `clock_ms` | `int` | newest event time seen from *any* provider (kproc, kfile, knet) |
| `self.lateness_ms` | `int` | 20,000 (20 s, `--lateness-s`) |
| `wm` | `int` | watermark = `clock_ms − lateness_ms` |
| `S["released_wm"]` | `int \| None` | the watermark the engine has already processed up to |
| `S["heap"]` | `list[tuple]` | min-heap of waiting events, ordered by `(t, stream, offset)` |

Flow:

1. Each new line is first written to the session's raw copy (`engine.py:256-259`). This happens even for
   lines that are dropped later, which is why a laptop replay can recover them.
2. If `t < S["released_wm"]`, the line is counted in `late_dropped` and discarded (`engine.py:267-269`).
   Otherwise it goes into the heap.
3. When the clock moves, `_advance` pops every event with `t < wm` in time order, feeds it to the
   converter and sets `released_wm = wm` (`engine.py:246, 274-286`).
4. A window `[start, end)` closes and is scored once `end ≤ wm` (`engine.py:295`).

**Why a late event can't simply be inserted:** the converter (`etw2nodlink.Graph`) is stateful and
order-dependent. It keys processes by PID + start time, and it maps a file object to a path from the
earlier Create event. Once later events have been processed, an older one can't be slotted in behind them.
So the drop is **per event**, even while the event's window is still open.

Two examples:

- **Lag of 1 minute** (early in a run).
  - At 14:47:30 a kproc event stamped 14:47:30 arrives, so `wm` = 14:47:10.
  - A kfile line stamped 14:46:30 arrives at the same moment.
  - 14:46:30 < 14:47:10, so it is dropped, although its window [14:45, 14:50) is not scored yet.
- **Lag of 9 minutes** (25 minutes into a run).
  - At 14:50:20 kproc pushes `wm` to 14:50:00. The window [14:45, 14:50) is scored with zero Read/Write edges.
  - The kfile lines arriving now are stamped around 14:41, far below `wm`, so they are dropped.
  - Their window [14:40, 14:45) was scored minutes earlier.

kproc and knet keep up with real time, so they set the clock. Any kfile delay above 20 s means a drop.
Offline (`build_experiment.py`) sorts the whole capture after the fact, so lateness never matters there,
and the problem only shows up live.

## Part 2: why kfile is late (the collector)

### Measured (2026-10-25)

- From a fresh run, Kernel-File event time falls behind wall time at once (9+ minutes behind after
  25 minutes). kproc and knet stay near real time.
- SilkService uses about 1 CPU core. Its memory stays flat at ~275 MB.
- Loki's kfile lag equals the lag in `kfile.json` on disk, so Alloy and Loki add nothing.
- Nearly all recent kfile events are SilkService's own writes to `kfile.json` plus Alloy's reads of it.
- The figures disagree: "event time advances at ~20–25 % of wall time" would mean ~19 minutes behind
  after 25 minutes, not 9+. Re-measure.

### Mechanism (verified in SilkService code)

For each event, the callback (`ETWCollector.cs:219`) does all of the following on **one thread**:

1. Builds a record. It may also call `Process.GetProcessById` when the process name is empty
   (`ETWCollector.cs:274`).
2. Parses the event's XML (`data.ToString()` → `XmlTextReader`).
3. Serializes the record to JSON (`ETWCollector.cs:315`).
4. Writes it like this (`ETWCollector.cs:117`):

```csharp
// JSONData: string = one event as JSON; Path: string = <run>\kfile.json (from etwctl.ps1:60)
File.AppendAllText(Path, (JSONData + Environment.NewLine));
```

`File.AppendAllText` **opens, writes and closes** the file on every call. There is no long-lived
buffered writer. Our Kernel-File keywords `0x380` = Create (0x80) + Read (0x100) + Write (0x200)
(`etwlab.json.j2:15`). So every line SilkService writes produces new events of its own:
- ≥ 1 Create (the open);
- ≥ 1 Write.

These come from SilkService itself, and they go back into SilkService's own queue.

Alloy tails `kfile.json`. Every read it does is a Read event, which SilkService turns into another line.
That line means another Create + Write, and Alloy reads it in turn.

```
1 real file event ──► SilkService writes 1 line ──► 1 Create + 1 Write (by SilkService) ──► 2 more lines ──► 4 ──► 8 …
                                │
                                └──► Alloy reads the line ──► 1 Read (by alloy) ──► 1 more line ──► …
```

Two examples of the growth:

- **A program does 100 file operations.**
  - SilkService writes 100 lines, which creates about 200 self-events.
  - Writing those creates about 400, then 800, and so on.
  - Nothing limits this except CPU. SilkService has one callback thread, so it stays at about 1 core and
    the queue keeps growing. This fits the measurements (lag from the first minute, 1 core, mostly
    self-events).
- **kproc, by contrast.** Writing `kproc.json` produces Kernel-*File* events, not Kernel-*Process* events.
  So kproc has no loop and stays near real time. knet is the same.

### Why the current exclusions don't help

- [OURS] The converter already drops edges from `silkservice` and `alloy` (`etw2nodlink.py:25`,
  `EXCLUDE_IMAGES`). That runs on the trainer, after SilkService has already received, serialized and
  written each event, so the loop has already happened. The filter has to run **inside the collector,
  before the write**.
- SilkService's own filter can only *keep* one value (`ETWCollector.cs:222-240`). With
  `FilterOption=ProcessName` and value `X`, any event whose name is not `X` is dropped. There is no
  "everything except SilkService and Alloy" option.

### All SilkService options checked (`h_SilkUtility.cs`, `h_SilkParameters.cs`)

| option | values | can it exclude SilkService / Alloy? |
|---|---|---|
| `CollectorType` | Kernel, User | no |
| `ProviderName`, `UserKeywords` / `KernelKeywords` | provider + categories | no: categories, not processes |
| `OutputType` + `Path` | file, url, eventlog | no |
| `FilterOption` + `FilterValue` | EventName, ProcessID, ProcessName, Opcode | no: keep-only, one value |
| `YaraScan` + `YaraOptions` | rule folder; None, All, Matches | indirectly (below) |

- **`url` output** (`ETWCollector.cs:128-155`): one synchronous HTTP POST per event (10 s timeout), body =
  that event's JSON, no batching, no auth headers, no retry (a failed POST drops the event). It would move
  the loop to Kernel-Network (each POST = SilkService Send + Recv events), be slower per event, lose events
  while the endpoint is down, and Loki's push API cannot take a bare event.
- **Yara as an exclude filter** [INFERENCE, untested]: with `YaraOptions=Matches` only events matching a
  rule are written (`ETWCollector.cs:97`); a rule matching JSON that does *not* contain
  `"ProcessName":"SilkService"` / `"alloy` would break the loop with config only, but every event is still
  serialized + scanned, kept events get re-parsed with a `YaraMatch` field added, and the per-line open
  stays.
- SilkETW is Mandiant's, Apache-2.0, last release v0.8 (2019-09), repo archived (last commit 2023-06): no
  upstream fix will come.

### A second problem in the same data: network events point at the wrong process

Verified on `live-20261025T073042Z/raw/knet.json`: in **77 %** of the Kernel-Network events (14,826 of
19,212) the event header's PID is not the payload's PID. The header PID is the thread that happened to run
when the packet was processed (Idle 0 in 10,441 events, System, even SilkService); the payload PID is the
owning process (e.g. msedge). TraceEvent writes the payload field as `_PID` and its header copy as `PID`, and
`etw2nodlink._net()` reads `pid`, so most NET edges in every capture so far (exp03 included) hang off the
wrong process. The new collector writes the payload PID as `PID`; the converter will prefer `_pid` for old
captures (DEVIATIONS D8).

### Still open

- **Late or lost?** ETW reports `Buffers Lost: 0`, yet memory stays flat while the lag grows. A growing
  backlog should use memory somewhere. [INFERENCE] Some events may be lost at the real-time layer, not
  just delayed.
- **Is the training data affected?** The Oct 22 1-hour training capture kept a full 77-minute kfile
  span. That hasn't been explained, and it hasn't been checked whether its kfile thinned out over time
  (Read edges per window across its 37 windows).
- **How much is the open/close cost, and how much the self-events?** [INFERENCE] Both add to the load.
  The fix below removes both.

## Ideas considered and rejected

- **Pull each window from Loki after it ends** instead of following Loki every 1 s. Live already pulls
  (`LokiSource.run_forever`, poll 1 s). Pulling later only waits longer for the same late data:
  - the wait must equal the kfile lag;
  - the lag grows the whole time SilkService runs (rotation every 6 h);
  - lost events never arrive.

  It also hides the problem, because no `late_dropped` counter would show it.
- **Raise `--lateness-s`** (for example 900). This needs no code and is a useful test: if the windows
  then get about training-level Read/Write counts, the events are late, not lost. But it isn't a fix,
  because the lag keeps growing.
- **Remove the HTTP shipper** (`etwship.py`). Live doesn't use it. Only replay and `compare_replay.py`
  do, so removing it changes nothing about this issue.

## Decision (user, 2026-10-26)

Option 2 below, in **Python** (stdlib + ctypes, uv-managed) at `src/etw-python`, and it also replaces
Alloy: the collector pushes its own run files to Loki with the same labels. SilkService and Alloy are removed
completely once the collector passes the checks at the end of this note plus a parity run against
SilkService. Design and plan: DEVIATIONS D10, PROGRESS "Next 3 steps", `src/etw-python/README.md`.

## Fix options (as considered before the decision)

1. **Patch SilkService**. It is open-source C#, and the change is about 10 lines:
   - In the callback, before building the record, drop events whose `ProcessID` is SilkService's own PID
     (`Process.GetCurrentProcess().Id`), or whose process name is `alloy`. This breaks the loop.
   - Replace the per-event `AppendAllText` with one `StreamWriter` that stays open and flushes about
     every 1 s. Even unfiltered, that turns one open + write per line into a few writes per second.

   The JSON format stays the same, and the converter already drops these edges. So [INFERENCE] the graph
   content shouldn't change, and exp03 stays usable.

   Cost: we have to build SilkService (.NET Framework, on Windows), and the `win_collector` role ships
   our build instead of the v0.8 zip. This goes in DEVIATIONS.
2. **Our own small ETW consumer** (C# with TraceEvent, or krabsetw). Full control, but more work, and
   its output must match SilkETW's JSON field names for the converter.
3. **SilkService `eventlog` output.** This avoids the per-line file opens. But the Event Log service
   still writes `.evtx` files, Alloy would need a different source, and the raw line format changes. It
   isn't verified to break the loop.

Excluding Alloy by name means an attacker binary named `alloy.exe` goes unseen. The converter already
does this today, so it isn't new, but it belongs in DEVIATIONS.

## How to check a fix

1. Start a fresh run and compare the newest kfile `TimeStamp` in Loki with wall time. It should be
   < 20 s behind and stay there for ≥ 30 minutes.
2. Check that `late_dropped` in `/api/status` stays ≈ 0.
3. Check that each live window's Read/Write counts are in the range of the exp03 training windows.
4. Check that SilkService's CPU is well below 1 core.
