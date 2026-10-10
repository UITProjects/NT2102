"""
encode_to_features.py - OCR-APT behavior features (§5.1.2) as plain CSV, for EDA.

Re-implements ONLY the feature step of the repo: encode_to_PyG.py::feature_engineering (l.131-245).
No RDF, no GraphDB, no PyG folders, no train/valid split. One edge CSV in, three files out.

Input   <edges.csv>  NODLINK format: source-id,source-type,destination-id,destination-type,edge-type,timestamp
                     (our etwwindow output: epoch ms, window-prefixed node IDs like "w0007_...")
Output  features_raw.csv     human-readable: raw counts + idle gaps in float seconds + n_events  (do EDA here)
        features_model.csv   what the GNN would see: L2-normalised actions + min-max idle stats  (15 features)
        feature_scaler.json  column order + min/max fitted on THIS file (reuse for test/live windows)

Usage   python encode_to_features.py --edges SimulatedW10_train_edges.csv --out-dir features/
        python encode_to_features.py --selftest          # hand-computed toy graph, must print "SELFTEST OK"
"""
import argparse
import json
import os
import re
import sys

import numpy as np
import pandas as pd

# [REPO] database_config.py l.85-86 + l.91-94: action vocabulary for SimulatedW10, in this order,
# each expanded to out_<a>, in_<a>. Order matters: it is the column order of the feature tensor.
ACTIONS = ["write", "read", "send", "recv", "start", "load"]
ACTION_COLS = [f"{d}_{a}" for a in ACTIONS for d in ("out", "in")]          # 12 columns
# [REPO] encode_to_PyG.py l.178-180: idle columns are inserted in this order (avg, max, min).
IDLE_COLS = ["avg_idle_time", "max_idle_time", "min_idle_time"]             # 3 columns
TS_DIVISOR_TO_SECONDS = {"ns": 1e9, "us": 1e6, "ms": 1e3, "s": 1.0}
# [REPO] l.167-168: SimulatedW10 gaps are divided by 1000 -> the repo assumes epoch MILLISECONDS.
REQUIRED = ["source-id", "source-type", "destination-id", "destination-type", "edge-type", "timestamp"]
WINDOW_RE = re.compile(r"^(w\d+)_")                                          # etwwindow prefix "w0007_"


def load_edges(path: str) -> pd.DataFrame:
    df = pd.read_csv(path, dtype={"source-id": str, "destination-id": str})
    missing = [c for c in REQUIRED if c not in df.columns]
    if missing:
        sys.exit(f"[ERROR] {path} is missing columns {missing}; got {list(df.columns)}")
    return normalise_edges(df)


def normalise_edges(df: pd.DataFrame) -> pd.DataFrame:
    df = df.dropna(subset=REQUIRED).copy()
    # [REPO] transform_to_RDF.py l.89-90 keeps the part after the last "/" for SimulatedW10,
    # then encode_to_PyG.py l.142 strips "EVENT_" and lower-cases.
    df["edge-type"] = (df["edge-type"].astype(str).str.split("/").str[-1]
                       .str.replace("EVENT_", "", regex=False).str.lower())
    df["timestamp"] = pd.to_numeric(df["timestamp"], errors="raise").astype("int64")
    return df


def check_vocabulary(edges: pd.DataFrame) -> None:
    # [REPO] an unknown edge type raises KeyError at l.147 (dict has no such key). We fail loudly too,
    # but with a readable list, because silently dropping edges would bias every count.
    unknown = edges.loc[~edges["edge-type"].isin(ACTIONS), "edge-type"].value_counts()
    if len(unknown):
        sys.exit(f"[ERROR] edge types outside the 6-action vocabulary:\n{unknown.to_string()}")


def node_table(edges: pd.DataFrame) -> pd.DataFrame:
    """One row per node, indexed by node_uuid: node_type, window. Shape (N, 2)."""
    src = edges[["source-id", "source-type"]].set_axis(["node_uuid", "node_type"], axis=1)
    dst = edges[["destination-id", "destination-type"]].set_axis(["node_uuid", "node_type"], axis=1)
    nodes = pd.concat([src, dst]).drop_duplicates()
    clash = nodes["node_uuid"].duplicated(keep=False)
    if clash.any():  # same ID seen with two types -> the ID is not a real identity (PID reuse? converter bug?)
        print(f"[WARN] {nodes.loc[clash, 'node_uuid'].nunique()} node IDs carry >1 node_type; keeping the first")
        nodes = nodes.drop_duplicates("node_uuid")
    nodes["window"] = nodes["node_uuid"].str.extract(WINDOW_RE, expand=False).fillna("")
    return nodes.set_index("node_uuid").sort_index()


