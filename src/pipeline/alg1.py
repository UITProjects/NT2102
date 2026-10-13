#!/usr/bin/env python3
"""
alg1.py - OCR-APT Algorithm 1 (anomalous subgraphs + subgraph-score filter, "SG-Det") on ONE scored window,
in memory (no GraphDB). [REPO] l.N = upstream detect_anomalous_subgraphs.py.

    A       = anomalous nodes (score > tau of their type)                       [PAPER] Alg. 1 input
    initSG  = every edge with >= 1 endpoint in A  (+ isolated A nodes)          Alg. 1 l.3-5   [REPO] l.238-293
    seeds   = top n_seed A nodes per type, not isolated in initSG,              Alg. 1 l.6-7   [REPO] l.705-722
              by prob desc, then type order net < process < file
    per seed: seed + anomalous neighbours, plus any neighbour leading to an     Alg. 1 l.8-11  [REPO] l.552-648
              unvisited anomalous node; >= min_nodes or dropped; nodes become
              "correlated" (correlated seeds are not skipped, as upstream)
    > max_e edges -> Louvain communities (>= min_nodes nodes, > 2 edges)        Alg. 1 l.12-17 [REPO] l.520-542
    drop identical subgraphs                                                    Alg. 1 l.19    [REPO] l.650-672
    sg_score = sum over ANOMALOUS nodes of                                      Alg. 1 l.21    [REPO] l.864-871
               prob = clamp((score - train_min_t) / (train_max_t - train_min_t), 0, 1)
    level    = Negligible / Minor / Moderate / Significant / Critical ([OURS] Moderate from MODERATE_FROM)
    kept     = level >= min_level (default Moderate, [REPO] l.61)               Alg. 1 l.22-24

Deviations ([OURS] level scale, no top-k fallback, `oversize`, --sg-min-score): docs/DEVIATIONS.md D8.

Usage (from the repo root)
    uv run python src/pipeline/alg1.py --selftest
    uv run python src/pipeline/alg1.py --run-dir src/artifacts/runs/exp01 \
        --session-dir src/live/sessions/<id>          # or --window-dir .../windows/<wid>
      -> <window>/subgraphs_alg1.{json,csv}, one summary row per window
"""
from __future__ import annotations

import argparse
import glob
import json
import math
import os
import sys

import networkx as nx
import pandas as pd

LEVELS = ["Negligible", "Minor", "Moderate", "Significant", "Critical"]
MODERATE_FROM = 30.0                                    # [OURS] live scale; [REPO] l.394 is > 10
TYPE_ORDER = {"net": 1, "process": 2, "file": 3}        # [REPO] node_type_order l.674


def load_score_range(run_dir: str) -> dict:
    """node_type -> (min, max) of the TRAINING scores of the saved model ([REPO] decision_score_).

    train_scores.csv is written from run 0, the run whose models are saved (train_gnn_models.py)."""
    p = os.path.join(run_dir, "train_scores.csv")
    if not os.path.exists(p):
        sys.exit(f"[ERROR] {p} missing: Alg. 1 needs the training score range per node type "
                 f"(re-run train_gnn_models.py)")
    s = pd.read_csv(p)
    s = s[s["split"] == "train"]
    return {t: (float(g["score"].min()), float(g["score"].max())) for t, g in s.groupby("node_type")}


def node_prob(nodes: pd.DataFrame, score_range: dict) -> pd.Series:
    """[REPO] PyGOD 'linear' probability: training min -> 0, training max -> 1, clamped to [0, 1]."""
    lo = nodes["node_type"].map(lambda t: score_range.get(t, (math.nan, math.nan))[0])
    hi = nodes["node_type"].map(lambda t: score_range.get(t, (math.nan, math.nan))[1])
    return ((nodes["score"] - lo) / (hi - lo)).clip(0, 1).fillna(0.0)


def classify_level(score: float, moderate_from: float = MODERATE_FROM) -> str:
    """[REPO] l.394 bounds (upper bound inclusive) except Minor/Moderate: Moderate from `moderate_from` (inclusive)."""
    if score <= 1:
        return "Negligible"
    if score < moderate_from:
        return "Minor"
    for bound, name in ((100, "Moderate"), (1000, "Significant")):
        if score <= bound:
            return name
    return "Critical"


