#Requires -RunAsAdministrator
<#
  Collector provisioner for the ETW lab VM (OCR-APT reproduction). Runs AFTER base.ps1 and tools.ps1.

  Sets up:
    1. Embeddable Python (pinned) in C:\Tools\python-embed\<ver>   - runs the converter, stdlib only
    2. C:\ProgramData\etwlab\{etw2nodlink.py, etwctl.ps1, etwlab.json} + C:\Tools\etwctl.cmd on PATH
    3. Windows service "SilkService" (SilkETW's service mode), startup = Manual
    4. Optionally starts a capture run (default: train) if the collector is not already running

  Output (synced to host ./out/etw):
    C:\lab-out\etw\raw\<split>\<run>\{kproc,kfile,knet}.json      raw SilkETW JSON lines
    C:\lab-out\etw\<Graph>_<split>_{edges,node_attrs}.csv           after 'etwctl convert -Split <split>'

  Safe to re-run: every file is only rewritten if its content changed; a running capture is never
  touched; Python is only downloaded once (and hash-checked when pinned).

  Vagrantfile:
    config.vm.provision "collector", type: "shell", path: "provision/collector.ps1"
    # args: "-StartSplit none"   to install without starting a capture
#>
param(
    [string]$PythonVersion   = "3.12.7",
    [string]$PythonZipSha256 = "",                 # pin after first run (value lands in lab-versions.json)
    [string]$Graph           = "SimulatedW10",     # repo asserts host in a fixed list -> reuse W10 name
    [string]$OutDir          = "C:\lab-out\etw",
    [string]$RawRoot         = "C:\lab-out\etw\raw",
    [ValidateSet("none", "train", "test")][string]$StartSplit = "train"
)

$ErrorActionPreference = "Stop"
$ProgressPreference    = "SilentlyContinue"
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12

$ToolsDir     = "C:\Tools"
$LabHome      = "C:\ProgramData\etwlab"
$VersionsFile = "$ToolsDir\lab-versions.json"
$ServiceName  = "SilkService"
$Utf8         = New-Object System.Text.UTF8Encoding($false)

# ---------- helpers ----------
function Write-IfChanged([string]$Path, [string]$Content) {
    # Returns $true if the file was (re)written. Unchanged files keep their timestamp.
    if ((Test-Path $Path) -and ([IO.File]::ReadAllText($Path) -eq $Content)) { return $false }
    [IO.File]::WriteAllText($Path, $Content, $Utf8)
    Write-Host "[collector] wrote $Path"
    return $true
}

function Save-File([string]$Url, [string]$OutFile) {
    if (Test-Path $OutFile) { Write-Host "[collector] cached: $OutFile"; return }
    Invoke-WebRequest $Url -OutFile "$OutFile.part" -UseBasicParsing
    Move-Item "$OutFile.part" $OutFile -Force
}

# ---------- 0. preconditions ----------
$silkSvcExe = Get-ChildItem "$ToolsDir\SilkETW" -Recurse -Filter "SilkService.exe" -ErrorAction SilentlyContinue |
              Select-Object -First 1
if (-not $silkSvcExe) { throw "[collector] SilkService.exe not found under $ToolsDir\SilkETW - run the tools provisioner first" }
if (-not (Test-Path "C:\lab-out")) { throw "[collector] C:\lab-out missing - is the synced folder configured?" }
foreach ($d in $LabHome, $OutDir, $RawRoot, "$ToolsDir\cache") { New-Item -ItemType Directory -Force -Path $d | Out-Null }

# ---------- 1. embeddable Python ----------
$PyDir = "$ToolsDir\python-embed\$PythonVersion"
$PyExe = "$PyDir\python.exe"
$zip   = "$ToolsDir\cache\python-$PythonVersion-embed-amd64.zip"
if (-not (Test-Path $PyExe)) {
    Save-File "https://www.python.org/ftp/python/$PythonVersion/python-$PythonVersion-embed-amd64.zip" $zip
    $h = (Get-FileHash $zip -Algorithm SHA256).Hash
    if ($PythonZipSha256 -and $h -ne $PythonZipSha256.ToUpper()) {
        Remove-Item $zip -Force
        throw "[collector] python zip SHA256 mismatch: got $h, expected $PythonZipSha256 (deleted, re-run)"
    }
    if (-not $PythonZipSha256) { Write-Warning "[collector] python zip not pinned; SHA256 = $h" }
    Expand-Archive $zip -DestinationPath $PyDir -Force
}
$pyVer = (& $PyExe --version 2>&1) -join " "
if ($pyVer -notmatch [regex]::Escape($PythonVersion)) { throw "[collector] unexpected python: $pyVer" }
Write-Host "[collector] $pyVer at $PyExe"

# ---------- 2. converter, control script, config ----------
$converter = @'
"""etw2nodlink.py - convert SilkETW JSON-lines captures into the OCR-APT / NODLINK CSV schema.

Input  : <raw-root>/<split>/<run>/*.json   (one SilkETW JSON object per line, one file per provider)
Output : <out>/<graph>_<split>_edges.csv       source-id,source-type,destination-id,destination-type,edge-type,timestamp
         <out>/<graph>_<split>_node_attrs.csv  node,node_type,node_attr
         <out>/<graph>_<split>_summary.json    counts, unmapped event names, unresolved lookups

Stdlib only: runs on the embeddable Python inside the VM, or on any Python >= 3.8 on the host.
Edge timestamps are epoch milliseconds (the repo divides W10 gaps by 1000, encode_to_PyG.py l.167-168).
"""
import argparse, csv, glob, hashlib, heapq, json, os, re, sys, tempfile
from collections import Counter
from datetime import datetime, timezone

EDGE_HEADER = ["source-id", "source-type", "destination-id", "destination-type", "edge-type", "timestamp"]
NODE_HEADER = ["node", "node_type", "node_attr"]

# Edge direction for process<->object edges. "fwd" = process -> object, "rev" = object -> process.
# [INFERENCE] NODLINK's convention is undocumented. Check it on the real data before trusting this:
#   awk -F, 'NR>1 {print $5, $2"->"$4}' SimulatedW10_train_edges.csv | sort | uniq -c
DIRECTION = {"Load": "fwd", "Read": "fwd", "Write": "fwd", "Send": "fwd", "Recv": "fwd"}

# The collector writes its own output file, so Kernel-File sees its writes: drop them (feedback loop).
EXCLUDE_IMAGES = {"silkservice"}

_FRAC = re.compile(r"(\.\d{6})\d+")


def num(v):
    """'4,321' / '0x10E1' / 4321 -> int; None if unparseable."""
    if v is None or v == "":
        return None
    if isinstance(v, (int, float)):
        return int(v)
    s = str(v).strip().replace(",", "")
    try:
        return int(s, 16) if s.lower().startswith("0x") else int(float(s))
    except ValueError:
        return None


def first(*vals):
    for v in vals:
        if v is not None:
            return v
    return None


def lower_keys(d):
    return {str(k).lower(): v for k, v in d.items()} if isinstance(d, dict) else {}


def ts_ms(s):
    """SilkETW TimeStamp (ISO 8601, 7 fractional digits) -> epoch ms. Naive = UTC (VM clock is UTC)."""
    s = _FRAC.sub(r"\1", str(s).strip().replace("Z", "+00:00"))
    dt = datetime.fromisoformat(s)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return int(dt.timestamp() * 1000)


def dos_device_map():
    """'\\device\\harddiskvolume2' -> 'c:' via QueryDosDeviceW. Empty map when not on Windows."""
    m = {}
    if os.name != "nt":
        return m
    import ctypes
    buf = ctypes.create_unicode_buffer(1024)
    for c in "ABCDEFGHIJKLMNOPQRSTUVWXYZ":
        if ctypes.windll.kernel32.QueryDosDeviceW(c + ":", buf, 1024):
            m[buf.value.lower()] = c.lower() + ":"
    return dict(sorted(m.items(), key=lambda kv: -len(kv[0])))


def classify(provider, name):
    """(ProviderName, EventName) -> internal kind, or None. Names verified only loosely: see summary.json."""
    p, n = provider.lower(), name.lower()
    if "kernel-process" in p:
        if "processstart" in n or n.startswith("process/start"):
            return "pstart"
        if "processstop" in n or n.startswith("process/stop"):
            return "pstop"
        if "imageload" in n or n.startswith("image/load"):
            return "load"
    elif "kernel-file" in p:
        if "namecreate" in n:
            return "fname"
        if re.search(r"(^|/)create($|/)", n):
            return "fcreate"
        if re.search(r"(^|/)read($|/)", n):
            return "read"
        if re.search(r"(^|/)write($|/)", n):
            return "write"
    elif "kernel-network" in p:
        if "send" in n or "sent" in n:
            return "send"
        if "recv" in n or "receiv" in n:
            return "recv"
    return None


def read_events(path, stats):
    """Yield (epoch_ms, event) from one SilkETW file. A truncated last line (killed service) is skipped."""
    with open(path, encoding="utf-8-sig", errors="replace") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                ev = json.loads(line)
                t = ts_ms(lower_keys(ev).get("timestamp"))
            except (ValueError, TypeError, AttributeError):
                stats["bad_lines"][os.path.basename(path)] += 1
                continue
            yield t, ev


class Graph:
    def __init__(self, writer, dmap, stats):
        self.w, self.dmap, self.st = writer, dmap, stats
        self.nodes = {}   # node id -> [node_type, node_attr]
        self.used = set()  # node ids referenced by a written edge

    def new_run(self, run):
        # PIDs and file objects are only meaningful inside one capture session.
        self.run, self.pid2proc, self.fobj, self.fkey = run, {}, {}, {}

    # ---- nodes ----
    def path(self, p):
        if not p:
            return ""
        s = str(p).strip().lower()
        if s.startswith("\\??\\"):
            s = s[4:]
        for dev, drive in self.dmap.items():
            if s.startswith(dev + "\\"):
                return drive + s[len(dev):]
        return s

    def add_node(self, nid, ntype, attr):
        cur = self.nodes.get(nid)
        if cur is None:
            self.nodes[nid] = [ntype, attr or ""]
        elif not cur[1] and attr:
            cur[1] = attr
        return nid

    def proc(self, pid, name_hint=""):
        if pid is None:
            return None
        nid = self.pid2proc.get(pid)
        if nid is None:  # process started before the capture: we never saw its ProcessStart
            nid = self.pid2proc[pid] = "p%d_pre_%s" % (pid, self.run)
        return self.add_node(nid, "PROCESS", name_hint)

    def obj(self, prefix, ntype, key):
        return self.add_node(prefix + hashlib.sha1(key.encode()).hexdigest()[:16], ntype, key)

    # ---- edges ----
    def edge(self, proc_id, other_id, etype, t):
        base = re.split(r"[\\/]", self.nodes[proc_id][1])[-1].lower()
        if base.endswith(".exe"):
            base = base[:-4]
        if base in EXCLUDE_IMAGES:
            self.st["excluded"][etype] += 1
            return
        if etype == "Start" or DIRECTION[etype] == "fwd":
            src, dst = proc_id, other_id
        else:
            src, dst = other_id, proc_id
        self.w.writerow([src, self.nodes[src][0], dst, self.nodes[dst][0], etype, t])
        self.used.update((src, dst))
        self.st["edges"][etype] += 1

    # ---- dispatch ----
    def handle(self, t, ev):
        e = lower_keys(ev)
        prov, name = str(e.get("providername") or ""), str(e.get("eventname") or "")
        kind = classify(prov, name)
        if kind is None:
            self.st["unmapped"][prov + "|" + name] += 1
            return
        self.st["events"][kind] += 1
        d = lower_keys(e.get("xmleventdata"))
        hpid, hname = num(e.get("processid")), str(e.get("processname") or "")
        getattr(self, "on_" + kind)(t, d, hpid, hname)

    def on_pstart(self, t, d, hpid, hname):
        cpid, ppid = num(d.get("processid")), num(d.get("parentprocessid"))
        if cpid is None:
            self.st["unresolved"]["pstart"] += 1
            return
        ppid = first(ppid, hpid)
        parent = self.proc(ppid, hname if ppid == hpid else "")
        if parent is None:
            self.st["unresolved"]["pstart"] += 1
            return
        child = self.add_node("p%d_%d" % (cpid, t), "PROCESS", self.path(d.get("imagename")))
        self.pid2proc[cpid] = child
        self.edge(parent, child, "Start", t)

    def on_pstop(self, t, d, hpid, hname):
        self.pid2proc.pop(first(num(d.get("processid")), hpid), None)

    def on_load(self, t, d, hpid, hname):
        pid = first(num(d.get("processid")), hpid)
        img = self.path(d.get("imagename"))
        p = self.proc(pid, hname if pid == hpid else "")
        if p is None or not img:
            self.st["unresolved"]["load"] += 1
            return
        self.edge(p, self.obj("f", "FILE", img), "Load", t)

    def on_fname(self, t, d, hpid, hname):
        key, p = str(d.get("filekey") or "").lower(), self.path(d.get("filename"))
        if key and p:
            self.fkey[key] = p

    def on_fcreate(self, t, d, hpid, hname):
        fo, p = str(d.get("fileobject") or "").lower(), self.path(d.get("filename"))
        if fo and p:
            self.fobj[fo] = p

    def _file_io(self, t, d, hpid, hname, etype):
        fo, key = str(d.get("fileobject") or "").lower(), str(d.get("filekey") or "").lower()
        p = self.fobj.get(fo) or self.fkey.get(key)
        proc = self.proc(hpid, hname)
        if not p or proc is None:  # file opened before the capture started, or no PID
            self.st["unresolved"][etype] += 1
            return
        self.edge(proc, self.obj("f", "FILE", p), etype, t)

    def on_read(self, t, d, hpid, hname):
        self._file_io(t, d, hpid, hname, "Read")

    def on_write(self, t, d, hpid, hname):
        self._file_io(t, d, hpid, hname, "Write")

    def _net(self, t, d, hpid, hname, etype):
        pid = first(num(d.get("pid")), hpid)
        daddr, dport = d.get("daddr"), num(d.get("dport"))
        proc = self.proc(pid, hname if pid == hpid else "")
        if proc is None or not daddr:
            self.st["unresolved"][etype] += 1
            return
        self.edge(proc, self.obj("n", "NET", "%s:%s" % (daddr, dport)), etype, t)

    def on_send(self, t, d, hpid, hname):
        self._net(t, d, hpid, hname, "Send")

    def on_recv(self, t, d, hpid, hname):
        self._net(t, d, hpid, hname, "Recv")


def convert(raw_root, split, graph, out_dir):
    split_dir = os.path.join(raw_root, split)
    runs = sorted(d for d in glob.glob(os.path.join(split_dir, "*")) if os.path.isdir(d))
    if not runs:
        raise SystemExit("no capture runs under " + split_dir)
    os.makedirs(out_dir, exist_ok=True)
    base = os.path.join(out_dir, "%s_%s" % (graph, split))
    st = {k: Counter() for k in ("events", "edges", "excluded", "unresolved", "unmapped", "bad_lines")}

    # Write to .tmp and rename at the end: a crashed run never leaves a half CSV that looks complete.
    with open(base + "_edges.csv.tmp", "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(EDGE_HEADER)
        g = Graph(w, dos_device_map(), st)
        for run in runs:
            g.new_run(os.path.basename(run))
            streams = [read_events(f, st) for f in sorted(glob.glob(os.path.join(run, "*.json")))]
            # Each provider file is time-ordered; merge them so PID -> process lookups see events in order.
            for t, ev in heapq.merge(*streams, key=lambda x: x[0]):
                g.handle(t, ev)

    with open(base + "_node_attrs.csv.tmp", "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(NODE_HEADER)
        for nid in sorted(g.used):
            ntype, attr = g.nodes[nid]
            w.writerow([nid, ntype, attr or "unknown"])

    os.replace(base + "_edges.csv.tmp", base + "_edges.csv")
    os.replace(base + "_node_attrs.csv.tmp", base + "_node_attrs.csv")
    node_types = Counter(g.nodes[n][0] for n in g.used)
    summary = {"graph": graph, "split": split, "runs": [os.path.basename(r) for r in runs],
               "nodes": dict(node_types), "edges": dict(st["edges"]),
               "events": dict(st["events"]), "excluded": dict(st["excluded"]),
               "unresolved": dict(st["unresolved"]), "bad_lines": dict(st["bad_lines"]),
               "unmapped_top": dict(st["unmapped"].most_common(40))}
    with open(base + "_summary.json", "w", encoding="utf-8") as fh:
        json.dump(summary, fh, indent=2)
    return summary


def selftest():
    """Synthetic 3-provider capture -> check the 4 expected edges, 1 exclusion, 1 unmapped event."""
    def ev(prov, name, pid, pname, ts, **data):
        return json.dumps({"ProviderName": prov, "EventName": name, "ProcessID": pid, "ProcessName": pname,
                           "TimeStamp": ts, "XmlEventData": data})
    kp, kf, kn = ("Microsoft-Windows-Kernel-" + x for x in ("Process", "File", "Network"))
    files = {
        "kproc.json": [
            ev(kp, "ProcessStart/Start", 100, "explorer", "2026-10-07T10:00:00.0000001+00:00",
               ProcessID="4,321", ParentProcessID="100", ImageName=r"\Device\HarddiskVolume2\Windows\System32\cmd.exe"),
            ev(kp, "ImageLoad", 4321, "cmd", "2026-10-07T10:00:00.0100000Z",
               ProcessID="4321", ImageName=r"\Device\HarddiskVolume2\Windows\System32\ntdll.dll"),
            ev(kp, "ThreadStart/Start", 4321, "cmd", "2026-10-07T10:00:00.0200000Z", ThreadID="1")],
        "kfile.json": [
            ev(kf, "Create", 4321, "cmd", "2026-10-07T10:00:01.0000000Z", FileObject="0xAA", FileName=r"C:\tmp\x.txt"),
            ev(kf, "Write", 4321, "cmd", "2026-10-07T10:00:01.5000000Z", FileObject="0xAA", FileKey="0xBB"),
            ev(kf, "Create", 999, "SilkService", "2026-10-07T10:00:02.0000000Z", FileObject="0xCC", FileName=r"C:\out\k.json"),
            ev(kf, "Write", 999, "SilkService", "2026-10-07T10:00:02.1000000Z", FileObject="0xCC")],
        "knet.json": [
            ev(kn, "KERNEL_NETWORK_TASK_TCPIP/Datasent.", 4, "System", "2026-10-07T10:00:03.0000000Z",
               PID="4321", daddr="93.184.216.34", dport="443", size="100")],
    }
    with tempfile.TemporaryDirectory() as tmp:
        run = os.path.join(tmp, "raw", "train", "run1")
        os.makedirs(run)
        for fn, lines in files.items():
            with open(os.path.join(run, fn), "w", encoding="utf-8") as fh:
                fh.write("\n".join(lines) + "\n{truncated-line")
        s = convert(os.path.join(tmp, "raw"), "train", "SelfTest", os.path.join(tmp, "out"))
        with open(os.path.join(tmp, "out", "SelfTest_train_edges.csv"), encoding="utf-8") as fh:
            rows = list(csv.reader(fh))
    ok = (s["edges"] == {"Start": 1, "Load": 1, "Write": 1, "Send": 1} and s["excluded"] == {"Write": 1}
          and sum(s["unmapped_top"].values()) == 1 and rows[0] == EDGE_HEADER and len(rows) == 5)
    print("selftest", "OK" if ok else "FAILED", json.dumps(s["edges"]))
    return 0 if ok else 1


def main(argv=None):
    ap = argparse.ArgumentParser(description="SilkETW JSON -> OCR-APT/NODLINK CSVs")
    ap.add_argument("--raw-root")
    ap.add_argument("--split", choices=["train", "test"])
    ap.add_argument("--graph", default="SimulatedW10")
    ap.add_argument("--out")
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args(argv)
    if a.selftest:
        return selftest()
    if not (a.raw_root and a.split and a.out):
        ap.error("--raw-root, --split and --out are required")
    s = convert(a.raw_root, a.split, a.graph, a.out)
    print(json.dumps({k: s[k] for k in ("runs", "nodes", "edges", "unresolved", "excluded")}, indent=1))
    return 0 if s["edges"] else 2


if __name__ == "__main__":
    sys.exit(main())
'@
$etwctl = @'
<#
  etwctl - start/stop SilkService captures and convert them to OCR-APT CSVs.
    etwctl start   -Split train|test [-Run <name>]   new capture run (never appends to an old one)
    etwctl stop                                      stop the collector
    etwctl status                                    service state + current run file sizes
    etwctl convert -Split train|test                 raw\<split>\*\*.json -> <Graph>_<split>_*.csv
  Settings come from C:\ProgramData\etwlab\etwlab.json (written by the collector provisioner).
#>
param(
    [Parameter(Mandatory = $true, Position = 0)][ValidateSet("start", "stop", "status", "convert")][string]$Command,
    [ValidateSet("train", "test")][string]$Split,
    [string]$Run
)
$ErrorActionPreference = "Stop"
$Home_  = "C:\ProgramData\etwlab"
$Cfg    = Get-Content "$Home_\etwlab.json" -Raw | ConvertFrom-Json
$State  = "$Home_\state.json"
$Utf8   = New-Object System.Text.UTF8Encoding($false)   # UTF-8 without BOM

function Get-State { if (Test-Path $State) { Get-Content $State -Raw | ConvertFrom-Json } else { $null } }
function Set-State($obj) { [IO.File]::WriteAllText($State, ($obj | ConvertTo-Json), $Utf8) }

function Show-Status {
    $svc = Get-Service $Cfg.ServiceName
    $st  = Get-State
    Write-Host "service : $($svc.Name) = $($svc.Status)"
    if ($st) {
        Write-Host "run     : $($st.split)/$($st.run)  started $($st.started_utc)  stopped $($st.stopped_utc)"
        Get-ChildItem $st.dir -Filter *.json -ErrorAction SilentlyContinue |
            ForEach-Object { "{0,-12} {1,10:N0} KB" -f $_.Name, ($_.Length / 1KB) } | Write-Host
    }
}

switch ($Command) {
    "start" {
        if (-not $Split) { throw "start needs -Split train or -Split test" }
        $svc = Get-Service $Cfg.ServiceName
        if ($svc.Status -ne "Stopped") { throw "collector is $($svc.Status). Run 'etwctl stop' first." }
        if (-not $Run) { $Run = (Get-Date).ToUniversalTime().ToString("yyyyMMdd'T'HHmmss'Z'") }
        $dir = Join-Path $Cfg.RawRoot "$Split\$Run"
        if (Test-Path $dir) { throw "run folder already exists: $dir (choose another -Run)" }
        New-Item -ItemType Directory -Force -Path $dir | Out-Null

        # SilkService reads SilkServiceConfig.xml from its own folder, once, at service start.
        $sb = New-Object System.Text.StringBuilder
        [void]$sb.AppendLine("<SilkServiceConfig>")
        foreach ($c in $Cfg.Collectors) {
            [void]$sb.AppendLine("  <ETWCollector>")
            [void]$sb.AppendLine("    <Guid>$($c.Guid)</Guid>")
            [void]$sb.AppendLine("    <CollectorType>user</CollectorType>")
            [void]$sb.AppendLine("    <ProviderName>$($c.Provider)</ProviderName>")
            [void]$sb.AppendLine("    <UserKeywords>$($c.Keywords)</UserKeywords>")
            [void]$sb.AppendLine("    <OutputType>file</OutputType>")
            [void]$sb.AppendLine("    <Path>$dir\$($c.Stem).json</Path>")
            [void]$sb.AppendLine("  </ETWCollector>")
        }
        [void]$sb.AppendLine("</SilkServiceConfig>")
        [IO.File]::WriteAllText((Join-Path $Cfg.SilkServiceDir "SilkServiceConfig.xml"), $sb.ToString(), $Utf8)

        Set-State ([ordered]@{ split = $Split; run = $Run; dir = $dir;
                               started_utc = (Get-Date).ToUniversalTime().ToString("o"); stopped_utc = $null })
        Start-Service $Cfg.ServiceName

        # Canary: a process start + image loads + file reads we know must show up.
        Start-Sleep -Seconds 5
        & cmd.exe /c "whoami > nul"
        $deadline = (Get-Date).AddSeconds(40)
        $need = $Cfg.Collectors | Where-Object { $_.Required } | ForEach-Object { "$dir\$($_.Stem).json" }
        do {
            Start-Sleep -Seconds 2
            $missing = @($need | Where-Object { -not ((Test-Path $_) -and (Get-Item $_).Length -gt 0) })
        } while ($missing.Count -gt 0 -and (Get-Date) -lt $deadline)
        if ($missing.Count -gt 0) {
            Write-Warning "no events yet in: $($missing -join ', ')"
            Write-Warning "check the service log in $($Cfg.SilkServiceDir) and the Application event log"
            exit 1
        }
        Write-Host "[etwctl] capturing $Split/$Run -> $dir"
        Show-Status
    }
    "stop" {
        Stop-Service $Cfg.ServiceName -ErrorAction SilentlyContinue
        $st = Get-State
        if ($st -and -not $st.stopped_utc) {
            $st.stopped_utc = (Get-Date).ToUniversalTime().ToString("o"); Set-State $st
        }
        Show-Status
    }
    "status" { Show-Status }
    "convert" {
        if (-not $Split) { throw "convert needs -Split train or -Split test" }
        $st = Get-State
        if ((Get-Service $Cfg.ServiceName).Status -ne "Stopped" -and $st -and $st.split -eq $Split) {
            throw "a $Split capture is still running; 'etwctl stop' first (files are still being written)"
        }
        & $Cfg.Python $Cfg.Converter --raw-root $Cfg.RawRoot --split $Split --graph $Cfg.Graph --out $Cfg.OutDir
        if ($LASTEXITCODE -ne 0) { throw "converter exited with $LASTEXITCODE (see $($Cfg.OutDir)\$($Cfg.Graph)_$($Split)_summary.json)" }
    }
}
'@
$cfg = [ordered]@{
    ServiceName    = $ServiceName
    SilkServiceDir = $silkSvcExe.DirectoryName
    RawRoot        = $RawRoot
    OutDir         = $OutDir
    Graph          = $Graph
    Python         = $PyExe
    Converter      = "$LabHome\etw2nodlink.py"
    # Keyword masks: verify against C:\Tools\provider-dumps\keywords-<provider>.txt (dumped by tools.ps1)
    Collectors     = @(
        [ordered]@{ Stem = "kproc"; Required = $true;  Guid = "6f1a2c10-0b7e-4c1d-9a51-3e1f00000001"
                    Provider = "Microsoft-Windows-Kernel-Process"; Keywords = "0x50"  }  # PROCESS|IMAGE
        [ordered]@{ Stem = "kfile"; Required = $true;  Guid = "6f1a2c10-0b7e-4c1d-9a51-3e1f00000002"
                    Provider = "Microsoft-Windows-Kernel-File";    Keywords = "0x390" }  # FILENAME|CREATE|READ|WRITE
        [ordered]@{ Stem = "knet";  Required = $false; Guid = "6f1a2c10-0b7e-4c1d-9a51-3e1f00000003"
                    Provider = "Microsoft-Windows-Kernel-Network"; Keywords = "0x30"  }  # IPV4|IPV6
    )
}
$changed  = Write-IfChanged "$LabHome\etw2nodlink.py" ($converter -replace "`r`n", "`n")
$changed  = (Write-IfChanged "$LabHome\etwctl.ps1"   $etwctl) -or $changed
$changed  = (Write-IfChanged "$LabHome\etwlab.json"  ($cfg | ConvertTo-Json -Depth 4)) -or $changed
$null     = Write-IfChanged "$ToolsDir\etwctl.cmd" "@powershell -NoProfile -ExecutionPolicy Bypass -File `"$LabHome\etwctl.ps1`" %*`r`n"

$machinePath = [Environment]::GetEnvironmentVariable("Path", "Machine")
if (($machinePath -split ";") -notcontains $ToolsDir) {
    [Environment]::SetEnvironmentVariable("Path", "$machinePath;$ToolsDir", "Machine")
    Write-Host "[collector] added $ToolsDir to machine PATH (new shells only)"
}

& $PyExe "$LabHome\etw2nodlink.py" --selftest
if ($LASTEXITCODE -ne 0) { throw "[collector] converter selftest failed" }

# ---------- 3. Windows service ----------
$svc = Get-Service $ServiceName -ErrorAction SilentlyContinue
$bin = $silkSvcExe.FullName
if ($bin -match " ") { $bin = "`"$bin`"" }   # quote only if needed (sc.exe + PS 5.1 mangle embedded quotes)
if (-not $svc) {
    New-Service -Name $ServiceName -BinaryPathName $bin -DisplayName "SilkETW collector (etwlab)" -StartupType Manual | Out-Null
    Write-Host "[collector] service $ServiceName created"
} else {
    $cur = (Get-CimInstance Win32_Service -Filter "Name='$ServiceName'").PathName
    if ($cur -ne $bin) {
        & sc.exe config $ServiceName binPath= $bin | Out-Null
        Write-Host "[collector] service $ServiceName binPath updated ($cur -> $bin)"
    }
    Set-Service $ServiceName -StartupType Manual
}
# Restart after a crash (30 s), but stay Manual: a reboot must not silently resume into an old run folder.
& sc.exe failure $ServiceName reset= 86400 actions= restart/30000/restart/30000/restart/30000 | Out-Null

# ---------- 4. start a capture (only if nothing is running) ----------
$svc = Get-Service $ServiceName
if ($StartSplit -eq "none") {
    Write-Host "[collector] StartSplit=none: not starting a capture"
} elseif ($svc.Status -ne "Stopped") {
    Write-Host "[collector] $ServiceName already $($svc.Status): leaving the current run alone"
    if ($changed) { Write-Warning "[collector] scripts/config changed; they apply from the next 'etwctl start'" }
} else {
    & powershell -NoProfile -ExecutionPolicy Bypass -File "$LabHome\etwctl.ps1" start -Split $StartSplit
    if ($LASTEXITCODE -ne 0) { throw "[collector] etwctl start failed (exit $LASTEXITCODE)" }
}

# ---------- 5. record versions ----------
$versions = if (Test-Path $VersionsFile) { Get-Content $VersionsFile -Raw | ConvertFrom-Json } else { New-Object PSObject }
$entry = [ordered]@{
    python_embed      = $PythonVersion
    python_zip_sha256 = if (Test-Path $zip) { (Get-FileHash $zip -Algorithm SHA256).Hash } else { $PythonZipSha256 }
    silkservice_exe   = $silkSvcExe.FullName
    converter_sha256  = (Get-FileHash "$LabHome\etw2nodlink.py" -Algorithm SHA256).Hash
    collectors        = $cfg.Collectors | ForEach-Object { "$($_.Provider) $($_.Keywords)" }
    updated_utc       = (Get-Date).ToUniversalTime().ToString("o")
}
$versions | Add-Member -NotePropertyName collector -NotePropertyValue $entry -Force
[IO.File]::WriteAllText($VersionsFile, ($versions | ConvertTo-Json -Depth 5), $Utf8)
Write-Host "[collector] done. Use: etwctl status | etwctl stop | etwctl start -Split test | etwctl convert -Split train"