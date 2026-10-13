"""
w10_to_rdf.py - minimal, Windows-only version of upstream OCR-APT/src/transform_to_RDF.py ([REPO] l.N).
Not used by the live demo (Alg. 1 runs in memory); kept for the GraphDB path. Deviations: DEVIATIONS D4.

Scope: dataset=nodlink, host=SimulatedW10, CSV input only (what etw2nodlink.py produces).
Outputs (same names/format the downstream repo scripts read):
  <root>/<graph>.tsv            s/p/o triples      -> [REPO] encode_to_PyG.py l.269
  <root>/<graph>_graph_df.csv   clean edge list    -> [REPO] encode_to_PyG.py l.270
  <root>/<graph>_rdfs.ttl       Turtle-star        -> GraphDB (SPARQL in sparql_queries.py)
[OURS] dropped: the .nt file, tc3/optc/networkx branches, --adjust-uuid, --filter-node-type.

Usage (from the repo root; experiments hold only a train split so far):
  uv run python src/pipeline/w10_to_rdf.py --root-path src/out/etw/experiments/<id> --splits train
"""
import argparse
import re
import sys
from pathlib import Path

import pandas as pd

HOST = "SimulatedW10"
PREFIX = f"https://NODLINK.graph/{HOST}/"           # same namespace as [REPO] l.47-48
RDF_TYPE = "http://www.w3.org/1999/02/22-rdf-syntax-ns#type"

# Fixed vocabularies the downstream code assumes for this host:
EDGE_TYPES = {"Write", "Read", "Send", "Recv", "Start", "Load"}   # [REPO] database_config.py l.85-86
NODE_TYPES = {"PROCESS", "FILE", "NET"}                           # [REPO] database_config.py l.105-106
EDGE_COLS = ["source-id", "source-type", "destination-id", "destination-type", "edge-type", "timestamp"]
ID_OK = re.compile(r"^[A-Za-z0-9._:\-]+$")   # safe inside an IRI, and no "/" (repo splits on it)
MS_MIN, MS_MAX = 946_684_800_000, 4_102_444_800_000  # 2000-01-01 .. 2100-01-01 in epoch ms


def die(msg):
    sys.exit(f"[w10_to_rdf] ERROR: {msg}")


def load_edges(root, graph, split):
    """Read <graph>_<split>_edges.csv, clean it, and validate it against the W10 vocabulary."""
    path = root / f"{graph}_{split}_edges.csv"
    if not path.exists():
        die(f"{path} not found. Only have some splits? Pass e.g. --splits train")
    df = pd.read_csv(path, dtype={"source-id": str, "destination-id": str})
    missing = set(EDGE_COLS) - set(df.columns)
    if missing:
        die(f"{split}: missing columns {missing}")
    df = df[EDGE_COLS]
    n0 = len(df)
    df = df.dropna(subset=["edge-type"])                                 # [REPO] l.91 meant to do this (no-op bug)
    df["edge-type"] = df["edge-type"].astype(str).str.split("/").str[-1]  # [REPO] l.90
    bad_edge = ~df["edge-type"].isin(EDGE_TYPES)
    bad_node = ~df["source-type"].isin(NODE_TYPES) | ~df["destination-type"].isin(NODE_TYPES)
    if bad_edge.any():
        print(f"  {split}: dropping unknown edge types {df.loc[bad_edge, 'edge-type'].value_counts().to_dict()}")
    if bad_node.any():
        print(f"  {split}: dropping edges with unknown node types")
    df = df[~bad_edge & ~bad_node].copy()
    print(f"  {split}: {n0} rows -> {len(df)} edges after cleaning")

    ids = pd.concat([df["source-id"], df["destination-id"]]).unique()
    bad_ids = [i for i in ids if not ID_OK.match(str(i))]
    if bad_ids:
        die(f"{split}: {len(bad_ids)} node ids are not IRI-safe, e.g. {bad_ids[:3]} (fix etw2nodlink.py)")

    df["timestamp"] = pd.to_numeric(df["timestamp"], errors="raise").astype("int64")
    lo, hi = df["timestamp"].min(), df["timestamp"].max()
    if lo < MS_MIN or hi > MS_MAX:
        # [REPO] encode_to_PyG.py l.167-168 divides W10 gaps by 1000 -> timestamps must be epoch MILLISECONDS
        die(f"{split}: timestamps {lo}..{hi} are not epoch ms (s~1.7e9, ns~1.7e18, FILETIME~1.3e17)")
    return df


