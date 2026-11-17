#!/usr/bin/env python3
"""
snapshot.py - a recorded, read-only copy of the live server's history for the public demo (deploy/README.md).
[OURS] engineering: nothing here scores a window; every score / level / LLM verdict in a snapshot is what the live
server computed at the time, with the model and settings recorded in each session.json.

    build    sessions/ (trainer) -> one deterministic .tar.gz:
                 manifest.json                          range, counts, models, note, sha256 of every other file
                 sessions/history.jsonl                 the WINNING row per (host, window) (restart duplicates and
                                                        replay sessions left out), end <= --until
                 sessions/<sdir>/session.json           run parameters (absolute paths cut to names)
                 sessions/<sdir>/windows/<wid>/detail.json   the UI detail (top nodes, subgraphs, LLM summary +
                                                        report), rebuilt from scores.csv where detail.json is missing
                 sessions/crosshost.jsonl               cross-host findings that start before --until
             Raw ETW lines, edges, features and LLM prompt logs (calls.jsonl) are NOT copied. Every string goes
             through a redaction pass (known secrets + password-looking arguments), then the output is scanned again
             and the build fails if a known secret is still in it.

    open     SnapshotArchive: unpack the .tar.gz once into a data folder (tarfile filter "data": no path traversal,
             no links outside), check every file's sha256 against manifest.json, then serve it through the read-only
             Snapshot* repositories (repository.py). Any write raises ReadOnlyRepositoryError.

Usage (from the repo root)
    lab snapshot build --until 2026-11-15T20:15Z --out deploy/snapshot/nt2102-snapshot.tar.gz
    uv run python src/live/snapshot.py --selftest
"""
from __future__ import annotations

import gzip
import hashlib
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from repository import (CrosshostRepository, HistoryRepository, ReadOnlyRepositoryError,  # noqa: E402
                        Repositories, SessionStore, check_name)

FORMAT = 1
MANIFEST = "manifest.json"
REDACTED = "[REDACTED]"
# password-looking arguments in command lines (Sysmon EID 1, D14) and LLM reports quoting them:
#   -Password x | /p:x | password=x | --password x | -p 'x' after a user flag is too ambiguous and is not touched
SECRET_PATTERNS = [re.compile(r"(?i)(-{1,2}password[\s:=]+)(['\"]?)([^\s'\"]+)"),
                   re.compile(r"(?i)((?<![a-z])/p(?:assword)?:)(['\"]?)([^\s'\"]+)"),
                   re.compile(r"(?i)(\bpassword=)(['\"]?)([^\s'\"&;]+)"),
                   re.compile(r"(?i)(convertto-securestring\s+)(['\"]?)([^\s'\"]+)")]
DEFAULT_NOTE = ("Recorded snapshot of the live OCR-APT detector (read-only). Scores, alert levels and LLM verdicts are "
                "what the live server computed at the time with the model recorded per session; nothing is re-scored "
                "here. The 4.5 alert cut-off and NET rule off (since 2026-11-15) were tuned on the 2026-11-15 "
                "react2shell run, so that run is in-sample evidence, not an independent test.")


# --------------------------------------------------------------------------- redaction
def redact_str(s: str, secrets: list[str]) -> str:
    for sec in secrets:
        if sec:
            s = s.replace(sec, REDACTED)
    for pat in SECRET_PATTERNS:
        s = pat.sub(lambda m: m.group(1) + m.group(2) + REDACTED, s)
    return s


def redact(obj, secrets: list[str]):
    """Every string inside a JSON-like object (dict keys too); numbers / bools untouched."""
    if isinstance(obj, str):
        return redact_str(obj, secrets)
    if isinstance(obj, dict):
        return {redact(k, secrets): redact(v, secrets) for k, v in obj.items()}
    if isinstance(obj, list):
        return [redact(v, secrets) for v in obj]
    return obj


def secrets_from_ini(path: str, keys=("win_password", "grafana_admin_password")) -> list[str]:
    """Values of key=value lines in an Ansible inventory (proxmox.ini); missing file -> []."""
    out = []
    try:
        for line in open(path, encoding="utf-8"):
            k, sep, v = line.strip().partition("=")
            if sep and k.strip() in keys and v.strip():
                out.append(v.strip().strip("'\""))
    except OSError:
        pass
    return out


