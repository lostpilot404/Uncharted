[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$Executable,
    [Parameter(Mandatory = $true)][string]$OutputDirectory,
    [switch]$Benchmark,
    [ValidateRange(1, 10)][int]$Repetitions = 3
)
$ErrorActionPreference = 'Stop'
# Expected simulated failures exit 3; PowerShell must not treat these as script exceptions.
$PSNativeCommandUseErrorActionPreference = $false
$Executable = (Resolve-Path $Executable).Path
New-Item -ItemType Directory -Force -Path $OutputDirectory | Out-Null
$OutputDirectory = (Resolve-Path $OutputDirectory).Path
$results = [System.Collections.Generic.List[object]]::new()
function Invoke-Probe([string]$Name, [string[]]$ProbeArgs, [int]$ExpectedExit, [string[]]$RequiredMarkers) {
    $log = Join-Path $OutputDirectory "$Name.log"
    Write-Host "RUN name=$Name executable=$Executable args=$($ProbeArgs -join ' ') expected_exit=$ExpectedExit"
    & $Executable @ProbeArgs 2>&1 | Tee-Object -FilePath $log | Out-Host
    $exitCode = $LASTEXITCODE
    $text = Get-Content -LiteralPath $log -Raw
    $ok = ($exitCode -eq $ExpectedExit)
    foreach ($marker in $RequiredMarkers) {
        if (-not $text.Contains($marker)) { $ok = $false; Write-Host "MISSING_MARKER $marker" }
    }
    if ($text.Contains('REAL_OS_FAILURE') -or $text.Contains('CHECK=FAIL') -or $text.Contains('FAIL_STOP')) { $ok = $false }
    $results.Add([pscustomobject]@{name=$Name; args=$ProbeArgs; expected_exit=$ExpectedExit; actual_exit=$exitCode; pass=$ok; log=$log})
    $results | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath (Join-Path $OutputDirectory 'results.json')
    if (-not $ok) { throw "Probe case '$Name' failed (exit $exitCode); full log: $log" }
}
Invoke-Probe 'basic' @() 0 @('RESULT=PASS mode=basic', 'ALIGNMENT=PASS', 'CASE_RESULT=PASS mode=single-interior', 'CASE_RESULT=PASS mode=multi-interior', 'TARGET_INVALIDATION=PASS', 'TARGET_BACKING=PASS', 'BACKING_DIFFERENT_OFFSET_REMAP=PASS')
foreach ($point in @('split:1', 'map:2', 'unmap:1', 'remap:0', 'coalesce:0')) {
    $parts = $point.Split(':')
    Invoke-Probe ('inject-' + $point.Replace(':', '-')) @('--inject', $point) 3 @(
        "SIMULATED_PRECALL_FAILURE stage=$($parts[0]) index=$($parts[1]) real_os_invoked=NO",
        "SIMULATION_CHECK=PASS stage=$($parts[0]) index=$($parts[1])",
        'RESULT=EXPECTED_SIMULATED_FAILURE', 'CLEANUP=PASS'
    )
}
if ($Benchmark) {
    Invoke-Probe 'benchmark' @('--benchmark', "$Repetitions") 0 @('BENCH leaves=4 ', 'BENCH leaves=64 ', 'BENCH leaves=256 ', 'BENCH leaves=1024 ', 'RESULT=PASS mode=benchmark')
}
Write-Host "RUNNER_RESULT=PASS cases=$($results.Count) logs=$OutputDirectory"
