[CmdletBinding()]
param([string]$Zip = 'dist/uncharted-windows.zip', [string]$Results = 'out/package-launch')
$ErrorActionPreference = 'Stop'
$PSNativeCommandUseErrorActionPreference = $false
New-Item -ItemType Directory -Force $Results | Out-Null
$Results = (Resolve-Path $Results).Path
$clean = Join-Path $Results 'clean-extraction'
if (Test-Path $clean) { throw 'Refusing to overwrite a prior clean-extraction test' }
Expand-Archive -LiteralPath $Zip -DestinationPath $clean
$package = Join-Path $clean 'uncharted-windows'
$metadata = Get-Content (Join-Path $package 'BUILD-METADATA.json') -Raw | ConvertFrom-Json
foreach ($file in $metadata.files) {
    $path = Join-Path $package $file.path
    if ((Get-Item $path).Length -ne $file.bytes -or
        (Get-FileHash $path -Algorithm SHA256).Hash -ine $file.sha256) {
        throw "Extracted artifact integrity failure: $($file.path)"
    }
}
# Keep the clean runner's real profile and the user's future settings out of this check.
$env:APPDATA = Join-Path $Results 'appdata'
$env:BB_DATA_DIR = Join-Path $Results 'data'
$env:PATH = (Join-Path $package 'bin') + ';' + $env:PATH
New-Item -ItemType Directory -Force (Join-Path $env:APPDATA 'uncharted-launcher') | Out-Null
@{check_updates=$false; ui_language='en'; player_name='DIAGNOSTICS_PRIVATE_CANARY';
  game_dir='C:\PrivateDiagnosticFixture'; extra_env='API_TOKEN=DIAGNOSTICS_PRIVATE_ENV_CANARY'} | ConvertTo-Json |
    Set-Content (Join-Path $env:APPDATA 'uncharted-launcher/settings.json')
