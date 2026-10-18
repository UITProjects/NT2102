#!/usr/bin/env python3
"""
train_gnn_models.py - train one OCRGCN per node type on benign data (simplified from upstream
OCR-APT/src/train_gnn_models.py; [AUTHOR CODE] l.N refers to that file).

    benign edges CSV (NODLINK schema) + features_model.csv (from encode_to_features.py)
    -> one heterogeneous graph with inverse relations
    -> one OCRGCN per node type (RGCN encoder + one-class hypersphere loss)
    -> threshold = training-score quantile at (1 - contamination)
    -> early stop on benign-validation TNR
    -> models + metadata for test / live-window scoring (score_window.py)

Own re-implementation, not the authors' PyGOD fork; loss/threshold follow upstream PyGOD OCGNN
([INFERENCE]): docs/DEVIATIONS.md D5. No GraphDB, baselines or test metrics here.

Usage (from the repo root)
    uv run python src/pipeline/train_gnn_models.py --selftest
    uv run python src/pipeline/train_gnn_models.py \
        --edges src/out/etw/experiments/<id>/SimulatedW10_train_edges.csv \
        --features src/artifacts/features/self-dataset/features_model.csv \
        --scaler src/artifacts/features/self-dataset/feature_scaler.json --out-dir src/artifacts/runs/exp01
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time

import numpy as np
import pandas as pd
import torch
import torch.nn.functional as F
from torch import nn
from torch_geometric.nn import RGCNConv
from torch_geometric.seed import seed_everything

EDGE_COLS = ["source-id", "source-type", "destination-id", "destination-type", "edge-type", "timestamp"]


# --------------------------------------------------------------------------- data
def normalise_edges(df: pd.DataFrame) -> pd.DataFrame:
    """Same normalisation as encode_to_features.py, so relation names match feature names."""
    df = df.dropna(subset=EDGE_COLS).copy()
    df["source-id"] = df["source-id"].astype(str)
    df["destination-id"] = df["destination-id"].astype(str)
    df["source-type"] = df["source-type"].astype(str).str.strip().str.lower()
    df["destination-type"] = df["destination-type"].astype(str).str.strip().str.lower()
    # [AUTHOR CODE] transform_to_RDF.py l.90 keeps text after the last "/"; encode_to_PyG strips EVENT_ and lower-cases
    df["edge-type"] = (df["edge-type"].astype(str).str.split("/").str[-1]
                       .str.replace("EVENT_", "", regex=False).str.lower())
    return df


def build_graph(edges: pd.DataFrame, relations: list[str] | None = None):
    """Pooled windows -> one typed graph.

    relations  None for training (derived from the data). For scoring (test/live windows) pass the
               training list from train_meta.json: relation ids must index the same W_r the model
               learned. Edges whose triple is not in that list are dropped (count printed).

    Returns
        nodes      DataFrame [node_uuid, node_type], row i = global node index i      (N rows)
        edge_index LongTensor (2, 2E)  forward edges then inverse edges
        edge_type  LongTensor (2E,)    relation id in [0, R)
        relations  list[str] of length R, "src_type|rel|dst_type"
    """
    if relations is not None:
        fixed_fwd = [r for r in relations if "|inv_" not in r]
        key = edges["source-type"] + "|" + edges["edge-type"] + "|" + edges["destination-type"]
        unknown = ~key.isin(fixed_fwd)
        if unknown.any():
            print(f"[WARN] dropping {int(unknown.sum())} edges with relations unseen in training: "
                  f"{key[unknown].value_counts().head(5).to_dict()}")
            edges = edges[~unknown]
    # node table: a node must have exactly one type
    src = edges[["source-id", "source-type"]].set_axis(["node_uuid", "node_type"], axis=1)
    dst = edges[["destination-id", "destination-type"]].set_axis(["node_uuid", "node_type"], axis=1)
    nodes = pd.concat([src, dst]).drop_duplicates()
    clash = nodes["node_uuid"].duplicated(keep=False)
    if clash.any():
        sys.exit(f"[ERROR] {nodes.loc[clash, 'node_uuid'].nunique()} node ids have >1 type, e.g. "
                 f"{nodes.loc[clash].head(4).to_dict('records')}")
    nodes = nodes.sort_values(["node_type", "node_uuid"]).reset_index(drop=True)  # deterministic order
    idx = pd.Series(np.arange(len(nodes)), index=nodes["node_uuid"])

    # [AUTHOR CODE] relations are keyed by (src_type, edge_type, dst_type) triples (encode_to_PyG.py l.463),
    # and train_gnn_models.py l.519-522 adds an inverse relation "inv_<rel>" for every triple.
    # Multi-edges are kept (one edge per event), as in encode_to_PyG.py l.463-470 (no dedup there).
    key = edges["source-type"] + "|" + edges["edge-type"] + "|" + edges["destination-type"]
    if relations is None:
        fwd = sorted(key.unique())
        inv = [f"{d}|inv_{r}|{s}" for s, r, d in (k.split("|") for k in fwd)]
        relations = fwd + inv
    else:
        fwd = [r for r in relations if "|inv_" not in r]   # training order: fwd then inv (see below)
    rel_id = {k: i for i, k in enumerate(fwd)}

    s = torch.as_tensor(idx[edges["source-id"]].to_numpy(), dtype=torch.long)       # (E,)
    d = torch.as_tensor(idx[edges["destination-id"]].to_numpy(), dtype=torch.long)  # (E,)
    t = torch.as_tensor(key.map(rel_id).to_numpy(), dtype=torch.long)               # (E,)
    edge_index = torch.cat([torch.stack([s, d]), torch.stack([d, s])], dim=1)       # (2, 2E)
    edge_type = torch.cat([t, t + len(fwd)])                                         # (2E,)
    return nodes, edge_index, edge_type, relations


def load_features(path: str, nodes: pd.DataFrame, scaler_path: str | None):
    """features_model.csv -> x (N, F) aligned with the global node order."""
    feat = pd.read_csv(path, index_col=0)
    feat.index = feat.index.astype(str)
    if scaler_path:
        cols = json.load(open(scaler_path))["feature_order"]
    else:
        cols = [c for c in feat.columns if pd.api.types.is_numeric_dtype(feat[c])]
    missing_cols = [c for c in cols if c not in feat.columns]
    if missing_cols:
        sys.exit(f"[ERROR] feature columns missing from {path}: {missing_cols}")
    x = feat[cols].reindex(nodes["node_uuid"])
    n_missing = int(x.isna().any(axis=1).sum())
    if n_missing:
        print(f"[WARN] {n_missing} graph nodes have no feature row -> zeros. "
              f"Expected 0 if features and edges come from the same CSV.")
    x = torch.as_tensor(x.fillna(0.0).to_numpy(), dtype=torch.float32)  # (N, F)
    return x, cols


def make_split(nodes: pd.DataFrame, a) -> np.ndarray:
    """Per node: 'train' | 'val' | 'purged'.

    [AUTHOR CODE] encode_to_PyG.py l.102 (--training-valid): random 15 % of training nodes -> validation.
    [OURS] default is a split by window: nodes are window-prefixed, so a window is its own
    connected component and message passing never crosses windows. Holding out whole windows
    avoids leakage through shared neighbours; purging the windows that overlap the held-out one
    in time avoids leakage through near-duplicate behaviour.
    Overlapping predecessors of a window of length L, stride S: ceil(L/S) - 1 (5 min / 2 min -> 2).
    """
    split = np.full(len(nodes), "train", dtype=object)
    if a.val_split == "random":
        rng = np.random.default_rng(1)  # repo uses random_state=1
        for _, g in nodes.groupby("node_type"):
            pick = rng.choice(g.index.to_numpy(), size=max(1, int(round(a.val_frac * len(g)))), replace=False)
            split[pick] = "val"
        return split
    win = nodes["node_uuid"].str.split(a.window_sep, n=1).str[0]
    wins = sorted(win.unique())
    if len(wins) < a.val_windows + a.purge_windows + 1:
        sys.exit(f"[ERROR] only {len(wins)} windows (prefixes like {wins[:3]}); need "
                 f"{a.val_windows + a.purge_windows + 1}. Use --val-split random for non-windowed data.")
    val_w = set(wins[-a.val_windows:])
    purge_w = set(wins[-(a.val_windows + a.purge_windows):-a.val_windows]) if a.purge_windows else set()
    split[win.isin(val_w).to_numpy()] = "val"
    split[win.isin(purge_w).to_numpy()] = "purged"
    print(f"[SPLIT] windows: train={len(wins) - len(val_w) - len(purge_w)} "
          f"purged={sorted(purge_w)} val={sorted(val_w)}")
    return split


# --------------------------------------------------------------------------- model
class OCRGCN(nn.Module):
    """RGCN encoder + Deep-SVDD style hypersphere (center c, radius r).

    [PAPER] §5.1.1 RGCN layers aggregate 1-hop neighbours per edge type; final embeddings go to
    a one-class objective; score = distance to the hypersphere center; above radius -> anomalous.
    [INFERENCE] c and r are not trainable parameters: they are re-estimated during the first
    `warmup` epochs and frozen afterwards (upstream PyGOD OCGNNBase.loss_func).
    """

    def __init__(self, in_dim, hid_dim, num_layers, num_relations, dropout=0.0, beta=0.5, eps=1e-3):
        super().__init__()
        dims = [in_dim] + [hid_dim] * num_layers
        # RGCNConv: h_i' = W_0 h_i + sum_r mean_{j in N_r(i)} W_r h_j   (aggr="mean" -> 1/c_{i,r})
        self.convs = nn.ModuleList(RGCNConv(dims[k], dims[k + 1], num_relations) for k in range(num_layers))
        self.dropout, self.beta, self.eps = dropout, beta, eps
        self.register_buffer("c", torch.zeros(hid_dim))  # (H,)  saved in state_dict
        self.register_buffer("r", torch.tensor(0.0))     # ()    saved in state_dict

    def forward(self, x, edge_index, edge_type):
        h = x                                                  # (N, F)
        for k, conv in enumerate(self.convs):
            h = conv(h, edge_index, edge_type)                 # (N, H)
            if k < len(self.convs) - 1:                        # no activation on the last layer
                h = F.dropout(F.relu(h), p=self.dropout, training=self.training)
        return h                                               # (N, H)

    def score(self, emb):
        """emb (M, H) -> anomaly score (M,) = ||emb - c||^2 - r^2. Positive = outside the sphere."""
        return ((emb - self.c) ** 2).sum(dim=1) - self.r ** 2

    def loss(self, emb):
        """L = r^2 + (1/beta) * mean(relu(||z - c||^2 - r^2)).  emb (M, H) -> scalar.
        Only nodes outside the sphere get a gradient; they are pulled towards c."""
        return self.r ** 2 + torch.relu(self.score(emb)).mean() / self.beta

    @torch.no_grad()
    def update_center_radius(self, emb):
        """[INFERENCE] PyGOD warmup step: c = mean embedding, r = (1-beta) quantile of distances,
        so with beta = 0.5 half of the training nodes start outside the sphere."""
        dist = ((emb - self.c) ** 2).sum(dim=1)
        self.r.copy_(torch.quantile(torch.sqrt(dist), 1 - self.beta))
        c = emb.mean(dim=0)
        # push near-zero coordinates away from 0 (Deep SVDD trick against a trivial all-zero solution)
        c[(c.abs() < self.eps) & (c < 0)] = -self.eps
        c[(c.abs() < self.eps) & (c >= 0)] = self.eps
        self.c.copy_(c)


# --------------------------------------------------------------------------- training
def train_one_type(x, edge_index, edge_type, num_rel, train_idx, val_idx, a, seed):
    """Train the OCRGCN of one node type.

    [PAPER] §5.1.2 each model is trained on one node type but aggregates from all neighbour types:
    the encoder runs on the FULL graph, the loss uses only this type's training nodes.
    [AUTHOR CODE] same pattern: split_per_node_type() sets masks for the target type only (l.222-235).
    """
    seed_everything(seed)                                       # [AUTHOR CODE] l.632, once per node type
    model = OCRGCN(x.size(1), a.hidden, a.layers, num_rel, a.dropout, a.beta)
    opt = torch.optim.Adam(model.parameters(), lr=a.lr)         # [INFERENCE] PyGOD default: Adam, wd=0
    q = 1.0 - a.contamination
    best = {"tnr": -1.0}
    bad = 0
    t0 = time.time()
    for epoch in range(a.epochs):
        model.train()
        opt.zero_grad()
        emb = model(x, edge_index, edge_type)                   # (N, H)
        loss = model.loss(emb[train_idx])                       # scalar
        loss.backward()
        opt.step()
        if epoch < a.warmup:                                    # [AUTHOR CODE] --warmup 2 (l.45)
            model.update_center_radius(emb.detach()[train_idx])
            continue                                            # c moving -> no validation yet

        model.eval()
        with torch.no_grad():
            emb = model(x, edge_index, edge_type)
            s_tr, s_va = model.score(emb[train_idx]), model.score(emb[val_idx])
            thr = torch.quantile(s_tr, q)                       # [PAPER] §5.1.2 threshold from contamination
            tnr = (s_va <= thr).float().mean().item() if len(val_idx) else 1.0
        # [PAPER] §5.1.2 no malicious validation nodes -> stop when validation TNR declines.
        # [OURS] patience instead of stopping on the first decline; ties keep the later epoch.
        if tnr >= best["tnr"]:
            best = {"tnr": tnr, "epoch": epoch, "loss": loss.item(),
                    "state": {k: v.clone() for k, v in model.state_dict().items()}}
            bad = 0
        else:
            bad += 1
            if bad >= a.patience:
                break
        if a.verbose:
            print(f"    ep {epoch:3d} loss {loss.item():.5f} r {model.r.item():.4f} thr {thr.item():.5f} val_tnr {tnr:.4f}")

    if "state" not in best:                                     # epochs <= warmup
        best = {"tnr": float("nan"), "epoch": a.epochs - 1, "loss": loss.item(),
                "state": model.state_dict()}
    model.load_state_dict(best["state"])
    model.eval()
    with torch.no_grad():
        emb = model(x, edge_index, edge_type)
        scores = model.score(emb)                               # (N,) for every node; caller slices
        thr = torch.quantile(scores[train_idx], q).item()
    stats = {"best_epoch": best["epoch"], "loss": best["loss"], "threshold": thr,
             "radius": model.r.item(), "train_flag_rate": (scores[train_idx] > thr).float().mean().item(),
             "val_tnr": best["tnr"], "val_fpr": 1 - best["tnr"],
             # collapse check: ~0 means every node is mapped to the same point
             "emb_std": emb[train_idx].std(dim=0).mean().item(),
             "train_time_s": time.time() - t0}
    return model, scores, stats


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def run(a):
    torch.use_deterministic_algorithms(True, warn_only=True)    # [AUTHOR CODE] l.22 (warn_only: [OURS])
    edges = normalise_edges(pd.read_csv(a.edges, dtype={"source-id": str, "destination-id": str}))
    nodes, edge_index, edge_type, relations = build_graph(edges)
    x, feat_cols = load_features(a.features, nodes, a.scaler)
    split = make_split(nodes, a)
    R = len(relations)
    print(f"[GRAPH] N={len(nodes)} E={edge_index.size(1) // 2} (x2 with inverse) R={R} F={x.size(1)}")
    print(nodes.assign(split=split).groupby(["node_type", "split"]).size().unstack(fill_value=0))

    os.makedirs(os.path.join(a.out_dir, "models"), exist_ok=True)
    rows, final_scores = [], np.full(len(nodes), np.nan)
    final_pred = np.zeros(len(nodes), dtype=bool)
    for r in range(a.runs):
        seed = a.seed + r                                       # [OURS] repo draws random seeds after run 0 (l.741)
        print(f"\n=== run {r} seed {seed}")
        for ntype in sorted(nodes["node_type"].unique()):
            is_t = (nodes["node_type"] == ntype).to_numpy()
            tr = torch.as_tensor(np.where(is_t & (split == "train"))[0])
            va = torch.as_tensor(np.where(is_t & (split == "val"))[0])
            if len(tr) < 2:
                print(f"[SKIP] {ntype}: {len(tr)} training nodes")
                continue
            model, scores, st = train_one_type(x, edge_index, edge_type, R, tr, va, a, seed)
            rows.append({"run": r, "seed": seed, "node_type": ntype, "n_train": len(tr), "n_val": len(va), **st})
            print(f"  {ntype:<10} n_train={len(tr):<6} n_val={len(va):<6} best_ep={st['best_epoch']:<3} "
                  f"thr={st['threshold']:.5f} val_fpr={st['val_fpr']:.4f} emb_std={st['emb_std']:.4f}")
            if r == 0:                                          # [AUTHOR CODE] models saved from run 0 only (l.666)
                torch.save({"state_dict": model.state_dict(), "threshold": st["threshold"],
                            "in_dim": x.size(1), "hid_dim": a.hidden, "num_layers": a.layers,
                            "num_relations": R, "beta": a.beta, "contamination": a.contamination},
                           os.path.join(a.out_dir, "models", f"{ntype}.pt"))
                s = scores.numpy()
                final_scores[is_t] = s[is_t]
                final_pred[is_t] = s[is_t] > st["threshold"]

    summary = pd.DataFrame(rows)
    summary.to_csv(os.path.join(a.out_dir, "runs_summary.csv"), index=False)
    pd.DataFrame({"node_uuid": nodes["node_uuid"], "node_type": nodes["node_type"], "split": split,
                  "score": final_scores, "flagged": final_pred}
                 ).sort_values("score", ascending=False).to_csv(os.path.join(a.out_dir, "train_scores.csv"), index=False)
    meta = {"inputs": {p: {"path": os.path.abspath(p), "sha256": sha256(p)} for p in
                       [a.edges, a.features] + ([a.scaler] if a.scaler else [])},
            "relations": relations, "feature_order": feat_cols,
            "node_types": sorted(nodes["node_type"].unique()), "args": vars(a),
            "label_source": "none (benign-only training; validation = held-out benign nodes)"}
    json.dump(meta, open(os.path.join(a.out_dir, "train_meta.json"), "w"), indent=2)

    agg = summary.groupby("node_type")[["val_fpr", "train_flag_rate", "threshold", "best_epoch", "emb_std"]]
    print(f"\n=== {a.runs} run(s), mean ± std (label source: none, benign-only)")
    print((agg.mean().round(4).astype(str) + " ± " + agg.std(ddof=1).round(4).astype(str)).to_string())
    print(f"[DONE] {a.out_dir}/models/<type>.pt, runs_summary.csv, train_scores.csv, train_meta.json")
    return summary


# --------------------------------------------------------------------------- selftest
def selftest():
    """'It runs' + invariants on a synthetic windowed graph. Not evidence that it detects attacks."""
    import tempfile
    rng = np.random.default_rng(0)
    rows = []
    for w in range(8):                                  # 8 windows, like 20 min / 5 min / 2 min stride
        p = [f"w{w:04d}_p{i}" for i in range(20)]
        f = [f"w{w:04d}_f{i}" for i in range(40)]
        n = [f"w{w:04d}_n{i}" for i in range(6)]
        for _ in range(600):
            a, k = rng.choice(p), rng.random()
            if k < .4:   rows.append([a, "PROCESS", rng.choice(f), "FILE", "FileIO/Read"])
            elif k < .7: rows.append([a, "PROCESS", rng.choice(f), "FILE", "FileIO/Write"])
            elif k < .8: rows.append([a, "PROCESS", rng.choice(n), "FLOW", "TcpIp/Send"])
            elif k < .9: rows.append([a, "PROCESS", rng.choice(n), "FLOW", "TcpIp/Recv"])
            elif k < .97: rows.append([a, "PROCESS", rng.choice(f), "FILE", "Image/Load"])
            else:        rows.append([a, "PROCESS", rng.choice(p), "PROCESS", "Process/Start"])
    e = pd.DataFrame(rows, columns=EDGE_COLS[:5])
    e["timestamp"] = rng.integers(0, 300_000, len(e))
    ids = pd.unique(pd.concat([e["source-id"], e["destination-id"]]))
    feat = pd.DataFrame(rng.random((len(ids), 15)), index=pd.Index(ids, name="node_uuid"),
                        columns=[f"f{i}" for i in range(15)])
    with tempfile.TemporaryDirectory() as d:
        e.to_csv(f"{d}/e.csv", index=False)
        feat.to_csv(f"{d}/x.csv")
        a = parse_args(["--edges", f"{d}/e.csv", "--features", f"{d}/x.csv", "--out-dir", f"{d}/out",
                        "--epochs", "15", "--runs", "2"])
        s = run(a)
        m = torch.load(f"{d}/out/models/process.pt", weights_only=False)
        assert set(s["node_type"]) == {"process", "file", "flow"}, s["node_type"].unique()
        assert m["num_relations"] == 12, m["num_relations"]          # 6 fwd triples + 6 inverse
        assert m["state_dict"]["c"].shape == (32,)
        assert (s["train_flag_rate"] <= 0.05 + 1e-9).all()          # ~contamination, small-N rounding
        r0, r1 = s[s.run == 0], s[s.run == 1]
        assert not np.allclose(r0["threshold"].values, r1["threshold"].values), "seeds had no effect"
        a2 = parse_args(["--edges", f"{d}/e.csv", "--features", f"{d}/x.csv", "--out-dir", f"{d}/out2",
                         "--epochs", "15", "--runs", "1"])
        assert np.allclose(run(a2)["threshold"].values, r0["threshold"].values), "not reproducible"
    print("\n[SELFTEST OK] graph build, per-type training, thresholds, reproducibility")


# --------------------------------------------------------------------------- cli
def parse_args(argv=None):
    p = argparse.ArgumentParser(description="Simplified OCRGCN training (NT2102 POC)")
    p.add_argument("--selftest", action="store_true")
    p.add_argument("--edges", help="pooled SimulatedW10_train_edges.csv")
    p.add_argument("--features", help="features_model.csv from encode_to_features.py")
    p.add_argument("--scaler", default=None, help="feature_scaler.json (fixes feature column order)")
    p.add_argument("--out-dir", default=os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                                     "..", "artifacts", "runs", "ocrgcn"))
    # split
    p.add_argument("--val-split", choices=["window", "random"], default="window")
    p.add_argument("--window-sep", default="_", help="node_uuid = <window><sep><id>")
    p.add_argument("--val-windows", type=int, default=1)
    p.add_argument("--purge-windows", type=int, default=2, help="ceil(window/stride) - 1")
    p.add_argument("--val-frac", type=float, default=0.15, help="only for --val-split random")
    # model: [PAPER] §7.2.1 3 RGCN layers, 32-d, lr 0.005, beta 0.5; [AUTHOR CODE] warmup 2, epochs 100, dropout 0
    p.add_argument("--layers", type=int, default=3)
    p.add_argument("--hidden", type=int, default=32)
    p.add_argument("--lr", type=float, default=0.005)
    p.add_argument("--beta", type=float, default=0.5)
    p.add_argument("--warmup", type=int, default=2)
    p.add_argument("--epochs", type=int, default=100)
    p.add_argument("--dropout", type=float, default=0.0)
    p.add_argument("--contamination", type=float, default=0.001, help="[PAPER] Min_con when no labels")
    p.add_argument("--patience", type=int, default=5)
    p.add_argument("--runs", type=int, default=3)
    p.add_argument("--seed", type=int, default=360, help="[AUTHOR CODE] l.728")
    p.add_argument("--verbose", action="store_true")
    a = p.parse_args(argv)
    if not a.selftest and not (a.edges and a.features):
        p.error("--edges and --features are required (or use --selftest)")
    return a


if __name__ == "__main__":
    args = parse_args()
    selftest() if args.selftest else run(args)