def construct(nodes: pd.DataFrame, edges: pd.DataFrame, attrs: dict, score_range: dict, n_seed: int = 15,
              min_nodes: int = 3, max_edges: int = 5000, min_level: str = "Moderate",
              min_score: float | None = None, moderate_from: float = MODERATE_FROM) -> tuple[list, dict]:
    """nodes: [node_uuid, node_type, score, tau, margin, flagged] (score_window.score_edges)
    edges: normalised edge rows (train_gnn_models.normalise_edges), one row per event.
    Returns (subgraphs, stats); subgraphs sorted kept first, then by sg_score."""
    nodes = nodes.copy()
    nodes["prob"] = node_prob(nodes, score_range)
    A = set(nodes.loc[nodes["flagged"], "node_uuid"])
    stats = {"anomalous": len(A), "initsg_edges": 0, "isolated": 0, "seeds": 0, "subgraphs_built": 0,
             "duplicates_removed": 0, "left_out": len(A)}
    if not A:
        return [], stats

    # ---- Alg. 1 l.3-5: initSG = edges touching an anomalous node (edges of relation-dropped nodes excluded)
    known = set(nodes["node_uuid"])
    e = edges[edges["source-id"].isin(known) & edges["destination-id"].isin(known)]
    e = e[e["source-id"].isin(A) | e["destination-id"].isin(A)].reset_index(drop=True)
    stats["initsg_edges"] = len(e)
    succ, pred = {}, {}                                 # node -> set of out / in neighbours in initSG
    for a, b in zip(e["source-id"], e["destination-id"]):
        succ.setdefault(a, set()).add(b)
        pred.setdefault(b, set()).add(a)

    def nbrs(n):
        return succ.get(n, set()) | pred.get(n, set())

    isolated = {n for n in A if not (nbrs(n) - {n})}
    stats["isolated"] = len(isolated)
    init_corr = A - isolated

    # ---- Alg. 1 l.6-7: seeds
    info = nodes.set_index("node_uuid")
    cand = nodes[nodes["node_uuid"].isin(init_corr)]
    seeds = pd.concat([g.sort_values(["score", "node_uuid"], ascending=[False, True]).head(n_seed)
                       for _, g in cand.groupby("node_type")]) if len(cand) else cand
    seeds = seeds.assign(order=seeds["node_type"].map(lambda t: TYPE_ORDER.get(t, 4)))
    seeds = seeds.sort_values(["prob", "order", "node_uuid"], ascending=[False, True, True])
    stats["seeds"] = len(seeds)

    # ---- Alg. 1 l.8-18: one candidate subgraph per seed
    correlated, unvisited, built = set(), set(init_corr), []

    def induced(members):
        return e[e["source-id"].isin(members) & e["destination-id"].isin(members)]

    def partition(members, sub_e):
        """[REPO] l.520-542: Louvain on the directed multigraph, seed 0."""
        G = nx.MultiDiGraph()
        G.add_nodes_from(members)
        G.add_edges_from(zip(sub_e["source-id"], sub_e["destination-id"]))
        parts = []
        for comm in nx.community.louvain_communities(G, resolution=1, seed=0):
            ce = induced(comm)
            if len(comm) >= min_nodes and len(ce) > 2:
                parts.append((set(comm), ce))
        return parts or [(set(members), sub_e)]

    for seed in seeds["node_uuid"]:
        connected = nbrs(seed) - {seed}
        sub, visited = {seed}, {seed}
        sub |= (connected & A) - correlated
        visited |= sub
        for conn in sorted(connected):                  # 1 hop forward and backwards from the seed's neighbours
            reach = (nbrs(conn) & A) - correlated - visited
            if reach:                                   # keep only paths that lead to unvisited anomalous nodes
                sub |= reach
                sub.add(conn)
                visited |= sub
        if len(sub) < min_nodes:
            continue
        sub_e = induced(sub)
        parts = partition(sub, sub_e) if (max_edges and len(sub_e) > max_edges) else [(sub, sub_e)]
        for members, pe in parts:
            built.append((seed, members, pe, bool(max_edges) and len(pe) > max_edges))
            correlated |= members
        unvisited -= correlated
        if not unvisited:
            break
    stats["subgraphs_built"] = len(built)

    # ---- Alg. 1 l.19: identical subgraphs
    uniq = dedup_identical(built)
    stats["duplicates_removed"] = len(built) - len(uniq)

    # ---- Alg. 1 l.20-24: score, level, filter
    min_rank = LEVELS.index(min_level)
    out, in_any = [], set()
    for i, (seed, members, pe, oversize) in enumerate(uniq):
        anom = members & A
        in_any |= anom
        sg_score = float(sum(info.loc[n, "prob"] for n in anom))
        level = classify_level(sg_score, moderate_from)
        kept = (sg_score > min_score) if min_score is not None else LEVELS.index(level) >= min_rank
        nlist = []
        for n in members:
            r = info.loc[n]
            nlist.append({"id": n, "type": r["node_type"], "attr": attrs.get(n, ""), "score": _f(r["score"]),
                          "tau": _f(r["tau"]), "margin": _f(r["margin"]), "prob": _f(r["prob"]),
                          "role": "anomalous" if n in A else "connector"})
        nlist.sort(key=lambda x: (x["role"] != "anomalous", -(x["prob"] or 0), -(x["margin"] or 0)))
        agg = (pe.groupby(["source-id", "destination-id", "edge-type"])["timestamp"]
               .agg(["size", "min", "max"]).reset_index()) if len(pe) else pd.DataFrame()
        elist = [{"src": r["source-id"], "dst": r["destination-id"], "type": r["edge-type"],
                  "count": int(r["size"]), "first_ms": int(r["min"]), "last_ms": int(r["max"])}
                 for _, r in agg.iterrows()] if len(agg) else []
        elist.sort(key=lambda x: x["first_ms"])
        out.append({"id": i, "seed": seed, "n_anomalous": len(anom), "n_connectors": len(members - A),
                    "n_edges": int(len(pe)), "sg_score": round(sg_score, 4), "level": level, "kept": bool(kept),
                    "oversize": oversize, "max_margin": max(x["margin"] for x in nlist if x["role"] == "anomalous"),
                    "nodes": nlist, "edges": elist})
    stats["left_out"] = len(A - in_any)
    out.sort(key=lambda g: (not g["kept"], -g["sg_score"], g["id"]))
    return out, stats


