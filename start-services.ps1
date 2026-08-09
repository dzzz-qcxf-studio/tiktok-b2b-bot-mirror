[CmdletBinding()]
param(
    [switch]$DryRun,
    [ValidateRange(5, 120)]
    [int]$TimeoutSeconds = 30
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$UiRoot = Join-Path $ProjectRoot "tiktok_bot_console\ui"
$LogRoot = Join-Path $ProjectRoot "data\logs"
$BackendHealthUrl = "http://127.0.0.1:8000/api/health"
$FrontendUrl = "http://127.0.0.1:5173"

$BackendDisplayCommand = "python -m uvicorn tiktok_bot_api.main:app --env-file .env --port 8000"
$FrontendDisplayCommand = "npm.cmd run dev -- --host 127.0.0.1"

if ($DryRun) {
    Write-Output $BackendDisplayCommand
    Write-Output $FrontendDisplayCommand
    Write-Output $BackendHealthUrl
    Write-Output $FrontendUrl
    exit 0
}

function Test-ServiceHealth {
    param([Parameter(Mandatory = $true)][string]$Url)

    try {
        $response = Invoke-WebRequest -UseBasicParsing -Uri $Url -TimeoutSec 2
        return $response.StatusCode -eq 200
    }
    catch {
        return $false
    }
}

function Test-ListeningPort {
    param([Parameter(Mandatory = $true)][int]$Port)

    return $null -ne (Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1)
}

function Wait-ServiceHealth {
    param(
        [Parameter(Mandatory = $true)][string]$Name,
        [Parameter(Mandatory = $true)][string]$Url,
        [Parameter(Mandatory = $true)][System.Diagnostics.Process]$Process
    )

    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    while ((Get-Date) -lt $deadline) {
        if (Test-ServiceHealth -Url $Url) {
            Write-Output "$Name ready: $Url"
            return
        }
        if ($Process.HasExited) {
            throw "$Name exited before becoming healthy (exit code $($Process.ExitCode))."
        }
        Start-Sleep -Milliseconds 300
    }
    throw "$Name did not become healthy within $TimeoutSeconds seconds: $Url"
}

function Start-Backend {
    if (Test-ServiceHealth -Url $BackendHealthUrl) {
        Write-Output "Backend already healthy: $BackendHealthUrl"
        return
    }
    if (Test-ListeningPort -Port 8000) {
        throw "Port 8000 is occupied, but the backend health check failed. Stop the conflicting process first."
    }

    $python = (Get-Command python -ErrorAction Stop).Source
    $process = Start-Process `
        -FilePath $python `
        -ArgumentList @("-m", "uvicorn", "tiktok_bot_api.main:app", "--env-file", ".env", "--port", "8000") `
        -WorkingDirectory $ProjectRoot `
        -WindowStyle Hidden `
        -RedirectStandardOutput (Join-Path $LogRoot "backend.stdout.log") `
        -RedirectStandardError (Join-Path $LogRoot "backend.stderr.log") `
        -PassThru
    Write-Output "Backend started (PID $($process.Id))."
    Wait-ServiceHealth -Name "Backend" -Url $BackendHealthUrl -Process $process
}

function Start-Frontend {
    if (Test-ServiceHealth -Url $FrontendUrl) {
        Write-Output "Frontend already healthy: $FrontendUrl"
        return
    }
    if (Test-ListeningPort -Port 5173) {
        throw "Port 5173 is occupied, but the frontend health check failed. Stop the conflicting process first."
    }

    $npm = (Get-Command npm.cmd -ErrorAction Stop).Source
    $process = Start-Process `
        -FilePath $npm `
        -ArgumentList @("run", "dev", "--", "--host", "127.0.0.1") `
        -WorkingDirectory $UiRoot `
        -WindowStyle Hidden `
        -RedirectStandardOutput (Join-Path $LogRoot "frontend.stdout.log") `
        -RedirectStandardError (Join-Path $LogRoot "frontend.stderr.log") `
        -PassThru
    Write-Output "Frontend started (PID $($process.Id))."
    Wait-ServiceHealth -Name "Frontend" -Url $FrontendUrl -Process $process
}

New-Item -ItemType Directory -Force -Path $LogRoot | Out-Null
Start-Backend
Start-Frontend

Write-Output "Services are ready."
Write-Output "UI:  $FrontendUrl"
Write-Output "API: $BackendHealthUrl"
