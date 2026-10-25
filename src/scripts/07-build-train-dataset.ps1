#Requires -RunAsAdministrator
<#
.SYNOPSIS
  07-build-train-dataset.ps1 - one benign TRAIN experiment, end to end, into its own folder
  (NT2102 / OCR-APT POC, SimulatedW10 ETW lab VM).

.DESCRIPTION
  1. preflight   interactive desktop session, no other capture running (the always-on live capture is paused automatically), tools present, workload self-test
                 (explorer.exe must be the launcher, otherwise benign apps get powershell.exe as parent)
  2. capture     etwctl start -Split train -Run x-<UTC>  ->  settle  ->  06-benign-workload.ps1
                 -> etwctl stop (called from the workload's -BeforeLedger hook, so the ledger write
                 happens AFTER the capture and is not in the data)
  3. collect     copy the run's raw JSON into  <exp>\raw\train\x-<UTC>\  (Alloy has shipped it to Loki)
  4. convert     etw2nodlink.py on <exp>\raw only   ->  <exp>\converted\  (un-windowed, + _summary.json)
  5. window      etwwindow.py                        ->  <exp>\
                   SimulatedW10_train_edges.csv        pooled windows, node IDs prefixed w0007_  <- host: encode_to_features.py
                   SimulatedW10_train_node_attrs.csv   same prefixes
                   windows.csv, manifest.json          per-window counts, parameters, totals
  6. check       check_rdf_input.py: the pair meets transform_to_RDF.py's input contract -> <exp>\check.json
  7. record      <exp>\experiment.json: seed, durations, window params, tool hashes, totals, label source

  <exp> = C:\lab-out\etw\experiments\<UTC yyyyMMddTHHmmssZ>\ ; <exp>\labels\ holds the workload ledger.

.EXAMPLE
  .\07-build-train-dataset.ps1                              # 20-min workload, seed 42, 5-min windows / 2-min stride
  .\07-build-train-dataset.ps1 -Seed 7 -DurationMinutes 30
  .\07-build-train-dataset.ps1 -PostProcess C:\lab-out\etw\experiments\20261009T150000Z   # redo steps 4-7 only

.NOTES
  Run in an ELEVATED PowerShell inside the VM's RDP/console desktop (not vagrant powershell / WinRM).
  Elevation is needed for SilkService; the workload still starts apps through explorer.exe, so they run
  un-elevated with explorer as parent, like a real user.
  Needs etwwindow.py, installed by the win_collector Ansible role.
#>
[CmdletBinding()]
param(
    [ValidateRange(5, 1440)]  [int] $DurationMinutes = 20,
    [int] $Seed = 42,
    [ValidateRange(1, 120)]   [int] $WindowMinutes = 5,
    [ValidateRange(1, 120)]   [int] $StrideMinutes = 2,
    [ValidateRange(10, 3600)] [int] $MaxGapSeconds = 120,
    [ValidateRange(0, 600)]   [int] $SettleSeconds = 30,     # idle capture before the workload starts
    [ValidateRange(0, 600)]   [int] $ShipWaitSeconds = 15,   # after the stop: let Alloy push the tail to Loki
    [switch] $IncludeUserPowerShell,
    [switch] $AllowFallbackLauncher,                         # accept powershell.exe as app parent (not advised)
    [switch] $KeepWorkDir,                                   # default: empty Documents\work so every run starts equal
    [string] $ExperimentsRoot = 'C:\lab-out\etw\experiments',
    [string] $WorkloadScript = (Join-Path $PSScriptRoot '06-benign-workload.ps1'),
    [string] $PostProcess                                    # existing <exp> folder: run steps 4-7 only
)
$ErrorActionPreference = 'Stop'   # cmdlet errors throw. (No Set-StrictMode here: it would leak into etwctl.ps1.)

$Split     = 'train'
$LabDir    = 'C:\ProgramData\etwlab'
$EtwctlPs1 = Join-Path $LabDir 'etwctl.ps1'
$WinPy     = Join-Path $LabDir 'etwwindow.py'
$CheckPy   = Join-Path $LabDir 'check_rdf_input.py'
$Utf8      = New-Object System.Text.UTF8Encoding($false)    # UTF-8 without BOM (PS 5.1 Set-Content adds one)