def dedup_identical(built: list) -> list:
    """Keep the first of identical subgraphs. They are induced on their node set, so same nodes ->
    same edges. Rare in practice: non-seed members must be uncorrelated, so a repeat needs a seed whose
    earlier subgraph was partitioned away (Louvain drops communities of < min_nodes nodes)."""
    seen, uniq = set(), []
    for b in built:
        key = frozenset(b[1])
        if key not in seen:
            seen.add(key)
            uniq.append(b)
    return uniq


def _f(v):
    return None if v is None or (isinstance(v, float) and math.isnan(v)) else float(v)


def summarise(subgraphs: list) -> dict:
    kept = [g for g in subgraphs if g["kept"]]
    top = max(subgraphs, key=lambda g: g["sg_score"]) if subgraphs else None
    return {"subgraphs": len(subgraphs), "subgraphs_kept": len(kept),
            "max_sg_score": top["sg_score"] if top else 0.0, "max_level": top["level"] if top else ""}


# --------------------------------------------------------------------------- selftest
def selftest():
    """Hand-built graphs with known answers. 'It runs' + matches the upstream traversal rules;
    not evidence that anything detects attacks."""
    rng = {"process": (-3.0, 2.0), "file": (-1.0, 0.2), "net": (-1.0, 0.0)}

    def mk(spec, edge_list):
        """spec: {id: (type, score)}; tau = training max -> flagged == above the training range."""
        rows = [(n, t, s, rng[t][1]) for n, (t, s) in spec.items()]
        nd = pd.DataFrame(rows, columns=["node_uuid", "node_type", "score", "tau"])
        nd["margin"] = nd["score"] - nd["tau"]
        nd["flagged"] = nd["score"] > nd["tau"]
        ed = pd.DataFrame([(a, b, et, 1_000 + i) for i, (a, b, et) in enumerate(edge_list)],
                          columns=["source-id", "destination-id", "edge-type", "timestamp"])
        return nd, ed

    # (g) prob clamps; (h) level boundaries (upper bound inclusive, as upstream)
    nd, _ = mk({"a": ("file", 5.0), "b": ("file", -9.0), "c": ("file", -0.4)}, [])
    p = node_prob(nd, rng).round(6).tolist()
    assert p == [1.0, 0.0, 0.5], p
    assert [classify_level(x) for x in (1, 1.01, 10, 29.99, 30, 100, 100.5, 1000, 1000.5)] == \
        ["Negligible", "Minor", "Minor", "Minor", "Moderate", "Moderate", "Significant", "Significant", "Critical"]
    assert [classify_level(x, moderate_from=10.0001) for x in (10, 10.01)] == ["Minor", "Moderate"]  # [REPO] scale

    # (a) the 11:25 'updater' shape: 18 DLLs above the training max, loaded by ONE unflagged process
    spec = {"upd": ("process", 0.0), **{f"dll{i}": ("file", 0.32) for i in range(18)}}
    nd, ed = mk(spec, [("upd", f"dll{i}", "load") for i in range(18)])
    sgs, st = construct(nd, ed, {}, rng)
    assert len(sgs) == 1 and sgs[0]["n_anomalous"] == 18 and sgs[0]["n_connectors"] == 1, sgs
    assert sgs[0]["sg_score"] == 18.0 and sgs[0]["level"] == "Minor" and not sgs[0]["kept"], sgs[0]
    assert st["seeds"] == 15 and st["left_out"] == 0, st
    # [REPO] scale (Moderate > 10) -> kept; a numeric cut-off above it -> not kept again
    g = construct(nd, ed, {}, rng, moderate_from=10.0001)[0][0]
    assert g["level"] == "Moderate" and g["kept"], g
    assert not construct(nd, ed, {}, rng, min_score=20, moderate_from=10.0001)[0][0]["kept"]

    # (b) isolated anomalous node + (c) 2 anomalous joined by 1 connector -> Minor/Negligible, dropped
    nd, ed = mk({"iso": ("file", 1.0), "p": ("process", 0.0), "f1": ("file", 1.0), "f2": ("file", 1.0),
                 "q": ("process", 0.0)}, [("p", "f1", "write"), ("p", "f2", "read"), ("q", "iso", "write")])
    nd.loc[nd["node_uuid"] == "q", "flagged"] = False
    ed = ed[ed["destination-id"] != "iso"]                  # iso has no edge at all
    sgs, st = construct(nd, ed, {}, rng)
    assert st["isolated"] == 1 and len(sgs) == 1, (st, sgs)
    assert sgs[0]["n_anomalous"] == 2 and sgs[0]["sg_score"] == 2.0 and sgs[0]["level"] == "Minor"
    assert not sgs[0]["kept"] and st["left_out"] == 1, (sgs[0], st)

    # (d) chain A - x - B - y - C, prob A > C > B: seed A takes B; seed C finds B correlated -> < 3 nodes;
    #     seed B (upstream does not skip correlated seeds) then reaches C -> B is in 2 subgraphs
    nd, ed = mk({"A": ("file", 0.5), "x": ("process", 0.0), "B": ("file", 0.21),
                 "y": ("process", 0.0), "C": ("file", 0.3)},
                [("x", "A", "write"), ("x", "B", "write"), ("y", "B", "read"), ("y", "C", "read")])
    # all three above tau_file 0.2; a wider file range so their probs differ: A .75, C .65, B .605
    sgs, st = construct(nd, ed, {}, {**rng, "file": (-1.0, 1.0)})
    sets = sorted(sorted(n["id"] for n in g["nodes"]) for g in sgs)
    assert sets == [["A", "B", "x"], ["B", "C", "y"]], sets
    assert [g["seed"] for g in sorted(sgs, key=lambda g: g["id"])] == ["A", "B"], sgs

    # (e) identical subgraphs -> first one kept
    built = [("s1", {"a", "b", "c"}, None, False), ("s2", {"c", "b", "a"}, None, False),
             ("s3", {"a", "b"}, None, False)]
    assert [b[0] for b in dedup_identical(built)] == ["s1", "s3"]

    # (f) seed S starts 2 dense clusters (all anomalous, all 1 hop from S) -> 32 edges; max_edges 20 -> Louvain
    spec, el = {"S": ("process", 4.0)}, []
    for c in "LR":
        spec.update({f"{c}p{i}": ("process", 3.0) for i in range(4)})
        el += [(f"{c}p{i}", f"{c}p{j}", "start") for i in range(4) for j in range(4) if i != j]
        el += [("S", f"{c}p{i}", "start") for i in range(4)]
    nd, ed = mk(spec, el)
    sgs, st = construct(nd, ed, {}, rng, max_edges=20)
    assert st["subgraphs_built"] >= 2 and all(g["n_edges"] <= 20 for g in sgs), (st, sgs)
    sgs_big, _ = construct(nd, ed, {}, rng, max_edges=0)       # 0 = no cap, as upstream
    assert len(sgs_big) == 1 and sgs_big[0]["n_edges"] == len(ed), sgs_big
    print("[SELFTEST OK] prob clamp, levels, 18-DLL subgraph = 18 -> Minor (dropped; Moderate/kept on the repo scale), isolated node left out, "
          "2-node story Minor (dropped), chain/seed rules as upstream, dedup, Louvain split")