def action_counts(edges: pd.DataFrame, index: pd.Index) -> pd.DataFrame:
    """[REPO] l.147-148: each edge adds +1 to out_<type> of its source and +1 to in_<type> of its
    destination. Vectorised here with groupby instead of the repo's row loop. Shape (N, 12), ints."""
    out = edges.groupby(["source-id", "edge-type"]).size().unstack(fill_value=0).add_prefix("out_")
    inn = edges.groupby(["destination-id", "edge-type"]).size().unstack(fill_value=0).add_prefix("in_")
    counts = out.join(inn, how="outer")
    # fixed 12-column schema in the repo's order; absent actions become 0
    return counts.reindex(index=index, columns=ACTION_COLS, fill_value=0).fillna(0).astype("int64")


def idle_stats(edges: pd.DataFrame, index: pd.Index, ts_unit: str) -> pd.DataFrame:
    """[REPO] l.150-157 + l.159-201 with --get-idle-time and WITHOUT --get-cumulative-active-time
    (so EVERY gap counts as idle, l.175). [PAPER] §5.1.2: min/max/avg duration between a node's actions.
    Returns float seconds, shape (N, 5): avg/max/min idle + n_events + active_span_s."""
    # every edge timestamp belongs to BOTH endpoints (a self-loop therefore contributes a 0 s gap, as in the repo)
    ts = pd.concat([
        edges[["source-id", "timestamp"]].set_axis(["node_uuid", "ts"], axis=1),
        edges[["destination-id", "timestamp"]].set_axis(["node_uuid", "ts"], axis=1),
    ]).sort_values(["node_uuid", "ts"])                                  # (2E, 2)
    ts["gap_s"] = ts.groupby("node_uuid")["ts"].diff() / TS_DIVISOR_TO_SECONDS[ts_unit]  # first event -> NaN
    g = ts.groupby("node_uuid")
    stats = pd.DataFrame({
        "avg_idle_time": g["gap_s"].mean(),
        "max_idle_time": g["gap_s"].max(),
        "min_idle_time": g["gap_s"].min(),
        "n_events": g.size(),
        # EDA helper, NOT a model feature: first-to-last event of this node
        "active_span_s": (g["ts"].max() - g["ts"].min()) / TS_DIVISOR_TO_SECONDS[ts_unit],
    })
    # [REPO] l.192-196: nodes with a single event get 0 for all three (no gap exists).
    # Note: this makes "seen once" look identical to "always busy" - worth checking in EDA via n_events.
    return stats.reindex(index).fillna({c: 0.0 for c in IDLE_COLS})


def l2_normalise(counts: pd.DataFrame) -> pd.DataFrame:
    """[PAPER] §5.1.2 / [REPO] l.233 sklearn normalize(norm='l2', axis=1):
    x_i <- x_i / sqrt(sum_j x_j^2), per node (row). Every node has >=1 edge, so the norm is never 0."""
    norm = np.sqrt((counts.astype(float) ** 2).sum(axis=1))             # (N,)
    return counts.div(norm.replace(0, 1.0), axis=0)                      # (N, 12), each row has L2 norm 1


def fit_minmax(idle: pd.DataFrame) -> dict:
    return {c: {"min": float(idle[c].min()), "max": float(idle[c].max())} for c in IDLE_COLS}


def apply_minmax(idle: pd.DataFrame, params: dict) -> pd.DataFrame:
    """[REPO] l.236-237 MinMaxScaler: (x - min) / (max - min). Zero range -> 0, like sklearn.
    Values outside the fitted range (test/live data) fall outside [0, 1]; that is expected."""
    out = pd.DataFrame(index=idle.index)
    for c in IDLE_COLS:
        lo, hi = params[c]["min"], params[c]["max"]
        out[c] = (idle[c] - lo) / (hi - lo) if hi > lo else 0.0
    return out


def build_features(edges, ts_unit="ms", repo_rounding=True, scaler=None):
    check_vocabulary(edges)
    nodes = node_table(edges)                                            # (N, 2)
    counts = action_counts(edges, nodes.index)                           # (N, 12)
    idle = idle_stats(edges, nodes.index, ts_unit)                       # (N, 5) float seconds

    raw = nodes.join(counts).join(idle)                                  # (N, 2+12+5)

    idle_for_model = idle[IDLE_COLS].copy()
    if repo_rounding:  # [REPO] l.178-180 int(round(...)): sub-second gaps collapse to 0 s
        idle_for_model = idle_for_model.round().astype("int64")
    scaler = scaler or fit_minmax(idle_for_model)
    model = nodes.join(l2_normalise(counts)).join(apply_minmax(idle_for_model, scaler))
    return raw, model, scaler


def sanity_checks(edges, raw, model):
    E = len(edges)
    out_sum, in_sum = int(raw[[c for c in ACTION_COLS if c.startswith("out_")]].values.sum()), \
                      int(raw[[c for c in ACTION_COLS if c.startswith("in_")]].values.sum())
    assert out_sum == E and in_sum == E, f"count mismatch: edges={E} out={out_sum} in={in_sum}"
    norms = np.sqrt((model[ACTION_COLS] ** 2).sum(axis=1))
    assert np.allclose(norms, 1.0), "some action rows are not unit-L2"
    print(f"[CHECK] every edge counted once as out and once as in: {E} = {out_sum} = {in_sum}")
    print(f"[CHECK] all {len(model)} action vectors have L2 norm 1")