$CheckTool = @'
"""check_rdf_input.py - verify a <graph>_<split>_{edges,node_attrs}.csv pair meets what
OCR-APT's transform_to_RDF.py (--dataset nodlink) and encode_to_PyG.py assume.

  python check_rdf_input.py --dir <folder> [--graph SimulatedW10] [--split train] [--out check.json]
  exit 0 = OK (warnings allowed), 1 = contract violated, 2 = files missing

Contract (repo refs = OCR-APT transform_to_RDF.py / encode_to_PyG.py):
  edges   header source-id,source-type,destination-id,destination-type,edge-type,timestamp
          (pd.read_csv with header, l.82; column names used l.89, l.219-227, l.253-260)
  attrs   header node,node_type,node_attr (l.158-162; node_type only read with --adjust-uuid)
  ids, types, edge types are pasted into IRIs: prefix + "node/" + id, prefix + edge-type (l.134, l.222)
          -> no whitespace or IRI-illegal characters
  node_attr is pasted between double quotes into N-Triples (l.162, l.236-237) -> no " tab or newline
          (backslashes are fine: the .nt writer doubles them, l.403-404)
  timestamp integer epoch MILLISECONDS for SimulatedW10 (encode_to_PyG.py l.167-168 divides gaps by 1000)
  one type per node id, or get_all_nodes_df (l.253-260) emits two node-type triples for it
Stdlib only.
"""
import argparse, collections, csv, json, os, re, sys
from datetime import datetime, timezone

EDGE_HEADER = ["source-id", "source-type", "destination-id", "destination-type", "edge-type", "timestamp"]
NODE_HEADER = ["node", "node_type", "node_attr"]
IRI_BAD = re.compile(r'[\s<>"{}|^`\\]')          # chars not allowed raw inside an IRI
MS_LO, MS_HI = 946684800000, 4102444800000      # 2000-01-01 .. 2100-01-01 in epoch ms
MIN_NODE_REPR = 5                               # transform_to_RDF --min-node-representation default (l.20)