# --------------------------------------------------------------------------- build
def _sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _dump(path: str, obj, secrets: list[str]) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(redact(obj, secrets), fh, separators=(",", ":"), default=str)


def _git_sha(repo: str) -> str:
    try:
        return subprocess.run(["git", "-C", repo, "rev-parse", "--short", "HEAD"], capture_output=True, text=True,
                              timeout=5, check=True).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return "unknown"


def _tar_deterministic(src_dir: str, out_path: str) -> None:
    """Same input files -> same bytes: sorted names, mtime 0, uid/gid 0, fixed modes, gzip header mtime 0."""
    names = []
    for root, dirs, files in os.walk(src_dir):
        dirs.sort()
        for f in sorted(files):
            names.append(os.path.relpath(os.path.join(root, f), src_dir))
    names.sort(key=lambda n: (n != MANIFEST, n))                  # manifest first: readable without unpacking all
    os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
    tmp = out_path + ".tmp"
    with open(tmp, "wb") as raw, gzip.GzipFile(fileobj=raw, mode="wb", mtime=0, compresslevel=9) as gz, \
            tarfile.open(fileobj=gz, mode="w", format=tarfile.PAX_FORMAT) as tar:
        for n in names:
            ti = tarfile.TarInfo(n)
            data = open(os.path.join(src_dir, n), "rb").read()
            ti.size, ti.mtime, ti.mode, ti.uid, ti.gid, ti.uname, ti.gname = len(data), 0, 0o644, 0, 0, "", ""
            tar.addfile(ti, io.BytesIO(data))
    os.replace(tmp, out_path)


