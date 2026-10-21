param(
    [Parameter(Mandatory = $true)]
    [string]$ExePath,
    [int]$Port = 8770,
    [int]$Repeat = 3,
    [int]$TimeoutSeconds = 90
)

$ErrorActionPreference = "Stop"
$exe = (Resolve-Path $ExePath).Path
$directory = Split-Path $exe
$healthUrl = "http://127.0.0.1:$Port/_stcore/health"

function Measure-ProbeSeconds {
    $watch = [Diagnostics.Stopwatch]::StartNew()
    $output = & $exe probe
    if ($LASTEXITCODE -ne 0) {
        throw "probe failed with exit code $LASTEXITCODE"
    }
    if (($output | Out-String) -notmatch "bundled-sql-ok") {
        throw "probe output does not contain the bundled SQL result"
    }
    return [math]::Round($watch.Elapsed.TotalSeconds, 2)
}

function Get-ProcessTree([int]$rootId) {
    return Get-CimInstance Win32_Process |
        Where-Object { $_.ProcessId -eq $rootId -or $_.ParentProcessId -eq $rootId }
}

function Measure-ServeSeconds {
    if (Get-NetTCPConnection -State Listen -LocalPort $Port -ErrorAction SilentlyContinue) {
        throw "port $Port is already in use"
    }
    $watch = [Diagnostics.Stopwatch]::StartNew()
    $process = Start-Process $exe -ArgumentList "serve", $Port -PassThru -WindowStyle Hidden
    $seconds = -1
    while ($watch.Elapsed.TotalSeconds -lt $TimeoutSeconds -and -not $process.HasExited) {
        try {
            $response = Invoke-WebRequest $healthUrl -UseBasicParsing -TimeoutSec 2
            if ($response.StatusCode -eq 200) {
                $seconds = [math]::Round($watch.Elapsed.TotalSeconds, 2)
                break
            }
        } catch {
            Start-Sleep -Milliseconds 200
        }
    }
    $memory = 0
    foreach ($item in (Get-ProcessTree $process.Id)) {
        $running = Get-Process -Id $item.ProcessId -ErrorAction SilentlyContinue
        if ($running) {
            $memory += $running.WorkingSet64
        }
    }
    & taskkill /PID $process.Id /T /F | Out-Null
    Start-Sleep -Seconds 1
    if ($seconds -lt 0) {
        throw "serve did not become healthy within $TimeoutSeconds seconds"
    }
    return @{ Seconds = $seconds; MemoryMiB = [math]::Round($memory / 1MB, 1) }
}

$sizeBytes = (Get-ChildItem $directory -Recurse -File | Measure-Object Length -Sum).Sum
$probe = 1..$Repeat | ForEach-Object { Measure-ProbeSeconds }
$serve = 1..$Repeat | ForEach-Object { Measure-ServeSeconds }

[ordered]@{
    exe = $exe
    sizeMiB = [math]::Round($sizeBytes / 1MB, 1)
    probeSeconds = $probe
    serveHealthSeconds = @($serve | ForEach-Object { $_.Seconds })
    serveMemoryMiB = @($serve | ForEach-Object { $_.MemoryMiB })
} | ConvertTo-Json