def check(d, graph, split):
    edges = os.path.join(d, f"{graph}_{split}_edges.csv")
    attrs = os.path.join(d, f"{graph}_{split}_node_attrs.csv")
    for p in (edges, attrs):
        if not os.path.isfile(p):
            return {"ok": False, "errors": [f"missing {p}"], "warnings": []}, 2
    err, warn = collections.Counter(), collections.Counter()
    ex = {}                                     # first example per problem

    def bad(kind, sample, hard=True):
        (err if hard else warn)[kind] += 1
        ex.setdefault(kind, sample)

    node_type, edge_types = {}, collections.Counter()
    ts_min, ts_max, n_edges = None, None, 0
    with open(edges, newline="", encoding="utf-8") as fh:
        r = csv.reader(fh)
        hdr = next(r, None)
        if hdr != EDGE_HEADER:
            return {"ok": False, "errors": [f"edges header {hdr} != {EDGE_HEADER}"], "warnings": []}, 1
        for row in r:
            n_edges += 1
            if len(row) != 6:
                bad("edge_row_width", row); continue
            s, st, o, ot, et, ts = row
            for nid, nt in ((s, st), (o, ot)):
                if not nid or IRI_BAD.search(nid):
                    bad("node_id_not_iri_safe", nid)
                if not nt or IRI_BAD.search(nt):
                    bad("node_type_not_iri_safe", nt)
                prev = node_type.setdefault(nid, nt)
                if prev != nt:
                    bad("node_has_two_types", f"{nid}: {prev} / {nt}")
            et_last = et.split("/")[-1]         # SimulatedW10: edge-type split on '/' (l.89-90)
            if not et_last or IRI_BAD.search(et_last):
                bad("edge_type_not_iri_safe", et)
            edge_types[et_last] += 1
            try:
                t = int(ts)
            except ValueError:
                bad("timestamp_not_int", ts); continue
            if not MS_LO <= t <= MS_HI:
                bad("timestamp_not_epoch_ms", ts); continue
            ts_min = t if ts_min is None else min(ts_min, t)
            ts_max = t if ts_max is None else max(ts_max, t)

    attr_nodes, n_attrs = set(), 0
    with open(attrs, newline="", encoding="utf-8") as fh:
        r = csv.reader(fh)
        hdr = next(r, None)
        if hdr != NODE_HEADER:
            bad("attrs_header", hdr)
        else:
            for row in r:
                n_attrs += 1
                if len(row) != 3:
                    bad("attr_row_width", row); continue
                n, nt, a = row
                if n in attr_nodes:
                    bad("attr_duplicate_node", n, hard=False)
                attr_nodes.add(n)
                if any(c in a for c in '"\t\r\n'):
                    bad("attr_breaks_ntriples", a)
                if n in node_type and node_type[n] != nt:
                    bad("attr_type_differs_from_edges", f"{n}: {node_type[n]} / {nt}")
                if n not in node_type:
                    bad("attr_for_node_without_edges", n, hard=False)

    missing = [n for n in node_type if n not in attr_nodes]
    if missing:
        warn["node_without_attr"] += len(missing)
        ex.setdefault("node_without_attr", missing[0])
    type_counts = collections.Counter(node_type.values())
    for t, c in type_counts.items():
        if c <= MIN_NODE_REPR:                  # would be dropped by get_remove_nodeType_lst (l.266-288)
            bad(f"node_type_rare:{t}", c, hard=False)

    def utc(ms):
        return None if ms is None else datetime.fromtimestamp(ms / 1000, tz=timezone.utc).isoformat()

    res = {
        "ok": not err, "dir": d, "graph": graph, "split": split,
        "edges": n_edges, "attr_rows": n_attrs, "nodes": len(node_type),
        "node_types": dict(type_counts.most_common()), "edge_types": dict(edge_types.most_common()),
        "time_utc": [utc(ts_min), utc(ts_max)],
        "minutes": None if ts_min is None else round((ts_max - ts_min) / 60000, 1),
        "errors": dict(err), "warnings": dict(warn), "examples": {k: str(v)[:200] for k, v in ex.items()},
    }
    return res, (0 if not err else 1)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--dir", required=True)
    ap.add_argument("--graph", default="SimulatedW10")
    ap.add_argument("--split", default="train")
    ap.add_argument("--out")
    a = ap.parse_args(argv)
    res, code = check(a.dir, a.graph, a.split)
    txt = json.dumps(res, indent=1)
    if a.out:
        with open(a.out, "w", encoding="utf-8") as fh:
            fh.write(txt)
    print(json.dumps({k: res.get(k) for k in ("ok", "edges", "nodes", "minutes", "errors", "warnings")}))
    return code


if __name__ == "__main__":
    sys.exit(main())
'@

function Log([string]$m) { Write-Host ('[07 {0:HH:mm:ss}] {1}' -f (Get-Date), $m) }
function Utc-Now { (Get-Date).ToUniversalTime().ToString('o') }
function Write-Text([string]$Path, [string]$Text) { [IO.File]::WriteAllText($Path, $Text, $Utf8) }
function Write-Json([string]$Path, $Obj) { Write-Text $Path ($Obj | ConvertTo-Json -Depth 8) }
function Sha([string]$Path) { if (Test-Path $Path) { (Get-FileHash $Path -Algorithm SHA256).Hash.ToLower() } else { $null } }

# Run a native program; throw unless its exit code is in $Ok. (Like `cmd || exit` in bash.)
function Invoke-Native([string]$Exe, [string[]]$ArgList, [int[]]$Ok = @(0)) {
    $global:LASTEXITCODE = 0
    & $Exe @ArgList | Out-Host                    # show the tool's output; keep only the exit code as return value
    if ($Ok -notcontains $LASTEXITCODE) { throw "$([IO.Path]::GetFileName($Exe)) exited with $LASTEXITCODE" }
    return $LASTEXITCODE
}

