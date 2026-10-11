#!/usr/bin/env python3
"""
compare_replay.py - "works correctly" check for the live path.

Replay the TRAINING capture through etwship.py --replay -> live_server.py, with the window grid aligned to
the training windows (--origin-ms = w0000 start_ms, --stride-min 2). Every live window that starts where a
training window starts must then contain the same edges, the same features and the same scores as the
offline pipeline (etw2nodlink -> etwwindow -> encode_to_features -> train_gnn_models). Any difference
is a bug in the live path (or a converter/version mismatch), not "model behaviour".

Usage (repo root)
    uv run python src/live/compare_replay.py \
        --session src/live/sessions/replay-x-20261009T145632Z \
        --experiment src/out/etw/experiments/20261009T145632Z \
        --features src/artifacts/features/self-dataset/features_model.csv \
        --train-scores src/artifacts/runs/exp01/train_scores.csv
"""
import argparse
import os
import sys
from collections import Counter

import numpy as np
import pandas as pd

FEATS = ["out_write", "in_write", "out_read", "in_read", "out_send", "in_send", "out_recv", "in_recv",
         "out_start", "in_start", "out_load", "in_load", "avg_idle_time", "max_idle_time", "min_idle_time"]


def strip(df, col):
    """'w0003_p123_456' -> ('w0003', 'p123_456')."""
    parts = df[col].str.split("_", n=1)
    return df.assign(window=parts.str[0], node=parts.str[1])


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--session", required=True, help="src/live/sessions/<session>")
    ap.add_argument("--experiment", required=True, help="src/out/etw/experiments/<id> (windows.csv, edges)")
    ap.add_argument("--features", required=True, help="offline features_model.csv of that experiment")
    ap.add_argument("--train-scores", help="train_scores.csv of the model the server used")
    ap.add_argument("--tol", type=float, default=1e-4)
    a = ap.parse_args()

    tw = pd.read_csv(os.path.join(a.experiment, "windows.csv"))
    lw = pd.read_csv(os.path.join(a.session, "windows.csv"))
    pairs = tw.merge(lw, on="start_ms", suffixes=("_train", "_live"))
    if pairs.empty:
        sys.exit("no live window starts where a training window starts: run live_server.py with "
                 f"--stride-min 2 --origin-ms {tw['start_ms'].min()}")
    print(f"[compare] {len(pairs)} of {len(tw)} training windows have a live twin\n")

    feats = strip(pd.read_csv(a.features), "node_uuid")
    scores = strip(pd.read_csv(a.train_scores), "node_uuid") if a.train_scores else None
    te_all = pd.read_csv(os.path.join(a.experiment, "SimulatedW10_train_edges.csv"),
                         dtype={"source-id": str, "destination-id": str})
    s, d = te_all["source-id"].str.split("_", n=1), te_all["destination-id"].str.split("_", n=1)
    train_edges = pd.DataFrame({"w": s.str[0], "src": s.str[1], "dst": d.str[1],
                                "etype": te_all["edge-type"], "ts": te_all["timestamp"]})

    rows, ok = [], True
    for p in pairs.itertuples():
        wdir = os.path.join(a.session, "windows", p.window_live)
        r = {"train": p.window_train, "live": p.window_live, "status": p.status,
             "edges_train": p.edges_train, "edges_live": p.edges_live}
        if p.status != "scored":
            rows.append(r)
            ok = False
            continue
        le = pd.read_csv(os.path.join(wdir, "edges.csv"), dtype={"source-id": str, "destination-id": str})
        te = train_edges[train_edges["w"] == p.window_train]
        # multisets: the same event pair can occur twice at the same ms (multi-edges are kept)
        a_ms = Counter(map(tuple, te[["src", "dst", "etype", "ts"]].astype(str).values))
        b_ms = Counter(map(tuple, le[["source-id", "destination-id", "edge-type", "timestamp"]].astype(str).values))
        r["edges_only_train"] = sum((a_ms - b_ms).values())
        r["edges_only_live"] = sum((b_ms - a_ms).values())

        lf = pd.read_csv(os.path.join(wdir, "features_model.csv")).set_index("node_uuid")
        tf = feats[feats["window"] == p.window_train].set_index("node")
        common = tf.index.intersection(lf.index)
        r["nodes_only_train"], r["nodes_only_live"] = len(tf.index.difference(lf.index)), len(lf.index.difference(tf.index))
        r["feat_max_abs_diff"] = float(np.abs(tf.loc[common, FEATS].to_numpy() - lf.loc[common, FEATS].to_numpy()).max()) \
            if len(common) else float("nan")
        if scores is not None:
            ls = pd.read_csv(os.path.join(wdir, "scores.csv")).set_index("node_uuid")["score"]
            ts = scores[scores["window"] == p.window_train].set_index("node")["score"]
            c = ts.index.intersection(ls.index)
            r["score_max_abs_diff"] = float(np.abs(ts[c] - ls[c]).max()) if len(c) else float("nan")
        good = (r["edges_only_train"] == 0 and r["edges_only_live"] == 0 and r["nodes_only_train"] == 0
                and r["nodes_only_live"] == 0 and r["feat_max_abs_diff"] <= a.tol
                and r.get("score_max_abs_diff", 0) <= a.tol)
        r["match"] = good
        ok &= good
        rows.append(r)

    out = pd.DataFrame(rows)
    print(out.to_string(index=False))
    out.to_csv(os.path.join(a.session, "compare_replay.csv"), index=False)
    print(f"\n[compare] {'ALL MATCH' if ok else 'MISMATCH'} (written {a.session}/compare_replay.csv)")
    if not ok:
        print("  edges differ  -> converter state/order or dos_map; check status 'late events dropped' (raise --lateness-s)\n"
              "  features only -> scaler / ts unit / rounding\n"
              "  scores only   -> relation list or model/run mismatch")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