def build(sessions_dir: str, until_ms: int, out_path: str, from_ms: int | None = None, secrets: list[str] = (),
          runs_dir: str | None = None, note: str = DEFAULT_NOTE, repo: str | None = None, log=print) -> dict:
    """sessions_dir (a live server's, read only) -> out_path .tar.gz. Returns the manifest."""
    from file_store import FileCrosshostRepository
    from history import FileHistoryRepository, train_summary
    if not os.path.exists(os.path.join(sessions_dir, "history.jsonl")):
        raise SystemExit(f"{sessions_dir}/history.jsonl not found: not a live server's sessions folder")
    secrets = [s for s in secrets if s]
    src = FileHistoryRepository(sessions_dir)
    first = src.bounds()
    if first is None:
        raise SystemExit("history.jsonl is empty")
    lo = from_ms if from_ms is not None else first[0]
    # the winning row per (host, window start), live sessions only; then only windows that END by `until`
    rows = [r for r in src.query(lo, until_ms, limit=0)["windows"] if r.get("end_ms", 0) <= until_ms]
    if not rows:
        raise SystemExit("no window in that range")

    stage = tempfile.mkdtemp(prefix="nt2102-snapshot-")
    try:
        sroot = os.path.join(stage, "sessions")
        details = 0
        for r in rows:
            d = src._read_detail(r["sdir"], str(r["window"]))
            if d is not None:
                _dump(os.path.join(sroot, r["sdir"], "windows", str(r["window"]), "detail.json"), d, secrets)
                details += 1
        os.makedirs(sroot, exist_ok=True)
        with open(os.path.join(sroot, "history.jsonl"), "w", encoding="utf-8") as fh:
            for r in rows:
                fh.write(json.dumps(redact(r, secrets), default=str) + "\n")
        models, model_hosts = {}, {}
        sdirs = sorted({r["sdir"] for r in rows})
        for sdir in sdirs:
            try:
                meta = json.load(open(os.path.join(sessions_dir, sdir, "session.json")))
            except (OSError, ValueError):
                continue
            run = os.path.basename(os.path.normpath(meta.get("run_dir") or "")) or None
            if run:
                meta["run_dir"] = run                             # a name, not the trainer's home path
                if run not in models:
                    models[run] = train_summary(os.path.join(runs_dir, run)) if runs_dir else {}
                if meta.get("host"):
                    model_hosts.setdefault(run, set()).add(meta["host"])
            if meta.get("scaler"):
                meta["scaler"] = os.path.basename(meta["scaler"])
            _dump(os.path.join(sroot, sdir, "session.json"), meta, secrets)
        found = [f for f in FileCrosshostRepository(sessions_dir).load().values()
                 if isinstance(f.get("start_ms"), int) and f["start_ms"] < until_ms and f["start_ms"] >= lo]
        with open(os.path.join(sroot, "crosshost.jsonl"), "w", encoding="utf-8") as fh:
            for f in sorted(found, key=lambda f: f["start_ms"]):
                f = {k: v for k, v in f.items() if k not in ("dirty", "fresh")}
                fh.write(json.dumps(redact(f, secrets), default=str) + "\n")

        # scan: no known secret may survive (the redaction is the guard, this is the check)
        files = {}
        for root, _, fs in os.walk(stage):
            for f in fs:
                p = os.path.join(root, f)
                text = open(p, encoding="utf-8").read()
                for sec in secrets:
                    if sec in text:
                        raise SystemExit(f"redaction failed: a known secret is still in {os.path.relpath(p, stage)}")
                files[os.path.relpath(p, stage).replace(os.sep, "/")] = _sha256(p)
        hosts = sorted({r.get("host") for r in rows if r.get("host")})
        red = [r for r in rows if r.get("alert")]
        manifest = {
            "format": FORMAT, "from_ms": rows[0]["start_ms"], "to_ms": max(r["end_ms"] for r in rows),
            "until_ms": until_ms, "generated_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "code": _git_sha(repo or os.path.dirname(os.path.dirname(HERE))), "hosts": hosts,
            "counts": {"windows": len(rows), "details": details, "sessions": len(sdirs), "red": len(red),
                       "llm_attack": sum(1 for r in red if (r.get("llm") or {}).get("status") == "attack"),
                       "crosshost": len(found)},
            "models": models, "model_hosts": {k: sorted(v) for k, v in sorted(model_hosts.items())}, "note": note, "files": dict(sorted(files.items())),
        }
        json.dump(manifest, open(os.path.join(stage, MANIFEST), "w"), indent=1)
        _tar_deterministic(stage, out_path)
    finally:
        shutil.rmtree(stage, ignore_errors=True)
    log(f"[snapshot] {manifest['counts']} -> {out_path} ({os.path.getsize(out_path) / 1e6:.1f} MB)")
    return manifest


# --------------------------------------------------------------------------- open (unpack + verify)
class SnapshotError(RuntimeError):
    pass


class SnapshotArchive:
    """Unpacks a snapshot .tar.gz into data_dir/<archive sha256[:16]>/ once (a restart with the same archive and a
    kept data_dir skips it), verifies every file against manifest.json, and exposes root + manifest."""

    def __init__(self, root: str, manifest: dict, sha256: str):
        self.root, self.manifest, self.sha256 = root, manifest, sha256

    @classmethod
    def open(cls, tar_path: str, data_dir: str, log=print) -> "SnapshotArchive":
        t0 = time.time()
        sha = _sha256(tar_path)
        root = os.path.join(data_dir, sha[:16])
        ok_mark = os.path.join(root, ".verified")
        if not os.path.exists(ok_mark):
            if os.path.exists(root):
                shutil.rmtree(root)                       # a half-done unpack from a killed start
            tmp = root + ".part"
            shutil.rmtree(tmp, ignore_errors=True)
            os.makedirs(tmp)
            with tarfile.open(tar_path, "r:gz") as tar:
                tar.extractall(tmp, filter="data")        # refuses absolute paths, '..', links out of tmp
            cls._verify(tmp)
            open(os.path.join(tmp, ".verified"), "w").write(sha + "\n")
            os.replace(tmp, root)
            log(f"[snapshot] unpacked + verified {tar_path} -> {root} in {time.time() - t0:.1f}s")
        else:
            log(f"[snapshot] reusing {root} (already verified)")
        manifest = json.load(open(os.path.join(root, MANIFEST)))
        return cls(root, manifest, sha)

    @staticmethod
    def _verify(root: str) -> None:
        try:
            manifest = json.load(open(os.path.join(root, MANIFEST)))
        except (OSError, ValueError) as e:
            raise SnapshotError(f"no readable {MANIFEST}: {e}")
        if manifest.get("format") != FORMAT:
            raise SnapshotError(f"snapshot format {manifest.get('format')} != {FORMAT}")
        for rel, want in manifest.get("files", {}).items():
            p = os.path.join(root, *rel.split("/"))
            if not os.path.isfile(p) or _sha256(p) != want:
                raise SnapshotError(f"{rel}: missing or sha256 mismatch")