# ---------------------------------------------------------------- config + tools
$Cfg = Get-Content (Join-Path $LabDir 'etwlab.json') -Raw | ConvertFrom-Json   # written by the win_collector role
foreach ($p in @($Cfg.Python, $Cfg.Converter, $EtwctlPs1)) {
    if (-not (Test-Path $p)) { throw "missing $p - re-run windows.yml (win_collector)" }
}
if (-not (Test-Path $WinPy)) { throw "missing $WinPy - run '05-collect-10-minutes-window.ps1 -WindowOnly' once to install it" }
# install/refresh the checker only when its content changed (keeps the file's hash stable across runs)
if (-not (Test-Path $CheckPy) -or [IO.File]::ReadAllText($CheckPy) -ne $CheckTool) { Write-Text $CheckPy $CheckTool }
$Graph = $Cfg.Graph

# ---------------------------------------------------------------- steps 4-7 (also used by -PostProcess)
function Complete-Experiment([string]$Exp, [System.Collections.IDictionary]$Record) {
    $conv = Join-Path $Exp 'converted'
    Log "convert  $Exp\raw -> $conv"
    Invoke-Native $Cfg.Python @($Cfg.Converter, '--raw-root', (Join-Path $Exp 'raw'), '--split', $Split,
                                '--graph', $Graph, '--out', $conv) | Out-Null   # (Out-Null drops the returned 0)

    # etwwindow swaps its whole --out folder in atomically, so give it a private folder, never <exp> itself
    $stage = Join-Path $Exp '_windowing'
    Log "window   ${WindowMinutes}-min windows, ${StrideMinutes}-min stride, max gap ${MaxGapSeconds}s"
    $rc = Invoke-Native $Cfg.Python @($WinPy, '--indir', $conv, '--graph', $Graph, '--split', $Split,
            '--window-min', $WindowMinutes, '--stride-min', $StrideMinutes, '--max-gap-s', $MaxGapSeconds,
            '--out', $stage, '--force') -Ok @(0, 2)
    if ($rc -eq 2) { throw "no full $WindowMinutes-min window in the capture (see $conv\${Graph}_${Split}_summary.json)" }
    Get-ChildItem $stage -File | Move-Item -Destination $Exp -Force
    Remove-Item $stage -Recurse -Force

    Log 'check    transform_to_RDF input contract'
    $rc = Invoke-Native $Cfg.Python @($CheckPy, '--dir', $Exp, '--graph', $Graph, '--split', $Split,
                                      '--out', (Join-Path $Exp 'check.json')) -Ok @(0, 1)
    $man = Get-Content (Join-Path $Exp 'manifest.json') -Raw | ConvertFrom-Json
    $t = $man.totals
    $Record.windowing = [ordered]@{ window_min = $WindowMinutes; stride_min = $StrideMinutes; max_gap_s = $MaxGapSeconds }
    $Record.results = [ordered]@{
        windows = $t.windows; edges_in = $t.edges_in; edges_out = $t.edges_out
        edges_in_no_window = $t.edges_in_no_window; nodes_unique = $t.nodes_unique
        node_window_copies = $t.node_window_copies; contract_ok = ($rc -eq 0)
    }
    $Record.tools = [ordered]@{
        workload = Sha $WorkloadScript; etwctl = Sha $EtwctlPs1; converter = Sha $Cfg.Converter
        etwwindow = Sha $WinPy; checker = Sha $CheckPy; orchestrator = Sha $PSCommandPath
    }
    $Record.postprocessed_utc = Utc-Now
    Write-Json (Join-Path $Exp 'experiment.json') $Record
    if ($rc -ne 0) { throw "contract check FAILED - see $Exp\check.json (files kept for inspection)" }
    Log ("done     {0} windows, {1} edges, {2} node copies -> {3}" -f $t.windows, $t.edges_out, $t.node_window_copies, $Exp)
    Log "next     on the trainer: lab loop (Alloy has shipped this capture to Loki)"
}

