# DEVIATIONS from OCR-APT (paper + upstream repo)

Each entry: what changed, why, expected impact, which claims it limits.
Tags: [PAPER] paper text · [REPO] verified in upstream code · [OURS] this project · [INFERENCE] unverified.

## D1. Windowed, near-live detection
- What: [PAPER] offline prediction over the whole test log. [OURS] 5-min windows (train: 2-min stride,
  8 windows over 20 min); every node is copied per window (`w0007_` prefix).
- Why: the live demo needs bounded, incremental scoring.
- Impact: nodes whose behaviour spans windows are seen in fragments; idle-time stats are capped by
  the window length (train max idle = 290 s, `src/artifacts/features/self-dataset/feature_scaler.json`).
- Limits: no claim about whole-log detection quality.

## D2. Own scripted Windows ETW data
- What: [PAPER] NODLINK / DARPA TC3 / OpTC. [OURS] SilkETW Kernel-Process/File/Network on a W10 VM,
  converted to the NODLINK SimulatedW10 schema; benign activity from a seeded scripted workload
  (`src/scripts/06-benign-workload.ps1`), plus background OS activity.
- Why: own end-to-end pipeline for the live demo; dataset small enough to inspect.
- Impact: benign behaviour is narrower than human use → likely optimistic false-positive rates;
  converter drops/unresolves some events (see converter `_summary.json`).
- Limits: our numbers are not comparable with the paper's tables.

## D3. Feature extraction re-implemented (`src/pipeline/encode_to_features.py`)
- What: [REPO] `encode_to_PyG.py::feature_engineering` drops all-zero columns and fits the idle
  MinMax scaler on train+test. [OURS] all 15 columns kept; scaler fit on train only and reused;
  no RDF/GraphDB/PyG step; repo rounding of idle gaps to whole seconds kept (switchable).
- Why: fixed feature dimension across train/test/live; no test leakage into scaling; CSV for EDA.
- Impact: test/live idle values may fall outside [0, 1]; overlapping windows produce correlated rows.
- Limits: feature values are not bit-identical to the repo's.

## D4. Minimal W10 RDF transform (`src/pipeline/w10_to_rdf.py`)
- What: [REPO] `transform_to_RDF.py` for all datasets. [OURS] SimulatedW10 CSV only, train split only
  for now, ground-truth file optional (missing = all benign), stricter Turtle escaping.
- Why: only the W10 path is needed; train-only lets the pipeline run before a test capture exists.
- Impact: nothing downstream of GraphDB (subgraph construction) can be evaluated yet.
- Limits: no Alg. 1 / Alg. 2 claims.

## D5. Trainer re-implemented (`src/pipeline/train_gnn_models.py`)
- What: [REPO] authors' PyGOD fork (`OCRGCN`), random 15 % validation, stop at first TNR decline,
  random seeds after run 0. [OURS] own `OCRGCN` (PyG `RGCNConv` ×3, 32-d, lr 0.005, β 0.5, warmup 2,
  contamination 0.001); validation = last window held out + 2 overlapping windows purged; patience 5;
  seeds 360 + run; `use_deterministic_algorithms(warn_only=True)`. For scoring test/live graphs,
  `build_graph(..., relations=<train_meta.json relations>)` reuses the training relation ids and drops
  edges whose relation was never seen in training (counted), instead of re-deriving ids from the data.
- Why: explainable code without the fork; avoid leakage between overlapping windows.
- Impact: [INFERENCE] centre/radius update and loss follow upstream PyGOD OCGNN, not verified against
  the fork; results may differ from the repo's.
- Limits: cannot claim to reproduce OCRGCN exactly.

## D6. Labels from injected attacks (`src/atomic-attack-pack/LABELING.md`)
- What: [PAPER] DARPA ground truth / analyst reports (with §7 label-provenance caveats). [OURS]
  scenarios self-log a JSONL; a node is malicious if it matches an indicator AND is active in the
  attack time range.
- Why: exact knowledge of what was run, no external ground truth available.
- Impact: weak oracle; benign processes with the same image name inside an attack window can be
  over-labelled.
- Limits: claims only "detection of injected ATT&CK techniques on our capture".

## D7. Environment
- What: [PAPER] §7.2.2 does not state a GraphDB version. [OURS] GraphDB 10.8.0 (Docker Compose);
  Python deps via uv (`uv.lock`), torch / torch-geometric, no PyGOD.
- Impact: none expected for the detector; GraphDB-dependent stages untested.

## D8. Live inference path (`src/live/`, `src/pipeline/score_window.py`)
- What: [PAPER] offline prediction on the whole test graph (§5.1.3), then Alg. 1 subgraphs with a
  subgraph anomaly-score filter (§5.2). [OURS] the VM streams raw SilkETW lines to the host; the host
  re-orders events by time (20 s lateness), runs the training converter code (same sha256, taken from
  `04-collector.ps1`), cuts tumbling 5-min windows aligned to the clock, and scores each window with the
  frozen train scaler, train relation list and per-type models. Windows that start before the capture
  are `partial` and not scored. Edges of the shipper's own PID are dropped. Relations unseen in training
  are dropped (counted). Subgraphs come from "Alg.1-lite": flagged nodes + bridges (unflagged nodes
  adjacent to ≥ 2 flagged nodes), connected components, **no subgraph-score filter**. No dedup across
  windows (stride = window by default). State is held in memory for one session.
- Why: live demo; same code path as offline so a replay of the training capture can be checked
  against the offline artefacts (`compare_replay.py`).
- Impact: events arriving later than the lateness are dropped (shown in the UI); processes started
  before a window have no Start edge inside it; without the SG-Det filter, expect more and noisier
  subgraphs than the paper (Table 4 "Without SG-Det" is the relevant comparison).
- Limits: demonstrates near-live node scoring on our own data. No detection-quality claim until
  windows are labelled (D6); no claim about Alg. 1 precision.