# --------------------------------------------------------------------------- read-only repositories
def _read_only(*_a, **_k):
    raise ReadOnlyRepositoryError("this is a recorded snapshot: read-only")


class SnapshotHistoryRepository(HistoryRepository):
    def __init__(self, root: str):
        super().__init__()
        self.dir = os.path.join(root, "sessions")
        with open(os.path.join(self.dir, "history.jsonl"), encoding="utf-8") as fh:
            for line in fh:
                r = json.loads(line)
                self.rows[(r["sdir"], str(r["window"]))] = r

    append = _read_only

    def _read_detail(self, sdir: str, wid: str) -> dict | None:
        try:
            return json.load(open(os.path.join(self.dir, sdir, "windows", wid, "detail.json"), encoding="utf-8"))
        except (OSError, ValueError):
            return None


class SnapshotCrosshostRepository(CrosshostRepository):
    def __init__(self, root: str):
        self.path = os.path.join(root, "sessions", "crosshost.jsonl")

    def load(self) -> dict[str, dict]:
        out = {}
        try:
            with open(self.path, encoding="utf-8") as fh:
                for line in fh:
                    f = json.loads(line)
                    out[f["id"]] = f
        except OSError:
            pass
        return out

    append = _read_only


class SnapshotSessionStore(SessionStore):
    def __init__(self, root: str):
        self.root = os.path.join(root, "sessions")

    def path(self, sdir: str = "") -> str:
        if sdir:
            check_name(sdir)
        return os.path.join(self.root, sdir) if sdir else self.root

    def window_dir(self, sdir: str, wid: str) -> str:
        check_name(wid)
        return os.path.join(self.path(sdir), "windows", wid)

    def load_detail(self, sdir: str, wid: str) -> dict | None:
        try:
            return json.load(open(os.path.join(self.window_dir(sdir, wid), "detail.json"), encoding="utf-8"))
        except (OSError, ValueError):
            return None

    def session_meta(self, sdir: str) -> dict:
        try:
            return json.load(open(os.path.join(self.path(sdir), "session.json"), encoding="utf-8"))
        except (OSError, ValueError):
            return {}

    def budget(self, today: str) -> dict:
        return {"date": today, "used": 0}

    create_session = open_raw = write_window = append_index = append_verdict = save_detail = take_budget = _read_only


def open_snapshot(tar_path: str, data_dir: str | None) -> Repositories:
    if not data_dir:
        raise SnapshotError("a snapshot needs a data dir to unpack into (--data-dir)")
    arc = SnapshotArchive.open(tar_path, data_dir)
    return Repositories(SnapshotHistoryRepository(arc.root), SnapshotCrosshostRepository(arc.root),
                        SnapshotSessionStore(arc.root), {**arc.manifest, "archive_sha256": arc.sha256})