$os = Get-CimInstance Win32_OperatingSystem
$result = [ordered]@{
    source_sha=$metadata.source_sha; zip_sha256=(Get-FileHash $Zip -Algorithm SHA256).Hash.ToLower();
    os=$os.Caption; build=$os.BuildNumber; architecture=$env:PROCESSOR_ARCHITECTURE;
    clean_extraction='PASS'; hashes='PASS'; launcher='NOT_RUN'; runtime_controller_probe='NOT_RUN';
    diagnostics_export='NOT_RUN'; manual_testing='NOT_PERFORMED'; gameplay='BLOCKED: no game dump or suitable GPU supplied'
}
$process = $null
$failed = $false
try {
    # This is the real packaged GUI executable with no special smoke-test entry point.
    $process = Start-Process -FilePath (Join-Path $package 'Uncharted.exe') -WorkingDirectory $package `
        -RedirectStandardOutput (Join-Path $Results 'launcher.stdout.log') `
        -RedirectStandardError (Join-Path $Results 'launcher.stderr.log') -PassThru
    $timer = [Diagnostics.Stopwatch]::StartNew()
    while ($timer.Elapsed.TotalSeconds -lt 45) {
        $process.Refresh()
        if ($process.HasExited -or $process.MainWindowTitle -like 'Uncharted*') { break }
        Start-Sleep -Milliseconds 250
    }
    $process.Refresh()
    $result.window_title = $process.MainWindowTitle
    if ($process.HasExited) {
        $result.launcher='FAIL: exited before expected GUI'; $failed=$true
    } elseif ($process.MainWindowTitle -notlike 'Uncharted*') {
        $result.launcher='BLOCKED: expected GUI not observable in runner desktop session'
    } else {
        $result.launcher='PASS: expected real application window observed'
        $result.window_handle=$process.MainWindowHandle.ToInt64()
        if (-not $process.CloseMainWindow() -or -not $process.WaitForExit(15000)) {
            $result.launcher='FAIL: GUI did not close normally'; $failed=$true
        } else {
            $result.launcher_exit_code=$process.ExitCode
            if ($process.ExitCode -ne 0) { $result.launcher='FAIL: nonzero GUI exit'; $failed=$true }
        }
    }
    # Product CLI export uses the same snapshot/export path as the preview UI.
    # This is NOT a claim of human testing of the file chooser/preview interaction.
    $exportZip = Join-Path $Results 'diagnostics-export.zip'
    $exporter = Start-Process -FilePath (Join-Path $package 'Uncharted.exe') `
        -ArgumentList @('--export-diagnostics', ('"{0}"' -f $exportZip)) `
        -WorkingDirectory $package -PassThru `
        -RedirectStandardOutput (Join-Path $Results 'diagnostics.stdout.log') `
        -RedirectStandardError (Join-Path $Results 'diagnostics.stderr.log')
    if (-not $exporter.WaitForExit(20000)) {
        Stop-Process -Id $exporter.Id -Force
        throw 'Packaged diagnostic export timed out'
    }
    if ($exporter.ExitCode -ne 0) { throw "Diagnostic export exit: $($exporter.ExitCode)" }
    $archive = [IO.Compression.ZipFile]::OpenRead($exportZip)
    try {
        $report = @{}
        $total = 0
        foreach ($entry in $archive.Entries) {
            if ($entry.FullName -match '[/\\]' -or $entry.Length -gt 262144) {
                throw "Unsafe/oversized diagnostic member: $($entry.FullName)"
            }
            $stream = $entry.Open()
            $buffer = [IO.MemoryStream]::new()
            try { $stream.CopyTo($buffer); $bytes = $buffer.ToArray() }
            finally { $stream.Dispose(); $buffer.Dispose() }
            $total += $bytes.Length
            $report[$entry.FullName] = [ordered]@{
                bytes=$bytes.Length
                sha256=[Convert]::ToHexString([Security.Cryptography.SHA256]::HashData($bytes)).ToLowerInvariant()
                text=[Text.Encoding]::UTF8.GetString($bytes)
            }
        }
        if ($total -gt 2097152) { throw 'Diagnostic report exceeds uncompressed size cap' }
        foreach ($name in @('diagnostics.json','session.json','settings-redacted.json','launcher.log',
                            'previous-session.json','manifest.json','README.txt')) {
            if (-not $report.ContainsKey($name)) { throw "Missing diagnostics member: $name" }
        }
        $info = $report['diagnostics.json'].text | ConvertFrom-Json
        if ($info.source_sha -ne $metadata.source_sha -or $info.remote_telemetry -ne $false) {
            throw 'Diagnostic source provenance or offline policy mismatch'
        }
        $previous = $report['previous-session.json'].text | ConvertFrom-Json
        if ($previous.clean_shutdown -ne $true) { throw 'GUI normal shutdown was not persisted' }
        $manifest = $report['manifest.json'].text | ConvertFrom-Json
        foreach ($item in $manifest.files) {
            if ($report[$item.name].sha256 -ne $item.sha256 -or $report[$item.name].bytes -ne $item.bytes) {
                throw "Diagnostic manifest mismatch: $($item.name)"
            }
        }
        $text = ($report.Values | ForEach-Object { $_.text }) -join "`n"
        foreach ($private in @('DIAGNOSTICS_PRIVATE_CANARY','DIAGNOSTICS_PRIVATE_ENV_CANARY',
                               'PrivateDiagnosticFixture', $env:APPDATA)) {
            if ($text.Contains($private)) { throw 'Private fixture leaked into diagnostic report' }
        }
        $result.diagnostics_export='PASS: exact source, manifest hashes, shutdown history, privacy canaries, size limit'
    } finally { $archive.Dispose() }
    # Exercise the shipped runtime's actual native controller entry point, not a test binary.
    $probe = Start-Process -FilePath (Join-Path $package 'bin/bb-probe.exe') `
        -ArgumentList '--controller-info' -WorkingDirectory $package -PassThru `
        -RedirectStandardOutput (Join-Path $Results 'runtime.stdout.log') `
        -RedirectStandardError (Join-Path $Results 'runtime.stderr.log')
    if (-not $probe.WaitForExit(20000)) {
        Stop-Process -Id $probe.Id -Force
        $result.runtime_controller_probe='FAIL: timeout'; $failed=$true
    } else {
        $result.runtime_exit_code=$probe.ExitCode
        $result.runtime_controller_probe=if ($probe.ExitCode -eq 0) {'PASS'} else {'FAIL'}
        if ($probe.ExitCode -ne 0) { $failed=$true }
    }
} catch {
    $result.error = $_.Exception.ToString(); $failed=$true
} finally {
    if ($process) {
        $process.Refresh()
        if (-not $process.HasExited) { Stop-Process -Id $process.Id -Force; $result.forced_gui_cleanup=$true }
        elseif (-not $result.Contains('launcher_exit_code')) { $result.launcher_exit_code=$process.ExitCode }
    }
    $result | ConvertTo-Json -Depth 6 | Set-Content (Join-Path $Results 'native-launch-results.json')
    $message = ($result | ConvertTo-Json -Depth 6 -Compress).Replace('%','%25').Replace("`r",'%0D').Replace("`n",'%0A')
    Write-Output "::notice title=packaged-application-launch::$message"
}
if ($failed) { exit 1 }