if ($PostProcess) {
    $exp = (Resolve-Path $PostProcess).Path
    $recPath = Join-Path $exp 'experiment.json'
    $rec = [ordered]@{}
    if (Test-Path $recPath) {
        $old = Get-Content $recPath -Raw | ConvertFrom-Json
        foreach ($pr in $old.PSObject.Properties) { $rec[$pr.Name] = $pr.Value }   # PSCustomObject -> hashtable
        # reuse the experiment's window parameters unless given explicitly on this command line
        if ($old.PSObject.Properties['windowing']) {
            if (-not $PSBoundParameters.ContainsKey('WindowMinutes')) { $WindowMinutes = $old.windowing.window_min }
            if (-not $PSBoundParameters.ContainsKey('StrideMinutes')) { $StrideMinutes = $old.windowing.stride_min }
            if (-not $PSBoundParameters.ContainsKey('MaxGapSeconds')) { $MaxGapSeconds = $old.windowing.max_gap_s }
        }
    }
    Complete-Experiment $exp $rec
    return
}

# ---------------------------------------------------------------- 1. preflight (nothing goes into the train capture yet)
if ((Get-Process -Id $PID).SessionId -eq 0) { throw 'session 0 (service/WinRM): run this from the RDP or console desktop' }
if (-not (Test-Path $WorkloadScript)) { throw "workload script not found: $WorkloadScript" }
if (-not (Get-Command $WorkloadScript).Parameters.ContainsKey('BeforeLedger')) {
    throw "$WorkloadScript has no -BeforeLedger parameter - apply the 2-line patch first"
}
$svc = Get-Service $Cfg.ServiceName
# The always-on live capture (start-live.ps1 watchdog, test/live-*) is paused below, right before the train
# capture starts. Any other running capture (train, or a hand-started test run) still stops us here.
$StateFile = Join-Path $LabDir 'state.json'
$HoldFile  = Join-Path $LabDir 'live.hold'
$cur = if (Test-Path $StateFile) { Get-Content $StateFile -Raw | ConvertFrom-Json } else { $null }
$liveRunning = $svc.Status -eq 'Running' -and $cur -and $cur.split -eq 'test' -and "$($cur.run)" -like 'live-*'
if ($svc.Status -ne 'Stopped' -and -not $liveRunning) {
    throw "$($Cfg.ServiceName) is $($svc.Status): another capture is running ('etwctl stop' first)"
}
$expected = [math]::Floor(($DurationMinutes - $WindowMinutes) / $StrideMinutes) + 1
if ($expected -lt 1) { throw "-DurationMinutes $DurationMinutes is shorter than one $WindowMinutes-min window" }

Log 'preflight workload self-test (spawns and closes notepad; not captured)'
$st = & $WorkloadScript -SelfTest | Select-Object -Last 1
$st | Format-List | Out-Host
if ($st.LaunchMode -ne 'explorer-com' -or $st.NotepadParent -ne 'explorer') {
    if (-not $AllowFallbackLauncher) { throw 'apps would not be launched by explorer.exe (see self-test above); fix it or pass -AllowFallbackLauncher' }
    Write-Warning 'continuing with the fallback launcher: benign apps will have powershell.exe as parent'
}
if (-not $st.InteractiveSess) { throw 'self-test says the session is not interactive' }

$workDir = Join-Path $env:USERPROFILE 'Documents\work'
if (-not $KeepWorkDir -and (Test-Path $workDir)) {
    Log "reset    $workDir (same starting state for every experiment)"
    Get-ChildItem $workDir -Force | Remove-Item -Recurse -Force
}

$stamp = (Get-Date).ToUniversalTime().ToString('yyyyMMddTHHmmssZ')
$exp   = Join-Path $ExperimentsRoot $stamp
$run   = "x-$stamp"
New-Item -ItemType Directory -Force -Path (Join-Path $exp 'labels') | Out-Null
$rec = [ordered]@{
    id = $stamp; graph = $Graph; split = $Split; os_build = [Environment]::OSVersion.Version.ToString()
    label_source = 'benign by construction: scripted workload, no attack executed; includes background OS activity'
    capture = [ordered]@{ run = $run; settle_s = $SettleSeconds; started_utc = $null; stopped_utc = $null }
    workload = [ordered]@{ script = $WorkloadScript; seed = $Seed; duration_min = $DurationMinutes
                           include_user_powershell = [bool]$IncludeUserPowerShell; launch_mode = $st.LaunchMode
                           ledger_dir = (Join-Path $exp 'labels') }
    expected_windows = $expected
}
Write-Json (Join-Path $exp 'experiment.json') $rec      # written BEFORE the capture starts
Log "experiment $exp (expect about $expected windows)"