# --------------------------------------------------------------------------- selftest
def selftest():
    """'It runs': build from a synthetic sessions folder, cut-off, dedup, redaction + scan, determinism, unpack +
    verify + tamper detection, traversal refused, read-only repositories. Says nothing about detection quality."""
    from file_store import FileCrosshostRepository, FileSessionStore
    from history import FileHistoryRepository
    from repository import open_repositories
    base = 1_794_700_000_000
    m5 = 300_000
    secret = "Hunter2!lab"
    with tempfile.TemporaryDirectory() as d:
        sess = os.path.join(d, "sessions")
        h, st = FileHistoryRepository(sess), FileSessionStore(sess)
        for sdir in ("live-a-ws-etw-1", "live-a-ws-etw-1.r2", "replay-x"):
            os.makedirs(os.path.join(sess, sdir), exist_ok=True)
            json.dump({"session": sdir, "run_dir": "/home/it2102/NT2102/src/artifacts/runs/exp15-ws1",
                       "scaler": "/home/it2102/x/feature_scaler.json", "alg1": {"moderate_from": 4.5}},
                      open(os.path.join(sess, sdir, "session.json"), "w"))
        for k in range(6):
            s = base + k * m5
            mode = "loki"
            row = {"session": "live-a", "mode": mode, "moderate_from": 4.5, "window": f"w{k}", "host": "ws-etw-1",
                   "start_ms": s, "end_ms": s + m5, "status": "scored", "alert": k == 2, "max_sg_score": 7.0}
            h.append({"sdir": "live-a-ws-etw-1", **row, "status": "partial" if k < 2 else "scored"})
            if k < 2:                                    # the restart re-scored w0, w1: those rows win
                h.append({"sdir": "live-a-ws-etw-1.r2", **row})
                st.save_detail("live-a-ws-etw-1.r2", f"w{k}", {"top": {}, "window": row})
            elif k != 3:
                st.save_detail("live-a-ws-etw-1", f"w{k}", {
                    "top": {"process": [{"id": "p1", "cmdline": f"net use \\\\dc /user:x /p:{secret}"}]},
                    "llm": {"verdict": "attack", "subgraphs": [{"report": f"used -Password {secret} then {secret}"}]},
                    "window": row})
            h.append({"sdir": "replay-x", **row, "mode": "replay"})
        FileCrosshostRepository(sess).append({"id": "f1", "start_ms": base + 2 * m5, "level": "one-sided", "dirty": True})
        FileCrosshostRepository(sess).append({"id": "f9", "start_ms": base + 9 * m5, "level": "one-sided"})

        until = base + 5 * m5                                 # w0..w4 end by then; w5 does not
        out = os.path.join(d, "snap.tar.gz")
        man = build(sess, until, out, secrets=[secret], log=lambda *_: None)
        assert man["counts"]["windows"] == 5 and man["counts"]["red"] == 1 and man["counts"]["crosshost"] == 1, man
        assert man["counts"]["details"] == 4, "w3 had no detail and is still listed"
        raw = gzip.open(out).read()
        assert secret.encode() not in raw and b"/home/it2102" not in raw, "secret or home path in the archive"
        out2 = os.path.join(d, "snap2.tar.gz")
        man2 = build(sess, until, out2, secrets=[secret], log=lambda *_: None)
        assert man2["files"] == man["files"], "same input must give the same files"
        try:
            build(sess, until, os.path.join(d, "x.tar.gz"), secrets=["1"], log=lambda *_: None)   # in a timestamp
            raise AssertionError("scan missed a secret")
        except SystemExit as e:
            assert "redaction" in str(e) or "secret" in str(e), e

        repos = open_repositories("snapshot", out, os.path.join(d, "data"))
        q = repos.history.query(0, 10 ** 15)["windows"]
        assert [r["window"] for r in q] == ["w0", "w1", "w2", "w3", "w4"], q
        assert q[0]["sdir"] == "live-a-ws-etw-1.r2" and repos.history.query(0, 10 ** 15, include_replay=True)[
            "duplicates_hidden"] == 0, "replays and duplicates must not be in the snapshot"
        det = repos.history.detail("live-a-ws-etw-1", "w2")
        assert det["llm"]["verdict"] == "attack" and REDACTED in det["llm"]["subgraphs"][0]["report"], det
        assert REDACTED in det["top"]["process"][0]["cmdline"], det
        assert repos.history.detail("live-a-ws-etw-1", "w3")["window"]["window"] == "w3"
        assert repos.sessions.session_meta("live-a-ws-etw-1")["run_dir"] == "exp15-ws1"
        assert list(repos.crosshost.load()) == ["f1"] and "dirty" not in repos.crosshost.load()["f1"]
        assert repos.manifest["hosts"] == ["ws-etw-1"]
        for write in (lambda: repos.history.append({}), lambda: repos.crosshost.append({}),
                      lambda: repos.sessions.save_detail("a", "b", {}), lambda: repos.sessions.take_budget(1, "d")):
            try:
                write()
                raise AssertionError("write accepted")
            except ReadOnlyRepositoryError:
                pass
        # second open: reuse; a tampered unpack (no .verified mark) is redone and a tampered archive is refused
        root = SnapshotArchive.open(out, os.path.join(d, "data"), log=lambda *_: None).root
        os.remove(os.path.join(root, ".verified"))
        open(os.path.join(root, "sessions", "history.jsonl"), "a").write("tampered\n")
        SnapshotArchive.open(out, os.path.join(d, "data"), log=lambda *_: None)      # re-unpacked from the archive
        assert len(open(os.path.join(root, "sessions", "history.jsonl")).read().splitlines()) == 5
        bad = os.path.join(d, "bad.tar.gz")
        with tarfile.open(out, "r:gz") as src, tarfile.open(bad, "w:gz") as dst:
            for m in src.getmembers():
                data = src.extractfile(m).read()
                if m.name.endswith("history.jsonl"):
                    data += b"{}\n"
                    m.size = len(data)
                dst.addfile(m, io.BytesIO(data))
        try:
            SnapshotArchive.open(bad, os.path.join(d, "data2"), log=lambda *_: None)
            raise AssertionError("tampered archive accepted")
        except SnapshotError:
            pass
        evil = os.path.join(d, "evil.tar.gz")
        with tarfile.open(evil, "w:gz") as tar:
            ti = tarfile.TarInfo("../escape.txt")
            ti.size = 1
            tar.addfile(ti, io.BytesIO(b"x"))
        try:
            SnapshotArchive.open(evil, os.path.join(d, "data3"), log=lambda *_: None)
            raise AssertionError("path traversal accepted")
        except (tarfile.TarError, SnapshotError):
            pass
        assert not os.path.exists(os.path.join(d, "escape.txt"))
        assert redact_str("x -Password 'abc' y", []) == f"x -Password '{REDACTED}' y"
        assert redact_str("password=abc&x", []) == f"password={REDACTED}&x"
    print("[snapshot] selftest OK (cut-off, dedup, replays out, redaction + scan, deterministic files, unpack + "
          "verify, tamper + traversal refused, read-only repositories)")


