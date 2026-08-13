[CmdletBinding()]
param(
    [switch]$DryRun,
    [ValidateRange(0, 65535)]
    [int]$BackendPort = 0,
    [ValidateRange(5, 120)]
    [int]$TimeoutSeconds = 30
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

if (($BackendPort -gt 0) -and ($BackendPort -lt 1024)) {
    throw "BackendPort must be 0 (automatic) or between 1024 and 65535."
}

$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$UiRoot = Join-Path $ProjectRoot "tiktok_bot_console\ui"
$LogRoot = Join-Path $ProjectRoot "data\logs"
$BackendPortCandidates = if ($BackendPort -gt 0) { @($BackendPort) } else { @(8000) + @(8400..8409) }
$FrontendPort = 5173
$FrontendUrl = "http://127.0.0.1:5173"
$FrontendRuntimeUrl = "$FrontendUrl/__tiktok-bot-runtime"
$FrontendAppId = "tiktok-b2b-bot-ui"

$DryRunBackendPort = if ($BackendPort -gt 0) { [string]$BackendPort } else { "<auto>" }
$BackendDisplayCommand = "python -m uvicorn tiktok_bot_api.main:app --env-file .env --port $DryRunBackendPort"
$FrontendDisplayCommand = "VITE_API_BASE=http://127.0.0.1:$DryRunBackendPort npm.cmd run dev -- --host 127.0.0.1 --port 5173 --strictPort"

if ($DryRun) {
    Write-Output "Backend port candidates: $($BackendPortCandidates -join ',')"
    Write-Output $BackendDisplayCommand
    Write-Output $FrontendDisplayCommand
    if ($BackendPort -gt 0) {
        Write-Output "http://127.0.0.1:$BackendPort/api/health"
    }
    else {
        Write-Output "Candidate default: python -m uvicorn tiktok_bot_api.main:app --env-file .env --port 8000"
        Write-Output "http://127.0.0.1:8000/api/health"
    }
    Write-Output $FrontendUrl
    Write-Output $FrontendRuntimeUrl
    exit 0
}

function Get-BackendBaseUrl {
    param([Parameter(Mandatory = $true)][int]$Port)
    return "http://127.0.0.1:$Port"
}

function Test-BackendService {
    param([Parameter(Mandatory = $true)][int]$Port)

    try {
        $baseUrl = Get-BackendBaseUrl -Port $Port
        $identity = Invoke-RestMethod -Uri "$baseUrl/" -TimeoutSec 2
        if ($identity.service -ne "TikTok B2B Bot API") {
            return $false
        }
        $health = Invoke-RestMethod -Uri "$baseUrl/api/health" -TimeoutSec 2
        return $health.status -eq "ok"
    }
    catch {
        return $false
    }
}

function Get-FrontendRuntime {
    try {
        $response = Invoke-WebRequest -UseBasicParsing -Uri $FrontendRuntimeUrl -TimeoutSec 2
        $contentType = [string]$response.Headers["Content-Type"]
        if (($response.StatusCode -ne 200) -or (-not $contentType.StartsWith("application/json"))) {
            return $null
        }
        $runtime = $response.Content | ConvertFrom-Json
        $propertyNames = @($runtime.PSObject.Properties.Name)
        if (($runtime.appId -ne $FrontendAppId) -or (-not ($propertyNames -contains "apiBase"))) {
            return $null
        }
        if ([string]::IsNullOrWhiteSpace([string]$runtime.apiBase)) {
            return $null
        }
        return $runtime
    }
    catch {
        return $null
    }
}

function Test-FrontendApiTarget {
    param([Parameter(Mandatory = $true)][string]$ExpectedApiBase)

    $runtime = Get-FrontendRuntime
    return ($null -ne $runtime) -and ([string]$runtime.apiBase -eq $ExpectedApiBase)
}

function Test-ListeningPort {
    param([Parameter(Mandatory = $true)][int]$Port)

    return $null -ne (Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1)
}

function Test-PortBindable {
    param([Parameter(Mandatory = $true)][int]$Port)

    $listener = [System.Net.Sockets.TcpListener]::new([System.Net.IPAddress]::Loopback, $Port)
    try {
        $listener.Start()
        return $true
    }
    catch [System.Net.Sockets.SocketException] {
        return $false
    }
    finally {
        $listener.Stop()
    }
}

function Get-PortFromApiBase {
    param([Parameter(Mandatory = $true)][string]$ApiBase)

    try {
        $uri = [System.Uri]$ApiBase
        if (($uri.Scheme -ne "http") -or ($uri.Host -notin @("127.0.0.1", "localhost"))) {
            return 0
        }
        return $uri.Port
    }
    catch {
        return 0
    }
}

function Wait-ServiceHealth {
    param(
        [Parameter(Mandatory = $true)][string]$Name,
        [Parameter(Mandatory = $true)][string]$Url,
        [Parameter(Mandatory = $true)][System.Diagnostics.Process]$Process,
        [Parameter(Mandatory = $true)][scriptblock]$Probe
    )

    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    while ((Get-Date) -lt $deadline) {
        if (& $Probe) {
            Write-Host "$Name ready: $Url"
            return
        }
        if ($Process.HasExited) {
            throw "$Name exited before becoming healthy (exit code $($Process.ExitCode))."
        }
        Start-Sleep -Milliseconds 300
    }
    throw "$Name did not become healthy within $TimeoutSeconds seconds: $Url"
}

function Select-BackendPort {
    $runtime = Get-FrontendRuntime
    if ($null -ne $runtime) {
        $runtimePort = Get-PortFromApiBase -ApiBase ([string]$runtime.apiBase)
        if ($runtimePort -notin $BackendPortCandidates) {
            throw "The running TikTok Bot UI targets an unsupported backend: $($runtime.apiBase)."
        }
        if ((Test-BackendService -Port $runtimePort) -or (Test-PortBindable -Port $runtimePort)) {
            return $runtimePort
        }
        throw "The running TikTok Bot UI targets backend port $runtimePort, but that port cannot be reused."
    }
    if (Test-ListeningPort -Port $FrontendPort) {
        throw "Port $FrontendPort is occupied by an unknown frontend. Stop it before starting this project."
    }

    foreach ($port in $BackendPortCandidates) {
        if ((Test-ListeningPort -Port $port) -and (Test-BackendService -Port $port)) {
            return $port
        }
    }
    foreach ($port in $BackendPortCandidates) {
        if (Test-PortBindable -Port $port) {
            return $port
        }
    }
    throw "No backend port is available. Tried: $($BackendPortCandidates -join ', ')."
}

function Start-Backend {
    param([Parameter(Mandatory = $true)][int]$Port)

    $baseUrl = Get-BackendBaseUrl -Port $Port
    $healthUrl = "$baseUrl/api/health"
    if (Test-BackendService -Port $Port) {
        Write-Host "Backend already healthy: $healthUrl"
        return $baseUrl
    }
    if (-not (Test-PortBindable -Port $Port)) {
        throw "Backend port $Port is unavailable or reserved by Windows."
    }
    $python = (Get-Command python -ErrorAction Stop).Source
    $process = Start-Process `
        -FilePath $python `
        -ArgumentList @("-m", "uvicorn", "tiktok_bot_api.main:app", "--env-file", ".env", "--port", [string]$Port) `
        -WorkingDirectory $ProjectRoot `
        -WindowStyle Hidden `
        -RedirectStandardOutput (Join-Path $LogRoot "backend-$Port.stdout.log") `
        -RedirectStandardError (Join-Path $LogRoot "backend-$Port.stderr.log") `
        -PassThru
    Write-Host "Backend started on port $Port (PID $($process.Id))."
    Wait-ServiceHealth -Name "Backend" -Url $healthUrl -Process $process -Probe { Test-BackendService -Port $Port }
    return $baseUrl
}

function Start-Frontend {
    param([Parameter(Mandatory = $true)][string]$BackendBaseUrl)

    $runtime = Get-FrontendRuntime
    if ($null -ne $runtime) {
        if ([string]$runtime.apiBase -ne $BackendBaseUrl) {
            throw "Frontend/backend mismatch: UI targets $($runtime.apiBase), selected backend is $BackendBaseUrl."
        }
        Write-Host "Frontend already healthy and bound to ${BackendBaseUrl}: $FrontendUrl"
        return
    }
    if ((Test-ListeningPort -Port $FrontendPort) -or (-not (Test-PortBindable -Port $FrontendPort))) {
        throw "Port $FrontendPort is unavailable or occupied by an unknown frontend."
    }

    $npm = (Get-Command npm.cmd -ErrorAction Stop).Source
    $hadApiBase = Test-Path Env:VITE_API_BASE
    $previousApiBase = $env:VITE_API_BASE
    try {
        $env:VITE_API_BASE = $BackendBaseUrl
        $process = Start-Process `
            -FilePath $npm `
            -ArgumentList @("run", "dev", "--", "--host", "127.0.0.1", "--port", [string]$FrontendPort, "--strictPort") `
            -WorkingDirectory $UiRoot `
            -WindowStyle Hidden `
            -RedirectStandardOutput (Join-Path $LogRoot "frontend.stdout.log") `
            -RedirectStandardError (Join-Path $LogRoot "frontend.stderr.log") `
            -PassThru
    }
    finally {
        if ($hadApiBase) {
            $env:VITE_API_BASE = $previousApiBase
        }
        else {
            Remove-Item Env:VITE_API_BASE -ErrorAction SilentlyContinue
        }
    }
    Write-Host "Frontend started (PID $($process.Id))."
    Wait-ServiceHealth -Name "Frontend" -Url $FrontendRuntimeUrl -Process $process -Probe {
        Test-FrontendApiTarget -ExpectedApiBase $BackendBaseUrl
    }
}

New-Item -ItemType Directory -Force -Path $LogRoot | Out-Null
$SelectedBackendPort = Select-BackendPort
$BackendBaseUrl = Start-Backend -Port $SelectedBackendPort
Start-Frontend -BackendBaseUrl $BackendBaseUrl

if (-not (Test-BackendService -Port $SelectedBackendPort)) {
    throw "Final backend verification failed for $BackendBaseUrl."
}
if (-not (Test-FrontendApiTarget -ExpectedApiBase $BackendBaseUrl)) {
    throw "Final frontend/backend binding verification failed."
}

Write-Output "Services are ready."
Write-Output "UI:  $FrontendUrl"
Write-Output "API: $BackendBaseUrl/api/health"