# ---------------------------------------------------------------- 2. capture
# A closure over a hashtable: the workload calls it from its own scope, and both sides see the same
# hashtable object, so "stopped" is shared and the stop runs exactly once.
$Cap = @{ Etwctl = $EtwctlPs1; Started = $false; Stopped = $false; StoppedUtc = $null }
$stopCapture = {
    if ($Cap.Started -and -not $Cap.Stopped) {
        & $Cap.Etwctl stop | Out-Host
        $Cap.Stopped = $true
        $Cap.StoppedUtc = (Get-Date).ToUniversalTime().ToString('o')
    }
}.GetNewClosure()

# Hold file: the start-live.ps1 watchdog (SYSTEM task, every few minutes) leaves SilkService alone while it
# exists. Written BEFORE the live run is stopped, so a watchdog tick cannot restart live in between; removed
# in the finally below, so live resumes within one tick after the train capture. Our $PID lets the watchdog
# drop the hold if this PowerShell dies without running the finally.
$hold = [ordered]@{ pid = $PID; by = '07-build-train-dataset.ps1'; since_utc = (Utc-Now) }
Set-Content -Path $HoldFile -Value ($hold | ConvertTo-Json -Compress)
try {
    if ((Get-Service $Cfg.ServiceName).Status -ne 'Stopped') {
        Log 'pause    the always-on live capture (start-live.ps1 resumes it after this capture)'
        & $EtwctlPs1 stop | Out-Host
    }
    $rec.capture.started_utc = Utc-Now
    $Cap.Started = $true                                 # set first: a half-started capture is still stopped
    $global:LASTEXITCODE = 0
    & $EtwctlPs1 start -Split $Split -Run $run
    if ($LASTEXITCODE) { throw "etwctl start failed ($LASTEXITCODE)" }
    Log "settle   ${SettleSeconds}s idle capture"
    Start-Sleep -Seconds $SettleSeconds
    $wl = @{ DurationMinutes = $DurationMinutes; Seed = $Seed; LedgerDir = (Join-Path $exp 'labels')
             BeforeLedger = $stopCapture }               # hashtable splat = named arguments
    if ($IncludeUserPowerShell) { $wl.IncludeUserPowerShell = $true }
    & $WorkloadScript @wl
}
finally {                                                # also runs on Ctrl+C or any error
    & $stopCapture
    $rec.capture.stopped_utc = $Cap.StoppedUtc
    Write-Json (Join-Path $exp 'experiment.json') $rec
    Remove-Item $HoldFile -Force -ErrorAction SilentlyContinue   # live capture resumes at the next watchdog tick
}

# ---------------------------------------------------------------- 3. collect raw into the experiment
$src = Join-Path $Cfg.RawRoot "$Split\$run"
$dst = Join-Path $exp "raw\$Split\$run"
if (-not (Test-Path $src)) { throw "raw run folder not found: $src" }
Log "collect  $src -> $dst"
# COPY, not move: Grafana Alloy tails $src and ships it to Loki (the training source on the trainer); it
# needs a few seconds after the stop for the last lines, and moving files it holds open would fail.
Log "wait     ${ShipWaitSeconds}s for Alloy to ship the last lines to Loki"
Start-Sleep -Seconds $ShipWaitSeconds
# robocopy /E = cp -r (keep the source for Alloy); exit codes 0-7 mean success
Invoke-Native 'robocopy.exe' @($src, $dst, '/E', '/NFL', '/NDL', '/NJH', '/NJS', '/NP') -Ok (0..7) | Out-Null
$rec.capture.raw = $dst
Write-Json (Join-Path $exp 'experiment.json') $rec

# ---------------------------------------------------------------- 4-7
Complete-Experiment $exp $rec