# --------------------------------------------------------------------------- cli (re-judge saved windows)
def judge_window(wdir: str, score_range: dict, a) -> dict | None:
    sp = os.path.join(wdir, "scores.csv")
    if not os.path.exists(sp):
        return None
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import train_gnn_models as tgm
    nodes = pd.read_csv(sp, dtype={"node_uuid": str})
    nodes["flagged"] = nodes["flagged"].astype(str).str.lower() == "true"
    edges = tgm.normalise_edges(pd.read_csv(os.path.join(wdir, "edges.csv"),
                                            dtype={"source-id": str, "destination-id": str}))
    attrs = dict(zip(nodes["node_uuid"], nodes["attr"].fillna(""))) if "attr" in nodes else {}
    sgs, st = construct(nodes, edges, attrs, score_range, a.n_seed, a.min_nodes, a.max_edges,
                        a.sg_min_level, a.sg_min_score, a.sg_moderate_from)
    json.dump({"window": os.path.basename(wdir), "stats": st, "subgraphs": sgs},
              open(os.path.join(wdir, "subgraphs_alg1.json"), "w"), indent=1)
    cols = ["id", "seed", "n_anomalous", "n_connectors", "n_edges", "sg_score", "level", "kept", "oversize"]
    pd.DataFrame([{c: g[c] for c in cols} for g in sgs], columns=cols).to_csv(
        os.path.join(wdir, "subgraphs_alg1.csv"), index=False)
    return {"window": os.path.basename(wdir), "flagged": int(nodes["flagged"].sum()), **summarise(sgs),
            "left_out": st["left_out"]}


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--run-dir", help="train_gnn_models.py output (train_scores.csv)")
    ap.add_argument("--window-dir", help="one live window dir (scores.csv, edges.csv)")
    ap.add_argument("--session-dir", help="a live session dir: every windows/<wid>/ with scores.csv")
    ap.add_argument("--n-seed", type=int, default=15, help="[PAPER] §7.2.1 n_seed = 15 per node type")
    ap.add_argument("--min-nodes", type=int, default=3, help="[REPO] --min-nodes 3")
    ap.add_argument("--max-edges", type=int, default=5000, help="[PAPER] max_e = 5000 (0 = no cap)")
    ap.add_argument("--sg-min-level", default="Moderate", choices=LEVELS)
    ap.add_argument("--sg-min-score", type=float, default=None,
                    help="[OURS] numeric cut-off (kept = sg_score > X); overrides --sg-min-level")
    ap.add_argument("--sg-moderate-from", type=float, default=MODERATE_FROM,
                    help="[OURS] Moderate starts at sg_score >= X (default 30; [REPO] scale: 10.0001)")
    a = ap.parse_args()
    if not 1 < a.sg_moderate_from <= 100:
        ap.error("--sg-moderate-from must be in (1, 100]")
    if a.selftest:
        return selftest()
    if not a.run_dir or not (a.window_dir or a.session_dir):
        ap.error("--run-dir and one of --window-dir / --session-dir are required (or --selftest)")
    score_range = load_score_range(a.run_dir)
    print(f"[alg1] training score range per type: {score_range}")
    wdirs = [a.window_dir] if a.window_dir else sorted(glob.glob(os.path.join(a.session_dir, "windows", "*")))
    rows = [r for r in (judge_window(w, score_range, a) for w in wdirs) if r]
    if not rows:
        sys.exit("[alg1] no scored windows found")
    df = pd.DataFrame(rows)
    df["alert"] = df["subgraphs_kept"] > 0
    print(df.to_string(index=False))
    print(f"[alg1] windows with >= 1 flagged node: {(df['flagged'] > 0).sum()}  ->  alert after SG-Det: "
          f"{df['alert'].sum()}  (of {len(df)} scored)")


if __name__ == "__main__":
    main()
