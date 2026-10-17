# From Windows logs to model input

How one second of Windows activity becomes the tensors the OCRGCN reads. One toy capture is carried
through every stage; all numbers below come from running the real code on it (not hand-made).

Tags: [PAPER] paper text · [REPO] upstream behaviour (`OCR-APT/src/encode_to_PyG.py`; rounding and
zero-column drop spot-checked) · [OURS] what this project does · [INFERENCE] a guess.

```
SilkETW JSON lines ─(1)─► edge CSV ─(2)─► windows ─(3)─► 15 features / node ─(4)─► x, edge_index, edge_type ─► OCRGCN
 (VM, 3 providers)        (6 columns)     (w0007_ ids)    (+ frozen scaler)         (+ frozen relation list)
```

Offline (training) and live (demo) use the **same code** for stages 1, 3 and 4. Only stage 2 differs.

## The toy capture

Eight events on a VM, all on 2026-10-07 around 10:00:00 UTC:

| time | provider / event | payload (short) |
|---|---|---|
| 00.000 | Kernel-Process `ProcessStart` | explorer (pid 100) starts `cmd.exe` (pid 4321) |
| 00.010 | Kernel-Process `ImageLoad` | cmd loads `ntdll.dll` |
| 00.020 | Kernel-Process `ThreadStart` | not mapped to an edge |
| 01.000 | Kernel-File `Create` | file object `0xAA` = `C:\tmp\x.txt` |
| 01.500 / 04.500 | Kernel-File `Write` ×2 | on `0xAA` |
| 03.000 | Kernel-Network `Datasent` | top-level ProcessID **4**, payload `PID=4321`, to 93.184.216.34:443 |
| 09.000 | Kernel-File `Read` | on `0xAA` |

Plus two decoys: a `Write` by `SilkService` (the collector itself) and a `Read` by pid 77 on a file object whose
`Create` we never saw.

## 1. Raw events → edges (`src/pipeline/etw2nodlink.py`)

Every ETW event is classified by (provider, event name) (`classify`, ps1 l.157-181). Only these map to an edge;
everything else is counted under `unmapped` in `*_summary.json`.

| ETW event | edge type | source → destination |
|---|---|---|
| Kernel-Process `ProcessStart` | Start | parent process → child process |
| Kernel-Process `ImageLoad` | Load | process → DLL (FILE) |
| Kernel-File `Read` / `Write` | Read / Write | process → FILE |
| Kernel-Network send / recv | Send / Recv | process → NET |

All six point process → object (`DIRECTION`, ps1 l.103). [INFERENCE] NODLINK's own direction convention is
undocumented and still unchecked (see `docs/PROGRESS.md`). Stage 4 adds inverse edges, which probably softens
the effect on message passing, but the `out_*` / `in_*` feature split follows the direction directly.

**Node identity** (this is what keeps train and test comparable):

- PROCESS: `p<pid>_<start-ms>` (ps1 l.280). A process that was already running when the capture began has no
  `ProcessStart`, so it gets `p<pid>_pre_<run>` (l.235). Raw PIDs are reused by Windows, so a bare PID would
  merge unrelated processes.