def main(argv=None):
    import argparse
    from history import parse_time_ms
    ap = argparse.ArgumentParser(prog="snapshot.py", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--selftest", action="store_true")
    sub = ap.add_subparsers(dest="cmd")
    b = sub.add_parser("build", help="sessions/ -> snapshot .tar.gz")
    repo = os.path.dirname(os.path.dirname(HERE))
    b.add_argument("--sessions-dir", default=os.path.join(HERE, "sessions"))
    b.add_argument("--until", required=True, metavar="TIME",
                   help="keep windows that END by this time (ISO-8601, naive = UTC, or epoch ms)")
    b.add_argument("--from", dest="start", metavar="TIME", help="first window start (default: the first in history)")
    b.add_argument("--out", required=True, metavar="TAR_GZ")
    b.add_argument("--runs-dir", default=os.path.join(repo, "src", "artifacts", "runs"),
                   help="model folders, for the training hyperparameters shown in the UI")
    b.add_argument("--secrets-ini", default=os.path.join(repo, "src", "ansible", "proxmox.ini"),
                   help="redact the passwords set in this Ansible inventory")
    b.add_argument("--secret", action="append", default=[], help="another literal to redact (repeatable)")
    b.add_argument("--note", default=DEFAULT_NOTE, help="the text the demo UI shows about the data")
    a = ap.parse_args(argv)
    if a.selftest:
        selftest()
        return 0
    if a.cmd != "build":
        ap.print_help()
        return 1
    secrets = secrets_from_ini(a.secrets_ini) + a.secret + [os.environ.get("LAB_TOKEN", "")]
    build(a.sessions_dir, parse_time_ms(a.until), a.out, parse_time_ms(a.start), secrets, a.runs_dir, a.note, repo)
    return 0


if __name__ == "__main__":
    sys.exit(main())
