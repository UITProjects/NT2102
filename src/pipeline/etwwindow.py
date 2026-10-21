"""etwwindow.py - cut a converted OCR-APT/NODLINK CSV pair into overlapping time windows.

Input : <graph>_<split>_edges.csv       source-id,source-type,destination-id,destination-type,edge-type,timestamp (epoch ms)
        <graph>_<split>_node_attrs.csv  node,node_type,node_attr
Output: <out>/<graph>_<split>_edges.csv       POOLED: every window's edges; node IDs prefixed "w0007_"
        <out>/<graph>_<split>_node_attrs.csv  POOLED node attributes, same prefixes
        <out>/windows.csv                    one row per window: id, segment, start/end, edge and node counts
        <out>/manifest.json                  parameters, input fingerprint, totals (rerun skips if unchanged)
        <out>/per_window/w0007/<graph>_<split>_{edges,node_attrs}.csv   only with --per-window (unprefixed IDs)

Why prefixes: each window must be its own graph. Prefixing node IDs with the window ID makes the pooled file a
disjoint union of window graphs: no edge crosses windows, so RGCN messages and the per-node features computed by
encode_to_PyG.py (action frequencies, idle times) are all window-local.

Window rule: edges are split into contiguous segments (no gap > --max-gap-s between consecutive events; a gap
means a different run or a collection hole). Inside a segment of duration L, windows start at
seg_start + k*stride and cover [start, start + window). Only full windows: k = 0 .. floor((L - window)/stride).
Edges after the last full window, and segments shorter than one window, are dropped and counted in the manifest.

Stdlib only (runs on the VM's embeddable Python). Output is deterministic, written to a temp folder and swapped
in at the end, so an interrupted run never leaves half-written files behind.
"""
import argparse, bisect, csv, json, os, shutil, sys, tempfile, time
from array import array
from datetime import datetime, timezone

EDGE_HEADER = ["source-id", "source-type", "destination-id", "destination-type", "edge-type", "timestamp"]
NODE_HEADER = ["node", "node_type", "node_attr"]
MAX_OPEN_WINDOWS = 1000  # per-window mode keeps one file per window open


def utc(ms):
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_ts(s):
    try:
        return int(s)
    except ValueError:
        return int(float(s))


def open_csv(path):
    fh = open(path, newline="", encoding="utf-8")
    r = csv.reader(fh)
    return fh, r


def check_header(path, got, expected):
    if got != expected:
        raise SystemExit(f"{path}: header {got} != expected {expected}")


def fingerprint(*paths):
    return [[os.path.basename(p), os.path.getsize(p), int(os.path.getmtime(p))] for p in paths]


def find_segments(ts, max_gap_ms):
    """ts: sorted array of epoch ms -> list of (first_ms, last_ms)."""
    segs, start, prev = [], ts[0], ts[0]
    for t in ts:
        if t - prev > max_gap_ms:
            segs.append((start, prev))
            start = t
        prev = t
    segs.append((start, prev))
    return segs


def plan(segs, w_ms, s_ms):
    """-> windows [(seg_idx, start_ms, end_ms)], seg_info [(seg_start, first_window_idx, n_windows)]."""
    windows, info = [], []
    for si, (a, b) in enumerate(segs):
        length = b + 1 - a  # last event is inside the segment
        k = (length - w_ms) // s_ms + 1 if length >= w_ms else 0
        info.append((a, len(windows), k))
        windows += [(si, a + i * s_ms, a + i * s_ms + w_ms) for i in range(k)]
    return windows, info