- FILE: `f` + first 16 hex of `sha1(path)`; NET: `n` + `sha1("ip:port")` (`obj`, l.238). Same path → same ID in
  every capture. Paths are lower-cased and `\Device\HarddiskVolumeN\` is mapped to `c:` (`path`, l.211).
- File events carry a file *object*, not a path. The converter remembers `Create` / `NameCreate` events
  (`on_fcreate`, `on_fname`) and looks the path up on each Read/Write (l.306-313). No path known → the event is
  **unresolved** and dropped (counted).
- Network events: the converter takes the `PID` field inside the payload and falls back to the top-level
  ProcessID only if it is missing (`_net`, l.322). In the toy event the top-level ID is 4 but the payload says 4321.

Result for the toy capture (`excluded: Write 1`, `unresolved: Read 1`, `unmapped: ThreadStart 1`):

```
source-id            source-type  destination-id     destination-type  edge-type  timestamp
p100_pre_run1        PROCESS      p4321_1791367200000 PROCESS          Start      1791367200000
p4321_1791367200000  PROCESS      f7c6…dfe           FILE              Load       1791367200010
p4321_1791367200000  PROCESS      f342…21a           FILE              Write      1791367201500
p4321_1791367200000  PROCESS      n740…6af           NET               Send       1791367203000
p4321_1791367200000  PROCESS      f342…21a           FILE              Write      1791367204500
p4321_1791367200000  PROCESS      f342…21a           FILE              Read       1791367209000
```

Timestamps are epoch **milliseconds**. 5 nodes, 6 edges. This is the NODLINK W10 schema the upstream code expects.

## 2. Edges → windows

**Training** (`src/pipeline/etwwindow.py`): the capture is cut into 5-min
windows with a 2-min stride, so a 20-min capture gives 8 windows. Window *i* covers $[s_i, s_i + 300\,\text{s})$ with
$s_i = s_0 + 120\,i$. Example: $s_0 = 14{:}56{:}33$ → w0000 = 14:56:33–15:01:33, w0001 = 14:58:33–15:03:33
(real values from `windows.csv`). An event at 14:59:00 lies in both. Every window gets its own copy of the nodes
(`w0000_`, `w0001_` prefixes), so the pooled file is a **disjoint union** of window graphs: no edge, no message
passing and no feature crosses windows. Only full windows are emitted; a gap > `--max-gap-s` starts a new segment.

**Live** (`src/live/engine.py`): events are re-ordered by event time, released once older than the watermark
(VM clock − 20 s lateness, `_advance` l.264), and cut into clock-aligned tumbling windows (stride = window by
default; `--stride-min` / `--origin-ms` change it, `_close_windows` l.278). A window closes when the watermark passes its end; a window that began before the first
event is `partial` and is not scored. [OURS] Live window node IDs are **not** prefixed: one window is one graph,
scored on its own.

For the rest of this note, treat the 6 toy edges as one window and prefix `w0000_`.

## 3. Window edges → 15 features per node (`encode_to_features.py::build_features`, l.131)

Per node, 12 action counts + 3 idle statistics. Column order (fixed, `ACTIONS` l.27): for each of
write, read, send, recv, start, load, the pair `out_<a>`, `in_<a>`. Each edge adds 1 to `out_<type>` of its
source and 1 to `in_<type>` of its destination (`action_counts`, l.76).

Counts for the toy window (non-zero columns only):

| node | out/in counts | n_events |
|---|---|---|
| `p4321…` (cmd) | out_write 2, out_read 1, out_send 1, out_load 1, in_start 1 | 6 |
| `f342…` (x.txt) | in_write 2, in_read 1 | 3 |
| `n740…` (93.184.216.34) | in_send 1 | 1 |
| `f7c6…` (ntdll) | in_load 1 | 1 |
| `p100_pre…` (explorer) | out_start 1 | 1 |

**L2 normalisation** ($\hat v = v / \lVert v \rVert_2$, l.110), per node, over its 12 counts. Examples:

1. cmd: $v = (2,1,1,1,1)$ on five columns, $\lVert v\rVert_2 = \sqrt{4+1+1+1+1} = \sqrt 8 = 2.83$ →
   out_write $= 2/2.83 = 0.707$, each of the others $= 1/2.83 = 0.354$.
2. x.txt: $v = (2,1)$, $\lVert v\rVert_2 = \sqrt5 = 2.24$ → in_write $0.894$, in_read $0.447$.

Only the *mix* survives: a node that wrote 2 and read 1 looks the same as one that wrote 200 and read 100.
Every node has at least one edge, so the norm is never 0.

**Idle statistics** (`idle_stats`, l.86). Each edge timestamp belongs to *both* endpoints. For a node,
sort its timestamps and take consecutive gaps in seconds; then avg / max / min of the gaps. A node with one
event has no gap and gets 0, 0, 0. Example, cmd: event times 0.000, 0.010, 1.5, 3.0, 4.5, 9.0 s →
gaps 0.01, 1.49, 1.5, 1.5, 4.5 → avg 1.80, max 4.5, min 0.01. x.txt: 1.5, 4.5, 9.0 → gaps 3.0, 4.5 →
avg 3.75, max 4.5, min 3.0. [REPO] The three aggregates are then rounded to whole seconds (`repo_rounding`),
so cmd becomes avg 2, max 4 (numpy rounds .5 to even), min 0.

**Min-max scaling** ($x' = (x - \min)/(\max - \min)$, l.121). min and max are per idle column and come from a
**scaler fit on the training windows**, stored in `feature_scaler.json`. Examples:

1. Toy fit (max = 4 s): cmd avg $2 \to (2-0)/4 = 0.5$; cmd max $4 \to 1.0$.
2. Real training scaler (`artifacts/features/self-dataset/feature_scaler.json`: min 0, max 290 s): a live node
   with a 145 s idle gap $\to 0.5$; one with 300 s $\to 1.034$. Values above 1 are expected for live data
   (D3 in `DEVIATIONS.md`).

[OURS] The scaler is fit on train only and reused for test and live; all 15 columns are kept even if a
column is all zero, so the feature dimension never depends on the data. [REPO] upstream drops all-zero columns
and fits on train+test together.

Output: `features_model.csv`, shape [N, 15] float. Toy cmd row (column order as above):
`[0.707, 0, 0.354, 0, 0.354, 0, 0, 0, 0, 0.354, 0.354, 0, 0.5, 1.0, 0.0]`.

The human-readable version (raw counts, idle gaps in seconds, `n_events`) is `features_raw.csv`; use it for EDA.
For a step-by-step interactive walkthrough with 17 events, open `docs/explainers/edges-to-node-features.html`
(it narrates the upstream function; ours is the same logic as a pandas re-implementation).

## 4. Features + edges → tensors (`train_gnn_models.py::build_graph`, l.64)

Inputs: the window edge CSV and `features_model.csv`. Outputs for $N$ nodes and $E$ edges:

| tensor | shape | meaning |
|---|---|---|
| `x` | [N, 15] | the features from stage 3, rows in node order |
| `edge_index` | [2, 2E] | row 0 = source index, row 1 = destination index |
| `edge_type` | [2E] | relation id in $[0, R)$ |

Details that matter:

- **Node order**: nodes are sorted by (type, id) and numbered 0…N-1. Toy: `f342…`=0, `f7c6…`=1, `n740…`=2,
  `p100…`=3, `p4321…`=4. `x` is re-indexed to this order.
- **Relation = (source type, edge type, destination type)**, e.g. `process|write|file`. Each gets an id.
  [REPO] An inverse `file|inv_write|process` is added for every relation, so messages flow both ways.
  The edge list is therefore the forward edges followed by the same edges reversed ($2E$ columns).
- Toy: 5 forward relations (no Recv in this capture) + 5 inverse = $R = 10$. Edge 1 (Start, p100 → p4321) is
  `[3, 4]` with relation `process|start|process` = id 3; its inverse is `[4, 3]` with id $3 + 5 = 8$.
  Our real training run has 6 forward + 6 inverse = **12 relations** (`artifacts/runs/exp01/train_meta.json`).
- Multi-edges are kept: cmd wrote twice, so two `[4, 0]` edges with relation 4 appear.

The model then computes, per node type $t$, `emb = model_t(x, edge_index, edge_type)` of shape [N, 32]
(`score_window.py` l.108): the encoder sees **all** nodes, but only type-$t$ rows are scored by model $t$
(l.110). Score $= \lVert emb - c\rVert^2 - r^2$; above the threshold $\tau_t$ the node is flagged.
Next (not covered here): flagged nodes → Alg. 1 subgraphs + score filter (`alg1.py`) → optional LLM step (`alg2.py`).

## 5. What is frozen at live time

A live window is scored with things that exist only because of training (`score_window.py::score_edges`, l.83):

- **the scaler** (`feature_scaler.json`): refitting on one 5-min window would rescale idle times differently from
  training, and the model would see inputs it was never calibrated for;
- **the relation list** (`train_meta.json`): relation id $k$ must index the same learned matrix $W_k$ as in
  training. Edges whose (src type, edge, dst type) triple is not in the list are dropped with a warning;
- **the feature order** and **per-type models + $\tau_t$**.

## Open checks

Converter loss (unresolved Reads/Writes), edge direction vs NODLINK, and the old un-windowed edges file:
see `docs/PROGRESS.md` "Open items" and the pitfalls in `src/README.md`.

## Reproduce

The toy capture above is built on the converter's own selftest (`src/pipeline/etw2nodlink.py --selftest`,
which Ansible also runs on the VM). The host selftests below exercise the same code paths on their own, smaller toys; they do
not print the numbers in this note:

```
uv run python src/pipeline/encode_to_features.py --selftest
uv run python src/pipeline/score_window.py --selftest
```

(Selftests prove "it runs", not "it detects attacks".)
