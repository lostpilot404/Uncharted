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
@{check_updates=$false; ui_language='en'} | ConvertTo-Json |
    Set-Content (Join-Path $env:APPDATA 'uncharted-launcher/settings.json')
$os = Get-CimInstance Win32_OperatingSystem
$result = [ordered]@{
    source_sha=$metadata.source_sha; zip_sha256=(Get-FileHash $Zip -Algorithm SHA256).Hash.ToLower();
    os=$os.Caption; build=$os.BuildNumber; architecture=$env:PROCESSOR_ARCHITECTURE;
    clean_extraction='PASS'; hashes='PASS'; launcher='NOT_RUN'; runtime_controller_probe='NOT_RUN';
    manual_testing='NOT_PERFORMED'; gameplay='BLOCKED: no game dump or suitable GPU supplied'
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
