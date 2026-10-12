#!/usr/bin/env python3
"""
score_window.py - score an edge CSV (test split, one live window, or the train file itself) with the
per-node-type OCRGCN models saved by train_gnn_models.py.

    edges (NODLINK schema) --encode_to_features.build_features(scaler = TRAIN scaler)--> x (N, 15)
                           --train_gnn_models.build_graph(relations = TRAIN relations)--> edge_index, edge_type
    per node type t:  emb = model_t(x, edge_index, edge_type)   (N, 32)
                      score = ||emb - c_t||^2 - r_t^2            (N,)  kept only for nodes of type t
                      flagged = score > tau_t                    tau_t = training quantile saved in <t>.pt

Both the scaler and the relation list MUST come from training: a refit scaler would rescale idle times
differently, and a re-derived relation list could give "process|write|file" a different id, i.e. a
different learned matrix W_r.

Usage (from the repo root)
    uv run python src/pipeline/score_window.py --selftest
    uv run python src/pipeline/score_window.py --run-dir src/artifacts/runs/exp01 \
        --edges src/out/etw/experiments/<id>/SimulatedW10_train_edges.csv \
        --scaler src/artifacts/features/self-dataset/feature_scaler.json \
        --out src/artifacts/runs/exp01/rescored.csv
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import dataclass, field

import numpy as np
import pandas as pd
import torch

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import alg1  # noqa: E402
import encode_to_features as enc  # noqa: E402
import train_gnn_models as tgm  # noqa: E402


@dataclass
class Run:
    run_dir: str
    relations: list[str]
    feature_order: list[str]
    models: dict = field(default_factory=dict)   # node_type (lower-case) -> OCRGCN (eval mode)
    tau: dict = field(default_factory=dict)      # node_type -> threshold
    score_range: dict = field(default_factory=dict)   # node_type -> (train min, train max), for Alg. 1 prob


def load_run(run_dir: str, scaler: dict, allow_collapsed: bool = False) -> Run:
    meta = json.load(open(os.path.join(run_dir, "train_meta.json")))
    if meta["feature_order"] != scaler["feature_order"]:
        sys.exit(f"[ERROR] feature order differs between {run_dir}/train_meta.json and the scaler")

    # A collapsed model maps every node to the same point -> every score identical -> useless.
    summ = os.path.join(run_dir, "runs_summary.csv")
    if os.path.exists(summ):
        s = pd.read_csv(summ)
        bad = s[(s["run"] == 0) & (s["emb_std"] < 1e-6)]
        if len(bad) and not allow_collapsed:
            sys.exit(f"[ERROR] collapsed model(s) in {run_dir}: {bad['node_type'].tolist()} have emb_std ~ 0. "
                     f"Retrain on matching edges/features (see docs/PROGRESS.md) or pass --allow-collapsed.")

    run = Run(run_dir, meta["relations"], meta["feature_order"])
    for ntype in meta["node_types"]:
        p = os.path.join(run_dir, "models", f"{ntype}.pt")
        if not os.path.exists(p):
            print(f"[WARN] no model for node type {ntype} ({p}); its nodes get no score")
            continue
        ck = torch.load(p, weights_only=False)
        m = tgm.OCRGCN(ck["in_dim"], ck["hid_dim"], ck["num_layers"], ck["num_relations"], beta=ck["beta"])
        m.load_state_dict(ck["state_dict"])        # also restores the buffers c (H,) and r ()
        m.eval()
        if ck["num_relations"] != len(run.relations):
            sys.exit(f"[ERROR] {p}: num_relations {ck['num_relations']} != {len(run.relations)} in train_meta.json")
        run.models[ntype], run.tau[ntype] = m, float(ck["threshold"])
    run.score_range = alg1.load_score_range(run_dir)
    return run


@torch.no_grad()
def score_edges(edges_raw: pd.DataFrame, run: Run, scaler: dict):
    """edges_raw: NODLINK columns, original casing (as written by etw2nodlink / etwwindow).

    Returns
        nodes  DataFrame [node_uuid, node_type, score, tau, margin, flagged]   (N rows; type lower-case)
        feats  DataFrame features_model rows (index node_uuid), same as encode_to_features writes
        info   dict with edge/node counts and dropped-relation count
    """
    # features: same code path as encode_to_features.py, with the frozen train min/max
    e_feat = enc.normalise_edges(edges_raw)
    _, feats, _ = enc.build_features(e_feat, scaler.get("ts_unit", "ms"),
                                     scaler.get("repo_rounding", True), scaler["idle_minmax"])
    # graph: same code path as training, with the frozen relation list
    e_graph = tgm.normalise_edges(edges_raw)
    nodes, edge_index, edge_type, _ = tgm.build_graph(e_graph, relations=run.relations)
    x = feats[run.feature_order].reindex(nodes["node_uuid"])
    n_missing = int(x.isna().any(axis=1).sum())          # only nodes that lost all edges to the relation filter
    x = torch.as_tensor(x.fillna(0.0).to_numpy(), dtype=torch.float32)   # (N, 15)

    score = np.full(len(nodes), np.nan)
    tau = np.full(len(nodes), np.nan)
    for ntype, model in run.models.items():
        is_t = (nodes["node_type"] == ntype).to_numpy()
        if not is_t.any():
            continue
        emb = model(x, edge_index, edge_type)              # (N, H): the encoder sees ALL node types
        s = model.score(emb).numpy()                       # (N,)
        score[is_t], tau[is_t] = s[is_t], run.tau[ntype]   # but only this type's scores are kept
    out = nodes.assign(score=score, tau=tau)
    out["margin"] = out["score"] - out["tau"]
    out["flagged"] = (out["score"] > out["tau"]).fillna(False)
    info = {"edges": int(edge_index.size(1) // 2), "nodes": int(len(nodes)),
            "edges_dropped_relation": int(len(e_graph) - edge_index.size(1) // 2),
            "nodes_without_features": n_missing,
            "flagged": int(out["flagged"].sum())}
    return out, feats, info


def load_scaler(path: str) -> dict:
    sc = json.load(open(path))
    if sc.get("refit") is False:
        print(f"[WARN] {path} was itself produced with --scaler (refit=false); make sure it is the TRAIN scaler")
    return sc


# --------------------------------------------------------------------------- selftest
def synthetic_edges(n_windows=6, seed=0) -> pd.DataFrame:
    """Windowed NODLINK-style edges with the real W10 node/edge vocabulary (epoch-ms timestamps)."""
    rng = np.random.default_rng(seed)
    t0 = 1_791_000_000_000
    rows = []
    for w in range(n_windows):
        p = [f"w{w:04d}_p{i}" for i in range(15)]
        f = [f"w{w:04d}_f{i}" for i in range(30)]
        n = [f"w{w:04d}_n{i}" for i in range(5)]
        for _ in range(400):
            a, k, t = rng.choice(p), rng.random(), t0 + w * 120_000 + int(rng.integers(0, 300_000))
            if k < .35:   rows.append([a, "PROCESS", rng.choice(f), "FILE", "Read", t])
            elif k < .6:  rows.append([a, "PROCESS", rng.choice(f), "FILE", "Write", t])
            elif k < .72: rows.append([a, "PROCESS", rng.choice(n), "NET", "Send", t])
            elif k < .84: rows.append([a, "PROCESS", rng.choice(n), "NET", "Recv", t])
            elif k < .96: rows.append([a, "PROCESS", rng.choice(f), "FILE", "Load", t])
            else:         rows.append([a, "PROCESS", rng.choice(p), "PROCESS", "Start", t])
    return pd.DataFrame(rows, columns=tgm.EDGE_COLS)


def selftest():
    """'It runs' + the scoring path reproduces the training scores on the training graph.
    Not evidence that anything detects attacks."""
    import tempfile
    e = synthetic_edges()
    with tempfile.TemporaryDirectory() as d:
        e.to_csv(f"{d}/edges.csv", index=False)
        _, feats, sc = enc.build_features(enc.normalise_edges(e))
        feats.to_csv(f"{d}/features_model.csv", index_label="node_uuid")
        json.dump({"feature_order": enc.ACTION_COLS + enc.IDLE_COLS, "idle_minmax": sc, "ts_unit": "ms",
                   "repo_rounding": True, "refit": True}, open(f"{d}/scaler.json", "w"))
        a = tgm.parse_args(["--edges", f"{d}/edges.csv", "--features", f"{d}/features_model.csv",
                            "--scaler", f"{d}/scaler.json", "--out-dir", f"{d}/run",
                            "--epochs", "12", "--runs", "1", "--purge-windows", "1"])
        tgm.run(a)

        scaler = load_scaler(f"{d}/scaler.json")
        run = load_run(f"{d}/run", scaler)
        assert set(run.models) == {"process", "file", "net"}, run.models.keys()
        assert set(run.score_range) == set(run.models), run.score_range
        nodes, feats2, info = score_edges(pd.read_csv(f"{d}/edges.csv", dtype=str).astype(
            {"timestamp": "int64"}), run, scaler)
        assert info["edges"] == len(e) and info["edges_dropped_relation"] == 0, info
        assert nodes["score"].notna().all() and set(nodes.columns) >= {"score", "tau", "flagged"}

        # same graph + same weights + eval mode -> identical scores to train_scores.csv
        ref = pd.read_csv(f"{d}/run/train_scores.csv").set_index("node_uuid")["score"]
        got = nodes.set_index("node_uuid")["score"].reindex(ref.index)
        diff = float(np.abs(got - ref).max())
        assert diff < 1e-4, f"rescored != train scores (max |diff| {diff})"

        # a relation unseen in training is dropped, not silently given a wrong id
        extra = pd.DataFrame([["w0000_f0", "FILE", "w0000_n0", "NET", "Send", 1_791_000_000_000]],
                             columns=tgm.EDGE_COLS)
        _, _, info2 = score_edges(pd.concat([e, extra], ignore_index=True), run, scaler)
        assert info2["edges_dropped_relation"] == 1, info2
    print(f"\n[SELFTEST OK] load_run, score_edges reproduces training scores (max |diff| {diff:.2e}), "
          f"unknown relations dropped")


# --------------------------------------------------------------------------- cli
def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--run-dir", help="output dir of train_gnn_models.py (models/, train_meta.json)")
    ap.add_argument("--edges", help="NODLINK edge CSV to score")
    ap.add_argument("--scaler", help="TRAIN feature_scaler.json")
    ap.add_argument("--out", help="scores CSV (default <run-dir>/scores_<edges name>.csv)")
    ap.add_argument("--allow-collapsed", action="store_true")
    a = ap.parse_args()
    if a.selftest:
        return selftest()
    if not (a.run_dir and a.edges and a.scaler):
        ap.error("--run-dir, --edges and --scaler are required (or --selftest)")

    scaler = load_scaler(a.scaler)
    run = load_run(a.run_dir, scaler, a.allow_collapsed)
    edges = pd.read_csv(a.edges, dtype={"source-id": str, "destination-id": str})
    nodes, _, info = score_edges(edges, run, scaler)
    out = a.out or os.path.join(a.run_dir, f"scores_{os.path.splitext(os.path.basename(a.edges))[0]}.csv")
    nodes.sort_values("score", ascending=False).to_csv(out, index=False)
    print(json.dumps(info, indent=1))
    print(nodes.groupby("node_type").agg(n=("score", "size"), flagged=("flagged", "sum"),
                                         flag_rate=("flagged", "mean"), tau=("tau", "first")).to_string())
    print(f"[DONE] {out}")


if __name__ == "__main__":
    main()