def summarise(edges, raw, ts_unit):
    span = (edges["timestamp"].max() - edges["timestamp"].min()) / TS_DIVISOR_TO_SECONDS[ts_unit]
    print(f"[INFO] edges={len(edges)}  nodes={len(raw)}  windows={raw['window'].replace('', np.nan).nunique()}")
    print(f"[INFO] timestamp span = {span:,.1f} s (if --ts-unit is right, this should match your capture length)")
    print(f"[INFO] nodes per type:\n{raw['node_type'].value_counts().to_string()}")
    zero_cols = [c for c in ACTION_COLS if raw[c].sum() == 0]
    if zero_cols:  # [REPO] l.204 would DROP these columns -> feature dimension would depend on the data
        print(f"[INFO] all-zero action columns (kept here, dropped by the repo): {zero_cols}")
    single = (raw["n_events"] == 1).mean()
    print(f"[INFO] {single:.1%} of nodes have a single event -> idle stats forced to 0")


def selftest():
    # Toy graph, timestamps in ms. Expected values computed by hand:
    #   p1: out_write=2, out_read=1, out_send=1 -> L2 norm sqrt(6); gaps 1,3,6 s -> min 1, max 6, avg 3.33
    #   f1: in_write=2, in_read=1;               gaps 1,9 s     -> min 1, max 9, avg 5
    #   n1: in_send=1;                           single event   -> 0,0,0
    edges = pd.DataFrame(
        [["p1", "PROCESS", "f1", "FILE", "FileIO/Write", 0],
         ["p1", "PROCESS", "f1", "FILE", "FileIO/Write", 1000],
         ["p1", "PROCESS", "n1", "NET", "TcpIp/Send", 4000],
         ["p1", "PROCESS", "f1", "FILE", "FileIO/Read", 10000]], columns=REQUIRED)
    edges = normalise_edges(edges)
    raw, model, _ = build_features(edges, "ms", repo_rounding=False)
    sanity_checks(edges, raw, model)
    r = raw.loc["p1"]
    assert (r.out_write, r.out_read, r.out_send) == (2, 1, 1)
    assert np.isclose(model.loc["p1", "out_write"], 2 / np.sqrt(6))
    assert (r.min_idle_time, r.max_idle_time) == (1, 6) and np.isclose(r.avg_idle_time, 10 / 3)
    assert (raw.loc["f1", "min_idle_time"], raw.loc["f1", "max_idle_time"], raw.loc["f1", "avg_idle_time"]) == (1, 9, 5)
    assert raw.loc["n1", IDLE_COLS].sum() == 0 and raw.loc["n1", "n_events"] == 1
    assert np.isclose(model.loc["f1", "max_idle_time"], 1.0)              # max 9 is the dataset max
    print("SELFTEST OK")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--edges", help="NODLINK-format *_edges.csv")
    ap.add_argument("--out-dir", default="features")
    ap.add_argument("--ts-unit", choices=TS_DIVISOR_TO_SECONDS, default="ms", help="unit of the timestamp column")
    ap.add_argument("--no-repo-rounding", action="store_true",
                    help="keep sub-second idle gaps in the model features (repo rounds to whole seconds)")
    ap.add_argument("--scaler", help="reuse min/max from a previous feature_scaler.json (test/live data)")
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()
    if a.selftest:
        return selftest()
    if not a.edges:
        ap.error("--edges is required (or use --selftest)")

    edges = load_edges(a.edges)
    scaler = json.load(open(a.scaler))["idle_minmax"] if a.scaler else None
    raw, model, scaler = build_features(edges, a.ts_unit, not a.no_repo_rounding, scaler)
    sanity_checks(edges, raw, model)
    summarise(edges, raw, a.ts_unit)

    os.makedirs(a.out_dir, exist_ok=True)
    raw.to_csv(os.path.join(a.out_dir, "features_raw.csv"), index_label="node_uuid")
    model.to_csv(os.path.join(a.out_dir, "features_model.csv"), index_label="node_uuid", float_format="%.6f")
    with open(os.path.join(a.out_dir, "feature_scaler.json"), "w") as f:
        json.dump({"source": os.path.abspath(a.edges), "ts_unit": a.ts_unit,
                   "repo_rounding": not a.no_repo_rounding, "feature_order": ACTION_COLS + IDLE_COLS,
                   "idle_minmax": scaler, "refit": a.scaler is None}, f, indent=2)
    print(f"[DONE] wrote features_raw.csv, features_model.csv, feature_scaler.json to {a.out_dir}/")


if __name__ == "__main__":
    main()