def window_ids_for(t, starts, info, w_ms, s_ms):
    """Global indices of every window whose [start, start+w) contains t."""
    si = bisect.bisect_right(starts, t) - 1
    a, base, k = info[si]
    if k == 0:
        return range(0)
    off = t - a
    lo = max(0, (off - w_ms) // s_ms + 1)
    hi = min(k - 1, off // s_ms)
    return range(base + lo, base + hi + 1)


def run(a):
    edges_in = os.path.join(a.indir, f"{a.graph}_{a.split}_edges.csv") if not a.edges else a.edges
    nodes_in = os.path.join(a.indir, f"{a.graph}_{a.split}_node_attrs.csv") if not a.nodes else a.nodes
    for p in (edges_in, nodes_in):
        if not os.path.isfile(p):
            raise SystemExit(f"missing input {p} (run 'etwctl convert -Split {a.split}' first)")
    w_ms, s_ms, gap_ms = a.window_min * 60000, a.stride_min * 60000, a.max_gap_s * 1000
    params = {"window_min": a.window_min, "stride_min": a.stride_min, "max_gap_s": a.max_gap_s,
              "graph": a.graph, "split": a.split, "per_window": a.per_window}
    inputs = fingerprint(edges_in, nodes_in)

    man_path = os.path.join(a.out, "manifest.json")
    if not a.force and os.path.isfile(man_path):
        with open(man_path, encoding="utf-8") as fh:
            old = json.load(fh)
        if old.get("params") == params and old.get("inputs") == inputs:
            print(f"[etwwindow] up to date: {a.out} ({old['totals']['windows']} windows); use --force to rebuild")
            return 0

    t0 = time.time()
    # Pass 1: timestamps only -> segments -> window plan
    fh, r = open_csv(edges_in)
    check_header(edges_in, next(r, None), EDGE_HEADER)
    ts = array("q", (parse_ts(row[5]) for row in r))
    fh.close()
    if not ts:
        raise SystemExit(f"{edges_in} has no edges")
    ts = array("q", sorted(ts))
    segs = find_segments(ts, gap_ms)
    windows, info = plan(segs, w_ms, s_ms)
    starts = [x[0] for x in info]
    n_in = len(ts)
    del ts
    if not windows:
        longest = max(b - a for a, b in segs) / 60000
        print(f"[etwwindow] no full {a.window_min}-min window: longest contiguous segment is {longest:.1f} min")
        return 2
    if a.per_window and len(windows) > MAX_OPEN_WINDOWS:
        raise SystemExit(f"--per-window would open {len(windows)} files (> {MAX_OPEN_WINDOWS}); drop --per-window")
    wid = [f"w{i:04d}" for i in range(len(windows))]

    parent = os.path.dirname(os.path.abspath(a.out))
    os.makedirs(parent, exist_ok=True)
    tmp = tempfile.mkdtemp(prefix=os.path.basename(a.out) + ".tmp-", dir=parent)
    try:
        # Pass 2: edges -> pooled (prefixed) + optional per-window files; remember node -> windows
        base = f"{a.graph}_{a.split}"
        pooled_fh = open(os.path.join(tmp, base + "_edges.csv"), "w", newline="", encoding="utf-8")
        pooled = csv.writer(pooled_fh)
        pooled.writerow(EDGE_HEADER)
        per = {}
        if a.per_window:
            for i, name in enumerate(wid):
                d = os.path.join(tmp, "per_window", name)
                os.makedirs(d)
                f = open(os.path.join(d, base + "_edges.csv"), "w", newline="", encoding="utf-8")
                wr = csv.writer(f)
                wr.writerow(EDGE_HEADER)
                per[i] = (f, wr)
        node_win = {}                    # node id -> set(window idx)
        node_type = {}                   # node id -> type (from edges)
        w_edges = [0] * len(windows)
        w_types = [dict() for _ in windows]
        n_out = 0
        fh, r = open_csv(edges_in)
        next(r)
        for row in r:
            t = parse_ts(row[5])
            for i in window_ids_for(t, starts, info, w_ms, s_ms):
                p = wid[i] + "_"
                pooled.writerow([p + row[0], row[1], p + row[2], row[3], row[4], row[5]])
                if per:
                    per[i][1].writerow(row)
                w_edges[i] += 1
                n_out += 1
                for nid, ntype in ((row[0], row[1]), (row[2], row[3])):
                    s = node_win.get(nid)
                    if s is None:
                        s = node_win[nid] = set()
                        node_type[nid] = ntype
                    if i not in s:
                        s.add(i)
                        w_types[i][ntype] = w_types[i].get(ntype, 0) + 1
        fh.close()
        pooled_fh.close()
        for f, _ in per.values():
            f.close()

        # Pass 3: node attributes -> one row per (node, window it appears in)
        nfh = open(os.path.join(tmp, base + "_node_attrs.csv"), "w", newline="", encoding="utf-8")
        nw = csv.writer(nfh)
        nw.writerow(NODE_HEADER)
        per_n = {}
        if a.per_window:
            for i, name in enumerate(wid):
                f = open(os.path.join(tmp, "per_window", name, base + "_node_attrs.csv"), "w", newline="",
                         encoding="utf-8")
                wr = csv.writer(f)
                wr.writerow(NODE_HEADER)
                per_n[i] = (f, wr)
        seen_attr = set()
        fh, r = open_csv(nodes_in)
        check_header(nodes_in, next(r, None), NODE_HEADER)
        for row in r:
            s = node_win.get(row[0])
            if not s:
                continue
            seen_attr.add(row[0])
            for i in sorted(s):
                nw.writerow([wid[i] + "_" + row[0], row[1], row[2]])
                if per_n:
                    per_n[i][1].writerow(row)
        fh.close()
        nfh.close()
        for f, _ in per_n.values():
            f.close()

        types = sorted({t for d in w_types for t in d})
        with open(os.path.join(tmp, "windows.csv"), "w", newline="", encoding="utf-8") as fh:
            wr = csv.writer(fh)
            wr.writerow(["window", "segment", "start_ms", "end_ms", "start_utc", "end_utc", "edges"] + types)
            for i, (si, st, en) in enumerate(windows):
                wr.writerow([wid[i], si, st, en, utc(st), utc(en), w_edges[i]] + [w_types[i].get(t, 0) for t in types])

        # edges that fell in no full window (segment tails, short segments)
        covered = 0
        fh, r = open_csv(edges_in)
        next(r)
        for row in r:
            if window_ids_for(parse_ts(row[5]), starts, info, w_ms, s_ms):
                covered += 1
        fh.close()

        totals = {
            "edges_in": n_in, "edges_out": n_out, "edges_in_no_window": n_in - covered,
            "duplication": round(n_out / max(covered, 1), 2),
            "windows": len(windows),
            "segments": [{"first_utc": utc(x), "last_utc": utc(y), "minutes": round((y - x) / 60000, 1),
                          "windows": info[k][2]} for k, (x, y) in enumerate(segs)],
            "nodes_unique": len(node_win),
            "node_window_copies": sum(len(s) for s in node_win.values()),
            "nodes_without_attrs": len(node_win) - len(seen_attr),
            "node_types": types,
        }
        man = {"tool": "etwwindow.py", "created_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
               "params": params, "inputs": inputs, "totals": totals, "seconds": round(time.time() - t0, 1)}
        with open(os.path.join(tmp, "manifest.json"), "w", encoding="utf-8") as fh:
            json.dump(man, fh, indent=1)

        # swap in: out -> out.old, tmp -> out, delete old
        old = a.out + ".old"
        if os.path.exists(old):
            shutil.rmtree(old)
        if os.path.exists(a.out):
            os.rename(a.out, old)
        os.rename(tmp, a.out)
        if os.path.exists(old):
            shutil.rmtree(old)
    except BaseException:
        shutil.rmtree(tmp, ignore_errors=True)
        raise
    print(json.dumps({k: totals[k] for k in ("windows", "edges_in", "edges_out", "edges_in_no_window",
                                             "nodes_unique", "node_window_copies", "segments")}, indent=1))
    print(f"[etwwindow] wrote {a.out} in {man['seconds']} s")
    return 0


def selftest():
    """Segment 1: 30 min of events every 10 s -> 11 windows (10 min / 2 min). Gap 1 h.
       Segment 2: 12 min -> 2 windows. Gap 1 h. Segment 3: 5 min -> 0 windows (too short)."""
    t0 = 1_700_000_000_000
    rows, ts = [], []
    for seg_start, minutes in ((t0, 30), (t0 + 90 * 60000, 12), (t0 + 162 * 60000, 5)):
        for k in range(minutes * 6 + 1):
            ts.append(seg_start + k * 10000)
    for i, t in enumerate(ts):
        rows.append([f"p1_{t0}", "PROCESS", f"f{i % 3}", "FILE", "Write", str(t)])
    with tempfile.TemporaryDirectory() as d:
        e = os.path.join(d, "T_train_edges.csv")
        n = os.path.join(d, "T_train_node_attrs.csv")
        with open(e, "w", newline="", encoding="utf-8") as fh:
            w = csv.writer(fh); w.writerow(EDGE_HEADER); w.writerows(rows)
        with open(n, "w", newline="", encoding="utf-8") as fh:
            w = csv.writer(fh); w.writerow(NODE_HEADER)
            w.writerows([[f"p1_{t0}", "PROCESS", "a.exe"], ["f0", "FILE", "x"], ["f1", "FILE", "y"], ["f2", "FILE", "z"]])
        out = os.path.join(d, "win")
        args = argparse.Namespace(indir=d, edges=None, nodes=None, out=out, graph="T", split="train",
                                  window_min=10, stride_min=2, max_gap_s=120, per_window=True, force=False)
        import io, contextlib
        with contextlib.redirect_stdout(io.StringIO()):
            rc = run(args)
            rc2 = run(args)  # second run must be a no-op
        with open(os.path.join(out, "manifest.json"), encoding="utf-8") as fh:
            m = json.load(fh)["totals"]
        with open(os.path.join(out, "T_train_edges.csv"), encoding="utf-8") as fh:
            pooled = list(csv.reader(fh))
        with open(os.path.join(out, "T_train_node_attrs.csv"), encoding="utf-8") as fh:
            pnodes = list(csv.reader(fh))
        with open(os.path.join(out, "per_window", "w0003", "T_train_edges.csv"), encoding="utf-8") as fh:
            w3 = list(csv.reader(fh))
        ok = (rc == 0 and rc2 == 0 and m["windows"] == 13
              and [s["windows"] for s in m["segments"]] == [11, 2, 0]
              and len(w3) == 1 + 60                                   # 10 min of one event per 10 s
              and pooled[1][0] == f"w0000_p1_{t0}" and pooled[1][2].startswith("w0000_f")
              and len(pooled) - 1 == m["edges_out"]
              and len(pnodes) - 1 == 13 * 4                           # 4 nodes copied into each of 13 windows
              and m["edges_in_no_window"] == 31 + 1 + 1)  # 5-min segment (31) + the event at the exclusive end of seg 1 and seg 2
    print("selftest", "OK" if ok else "FAILED", json.dumps({k: m[k] for k in ("windows", "edges_out",
                                                                             "edges_in_no_window")}))
    return 0 if ok else 1


def main(argv=None):
    ap = argparse.ArgumentParser(description="Cut OCR-APT/NODLINK CSVs into overlapping time windows")
    ap.add_argument("--indir", help="folder with <graph>_<split>_edges.csv and _node_attrs.csv")
    ap.add_argument("--edges"); ap.add_argument("--nodes")
    ap.add_argument("--out")
    ap.add_argument("--graph", default="SimulatedW10")
    ap.add_argument("--split", default="train", choices=["train", "test"])
    ap.add_argument("--window-min", type=int, default=5)
    ap.add_argument("--stride-min", type=int, default=2)
    ap.add_argument("--max-gap-s", type=int, default=120)
    ap.add_argument("--per-window", action="store_true")
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args(argv)
    if a.selftest:
        return selftest()
    if not a.out or not (a.indir or (a.edges and a.nodes)):
        ap.error("--out and (--indir or --edges + --nodes) are required")
    if a.stride_min > a.window_min:
        ap.error("--stride-min > --window-min would leave unobserved gaps between windows")
    return run(a)


if __name__ == "__main__":
    sys.exit(main())