def nodes_of(df):
    """Unique (node, type) pairs seen as source or destination. Shape: (n_nodes, 2)."""
    src = df[["source-id", "source-type"]].set_axis(["node", "type"], axis=1)
    dst = df[["destination-id", "destination-type"]].set_axis(["node", "type"], axis=1)
    return pd.concat([src, dst]).drop_duplicates()


def rare_types(train, test, k):
    """[REPO] l.266-293: drop node types with <= k nodes in train OR test."""
    rare = set()
    for df in (train, test):
        if df.empty:          # train-only mode: test contributes nothing
            continue
        counts = nodes_of(df)["type"].value_counts()
        rare |= set(counts[counts <= k].index)
    return rare


def esc(s):
    """Escape a Turtle string literal (the repo's CSV trick misses quotes/newlines)."""
    return (str(s).replace("\\", "\\\\").replace('"', '\\"')
            .replace("\n", "\\n").replace("\r", "\\r").replace("\t", "\\t"))


def main():
    ap = argparse.ArgumentParser(description="SimulatedW10 CSV -> OCR-APT RDF/TSV")
    ap.add_argument("--root-path", required=True, type=Path)
    ap.add_argument("--graph", default=HOST)
    ap.add_argument("--min-node-representation", type=int, default=5)
    ap.add_argument("--splits", nargs="+", choices=["train", "test"], default=["train", "test"],
                    help="which <graph>_<split>_edges.csv files to read; train is always required")
    a = ap.parse_args()
    root, graph = a.root_path, a.graph

    # 1. Load + validate the requested splits; a skipped split becomes an empty edge table
    if "train" not in a.splits:
        die("--splits must include train (a one-class model is trained on benign train data)")
    empty = pd.DataFrame(columns=EDGE_COLS)
    train = load_edges(root, graph, "train")
    test = load_edges(root, graph, "test") if "test" in a.splits else empty
    if test.empty:
        print("  WARNING: train-only mode -> no is_Train=False nodes, so no test set and no evaluation;"
              " run encode_to_PyG.py with --training-valid")

    # 2. Remove rare node types (and every edge touching them)
    rare = rare_types(train, test, a.min_node_representation)
    if rare:
        print(f"  removing rare node types: {sorted(rare)}")
    keep = lambda df: df[~df["source-type"].isin(rare) & ~df["destination-type"].isin(rare)]
    train, test = keep(train), keep(test)

    # 3. Node table with split flag. Test first; a node seen in both splits counts as test ([REPO] l.106-109).
    test_nodes = nodes_of(test).assign(is_Train="False")
    train_nodes = nodes_of(train).merge(test_nodes[["node", "type"]], how="left", indicator=True)
    train_nodes = train_nodes[train_nodes["_merge"] == "left_only"].drop(columns="_merge").assign(is_Train="True")
    nodes = pd.concat([test_nodes, train_nodes], ignore_index=True)
    if nodes["node"].duplicated().any():
        die(f"node ids with >1 type: {nodes.loc[nodes['node'].duplicated(), 'node'].head(3).tolist()}")

    # 4. Labels. [REPO] requires this file; [OURS] optional, and a missing file means all benign.
    gt = root / f"{HOST}_ground_truth.txt"
    if gt.exists() and gt.stat().st_size > 0:
        mal = set(pd.read_csv(gt, header=None, dtype=str)[0].str.strip())
    else:
        print(f"  WARNING: {gt.name} missing/empty -> every node labelled benign (no evaluation possible)")
        mal = set()
    nodes["is_malicious"] = nodes["node"].isin(mal).map({True: "True", False: "False"})
    print(f"  nodes: {len(nodes)} ({nodes['type'].value_counts().to_dict()}), malicious: {(nodes.is_malicious == 'True').sum()}")

    # 5. Node attributes (optional per split): columns node,node_attr
    attrs = []
    for split in a.splits:
        p = root / f"{graph}_{split}_node_attrs.csv"
        if p.exists():
            attrs.append(pd.read_csv(p, dtype=str)[["node", "node_attr"]])
    attrs = (pd.concat(attrs).dropna().drop_duplicates("node") if attrs
             else pd.DataFrame(columns=["node", "node_attr"]))
    attrs = attrs[attrs["node"].isin(nodes["node"])]

    # 6. Build the s/p/o table in the exact string format encode_to_PyG expects.
    iri = lambda s: PREFIX + "node/" + s
    edges = pd.concat([train, test], ignore_index=True)
    parts = [
        pd.DataFrame({"s": iri(nodes.node), "p": PREFIX + "node-type", "o": PREFIX + nodes.type}),
        pd.DataFrame({"s": iri(nodes.node), "p": PREFIX + "uuid", "o": '"' + nodes.node + '"'}),
        pd.DataFrame({"s": iri(nodes.node), "p": PREFIX + "is_Train", "o": nodes.is_Train}),
        pd.DataFrame({"s": iri(nodes.node), "p": PREFIX + "is_malicious", "o": nodes.is_malicious}),
        pd.DataFrame({"s": iri(attrs.node), "p": PREFIX + "node-attribute", "o": '"' + attrs.node_attr + '"'}),
        # schema rows: full IRIs here (repo writes bare names); encode_to_PyG only uses split("/")[-1]
        pd.DataFrame({"s": PREFIX + pd.Series(sorted(nodes.type.unique())), "p": RDF_TYPE, "o": PREFIX + "node-type"}),
        pd.DataFrame({"s": PREFIX + pd.Series(sorted(edges["edge-type"].unique())), "p": RDF_TYPE, "o": PREFIX + "edge-type"}),
        pd.DataFrame({"s": iri(edges["source-id"]), "p": PREFIX + edges["edge-type"], "o": iri(edges["destination-id"])}),
    ]
    triples = pd.concat(parts, ignore_index=True)
    triples.to_csv(root / f"{graph}.tsv", sep="\t", index=False)        # pandas quotes safely on both ends
    edges.to_csv(root / f"{graph}_graph_df.csv", sep="\t", index=False)

    # 7. Turtle-star for GraphDB: node facts as plain triples, edges as << s p o >> graph:timestamp "t".
    with open(root / f"{graph}_rdfs.ttl", "w", encoding="utf-8") as f:
        f.write(f"@prefix graph: <{PREFIX}> .\n\n")
        n = lambda x: f"<{PREFIX}node/{x}>"   # full IRI: ids like "1-abc" or "x." are not valid prefixed names
        for r in nodes.itertuples(index=False):
            f.write(f'{n(r.node)} graph:node-type graph:{r.type} ; graph:uuid "{r.node}" ; '
                    f'graph:is_Train "{r.is_Train}" ; graph:is_malicious "{r.is_malicious}" .\n')
        for r in attrs.itertuples(index=False):
            f.write(f'{n(r.node)} graph:node-attribute "{esc(r.node_attr)}" .\n')
        for t in sorted(nodes.type.unique()):
            f.write(f"graph:{t} a graph:node-type .\n")
        for e in sorted(edges["edge-type"].unique()):
            f.write(f"graph:{e} a graph:edge-type .\n")
        for s, et, d, ts in edges[["source-id", "edge-type", "destination-id", "timestamp"]].itertuples(index=False):
            f.write(f'<< {n(s)} graph:{et} {n(d)} >> graph:timestamp "{ts}" .\n')

    print(f"  wrote {len(triples)} tsv triples, {len(edges)} edges -> {root}")


if __name__ == "__main__":
    main()