# DUP Market Radar — single operator readiness gate (ops-ready, not 48h claim).
#
# Runs: Native build (unless -SkipBuild) → radar_gate → pytest → smoke offline
#       → live smoke --evidence → short soak (hard_fails must be 0).
# Writes: data/evidence/READY_OPS.json
#
# Exit 0  = ops-ready for dry_run / watchlist / optional live after LIVE_MONEY.md
# Exit 1  = not ready (do not enable auto dry_run:false)
#
# Does NOT start 48h soak. Does NOT claim approx_48h_claim_ok.
# Not financial advice — see docs/DISCLAIMER.md / docs/OPS_READY.md
param(
    [switch]$SkipBuild,
    [int]$SoakCycles = 5,
    [double]$SoakIntervalSec = 60,
    [switch]$SkipLive
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

Write-Host "DUP Market Radar go_ready"
Write-Host "Root: $Root"
Write-Host "Honesty: PASS != 48h soak claim. Live capital still needs LIVE_MONEY.md."
Write-Host ""

$env:PYTHONPATH = "src"
if ($SkipLive) {
    $env:RADAR_VERIFY_SKIP_LIVE = "1"
}

& (Join-Path $Root "scripts\verify.ps1") -Profile PreSoak -SkipBuild:$SkipBuild `
    -SoakCycles $SoakCycles -SoakIntervalSec $SoakIntervalSec
if ($LASTEXITCODE -ne 0) {
    Write-Host ""
    Write-Host "GO_READY: FAIL (verify PreSoak)"
    exit $LASTEXITCODE
}

$py = Join-Path $Root ".venv\Scripts\python.exe"
if (-not (Test-Path $py)) { $py = "python" }

$writer = @'
import json
import time
from pathlib import Path

root = Path(".")
ev = root / "data" / "evidence"
ev.mkdir(parents=True, exist_ok=True)
soak_path = ev / "LATEST_SOAK.json"
soak = {}
if soak_path.is_file():
    soak = json.loads(soak_path.read_text(encoding="utf-8"))
hard = int(soak.get("hard_fails") or 0)
claim48 = bool(soak.get("approx_48h_claim_ok"))
cycles = int(soak.get("cycles_ran") or soak.get("cycles") or 0)
ok = hard == 0 and cycles > 0
payload = {
    "product": "DUP Market Radar",
    "pack_kind": "ops_ready",
    "generated_at_utc": time.strftime("%Y%m%dT%H%M%SZ", time.gmtime()),
    "ops_ready": ok,
    "short_soak_hard_fails": hard,
    "short_soak_cycles": cycles,
    "approx_48h_claim_ok": claim48,
    "disclaimer": (
        "Ops-ready means PreSoak PASS + short soak hard_fails=0. "
        "Not a 48h soak claim. Not financial advice. "
        "Live capital requires docs/LIVE_MONEY.md operator steps."
    ),
    "next": {
        "run": ".\\scripts\\run_radar.ps1",
        "live_checklist": "docs/LIVE_MONEY.md",
        "ops_ready_doc": "docs/OPS_READY.md",
        "optional_48h": ".\\scripts\\verify.ps1 -Profile Soak48h",
    },
}
out = ev / "READY_OPS.json"
out.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
print(json.dumps(payload, indent=2))
raise SystemExit(0 if ok else 1)
'@

$tmp = Join-Path $env:TEMP "radar_go_ready_write.py"
Set-Content -Path $tmp -Value $writer -Encoding UTF8
& $py $tmp
$rc = $LASTEXITCODE
Remove-Item $tmp -Force -ErrorAction SilentlyContinue

Write-Host ""
if ($rc -eq 0) {
    Write-Host "GO_READY: PASS — data/evidence/READY_OPS.json"
    Write-Host "Start: .\scripts\run_radar.ps1"
    Write-Host "Live money: docs/LIVE_MONEY.md (dry_run stays true until you change it)"
    Write-Host "48h claim: still separate — only if Soak48h pack approx_48h_claim_ok=true"
} else {
    Write-Host "GO_READY: FAIL — see short soak hard_fails in LATEST_SOAK.json"
}
exit $rc
