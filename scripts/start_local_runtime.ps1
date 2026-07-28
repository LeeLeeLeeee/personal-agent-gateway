param(
    [switch]$Worker,
    [string]$ResultPath
)

$ErrorActionPreference = "Stop"

. (Join-Path $PSScriptRoot "local_runtime_common.ps1")
$identity = Assert-HostRuntimeIdentity

$pagRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot ".."))

function Write-StartResult {
    param([Parameter(Mandatory = $true)]$Result)

    $json = $Result | ConvertTo-Json -Compress -Depth 5
    $encoding = [Text.UTF8Encoding]::new($false)
    [IO.File]::WriteAllText($ResultPath, $json, $encoding)
}

if (-not $Worker) {
    $resultDirectory = Join-Path $pagRoot "data"
    New-Item -ItemType Directory -Force -Path $resultDirectory | Out-Null
    $resultFile = Join-Path $resultDirectory (
        "local-runtime-result-$([guid]::NewGuid().ToString('N')).json"
    )
    $workerArgs = @(
        "-NoProfile",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        "`"$PSCommandPath`"",
        "-Worker",
        "-ResultPath",
        "`"$resultFile`""
    )
    $workerProcess = Start-Process -FilePath "powershell.exe" `
        -ArgumentList $workerArgs -WindowStyle Hidden -PassThru
    $workerProcess.WaitForExit()
    $workerExitCode = $workerProcess.ExitCode
    if (Test-Path -LiteralPath $resultFile) {
        $json = Get-Content -Raw -LiteralPath $resultFile
        Remove-Item -LiteralPath $resultFile -Force
        [Console]::Out.WriteLine($json)
        [Console]::Out.Flush()
    } else {
        Write-RuntimeResult -Result ([ordered]@{
            status = "error"
            error = "runtime_worker_failed_without_result"
        })
        exit 1
    }
    exit $workerExitCode
}

if ([string]::IsNullOrWhiteSpace($ResultPath)) {
    throw "runtime_worker_result_path_missing"
}

trap {
    Write-StartResult -Result ([ordered]@{
        status = "error"
        error = $_.Exception.Message
    })
    exit 1
}

$workspaceRoot = [IO.Path]::GetFullPath((Join-Path $pagRoot ".."))
$lmgRoot = Join-Path $workspaceRoot "local-model-gateway"
$statePath = Join-Path $pagRoot "data\local-runtime-state.json"
$lmgData = Join-Path $lmgRoot "data"
$lmgExe = Join-Path $lmgData "bin\lmg.exe"
$started = [System.Collections.Generic.List[System.Diagnostics.Process]]::new()

function Import-RuntimeEnv {
    param([Parameter(Mandatory = $true)][string]$Path)

    Get-Content -LiteralPath $Path | ForEach-Object {
        $line = $_.Trim()
        if (-not $line -or $line.StartsWith("#")) {
            return
        }
        $name, $value = $line.Split("=", 2)
        if ($name) {
            [Environment]::SetEnvironmentVariable(
                $name.Trim(),
                $value.Trim(),
                "Process"
            )
        }
    }
}

function Wait-HttpSuccess {
    param(
        [Parameter(Mandatory = $true)][string]$Uri,
        [hashtable]$Headers = @{},
        [int]$Seconds = 30
    )

    $deadline = (Get-Date).AddSeconds($Seconds)
    while ((Get-Date) -lt $deadline) {
        try {
            $response = Invoke-WebRequest -UseBasicParsing -TimeoutSec 2 `
                -Uri $Uri -Headers $Headers
            if ($response.StatusCode -eq 200) {
                return
            }
        } catch {
            Start-Sleep -Milliseconds 500
        }
    }
    throw "health_check_failed: $Uri"
}

function Get-VerifiedProcess {
    param(
        [Parameter(Mandatory = $true)]$Entry,
        [Parameter(Mandatory = $true)][string]$ExpectedOwnerSid
    )

    if ($null -eq $Entry) {
        return $null
    }
    $process = Get-Process -Id ([int]$Entry.pid) -ErrorAction SilentlyContinue
    if ($null -eq $process) {
        return $null
    }
    if (-not (Test-RuntimeProcessMatches -Entry $Entry -Process $process)) {
        return $null
    }
    $ownerSid = Get-ProcessOwnerSid -ProcessId $process.Id
    if (-not [string]::Equals(
        $ownerSid,
        $ExpectedOwnerSid,
        [StringComparison]::OrdinalIgnoreCase
    )) {
        return $null
    }
    return $process
}

$envPath = Join-Path $pagRoot ".env"
if (-not (Test-Path -LiteralPath $envPath)) {
    throw "runtime_config_missing: $envPath"
}
Import-RuntimeEnv -Path $envPath
if ([string]::IsNullOrWhiteSpace($env:LMG_LOCAL_TOKEN)) {
    throw "runtime_config_missing: LMG_LOCAL_TOKEN"
}

Get-ChildItem Env: | Where-Object Name -Like "CODEX_*" |
    ForEach-Object { Remove-Item -LiteralPath "Env:$($_.Name)" }

$existing = Read-LocalRuntimeState -Path $statePath
if ($existing) {
    if (-not [string]::Equals(
        $existing.identity.sid,
        $identity.sid,
        [StringComparison]::OrdinalIgnoreCase
    )) {
        throw "runtime_state_identity_mismatch: remove no processes automatically"
    }
    $existingLmg = Get-VerifiedProcess `
        -Entry $existing.lmg -ExpectedOwnerSid $identity.sid
    $existingPag = Get-VerifiedProcess `
        -Entry $existing.pag -ExpectedOwnerSid $identity.sid
    if ($existingLmg -and $existingPag) {
        Wait-HttpSuccess -Uri "http://127.0.0.1:8788/livez"
        Wait-HttpSuccess -Uri "http://127.0.0.1:8788/readyz" `
            -Headers @{ Authorization = "Bearer $($env:LMG_LOCAL_TOKEN)" }
        Wait-HttpSuccess -Uri "http://127.0.0.1:8787/health/live"
        Wait-HttpSuccess -Uri "http://127.0.0.1:8787/health/ready"
        Write-StartResult -Result ([ordered]@{
            status = "already_running"
            identity = $identity.name
            lmg_pid = $existingLmg.Id
            pag_pid = $existingPag.Id
            lmg_ready = $true
            pag_ready = $true
        })
        exit 0
    }
    throw "runtime_state_mismatch: remove no processes automatically"
}

foreach ($port in 8787, 8788) {
    $listenerPid = Get-ListenerProcessId -Port $port
    if ($null -ne $listenerPid) {
        throw "port_conflict: port=$port pid=$listenerPid"
    }
}

try {
    New-Item -ItemType Directory -Force -Path (Split-Path -Parent $lmgExe) |
        Out-Null
    Push-Location $lmgRoot
    try {
        & go build -o $lmgExe .\cmd\lmg
        if ($LASTEXITCODE -ne 0) {
            throw "lmg_build_failed: exit=$LASTEXITCODE"
        }
    } finally {
        Pop-Location
    }

    $env:LMG_HOST = "127.0.0.1"
    $env:LMG_PORT = "8788"
    $env:LMG_DATA_DIR = $lmgData
    if ([string]::IsNullOrWhiteSpace($env:LMG_ALLOWED_ROOTS)) {
        $env:LMG_ALLOWED_ROOTS = $workspaceRoot
    }

    $lmgOut = Join-Path $lmgData "lmg-runtime.out.log"
    $lmgErr = Join-Path $lmgData "lmg-runtime.err.log"
    $lmg = Start-Process -FilePath $lmgExe -WorkingDirectory $lmgRoot `
        -RedirectStandardOutput $lmgOut -RedirectStandardError $lmgErr `
        -WindowStyle Hidden -PassThru
    $started.Add($lmg)

    Wait-HttpSuccess -Uri "http://127.0.0.1:8788/livez"
    Wait-HttpSuccess -Uri "http://127.0.0.1:8788/readyz" `
        -Headers @{ Authorization = "Bearer $($env:LMG_LOCAL_TOKEN)" }
    $lmgListener = Get-VerifiedListenerProcess `
        -Port 8788 -ExpectedOwnerSid $identity.sid
    if ($lmgListener.Id -ne $lmg.Id) {
        $started.Add($lmgListener)
    }

    $python = Join-Path $pagRoot ".venv\Scripts\python.exe"
    if (-not (Test-Path -LiteralPath $python)) {
        throw "pag_python_missing: $python"
    }
    $env:PYTHONPATH = Join-Path $pagRoot "src"
    $env:LMG_BASE_URL = "http://127.0.0.1:8788"
    $env:AGENT_WEB_HOST = "127.0.0.1"
    $env:AGENT_WEB_PORT = "8787"
    $pagData = Join-Path $pagRoot "data"
    New-Item -ItemType Directory -Force -Path $pagData | Out-Null
    $pagArgs = @(
        "-m",
        "uvicorn",
        "personal_agent_gateway.app:create_app",
        "--factory",
        "--host",
        "127.0.0.1",
        "--port",
        "8787"
    )
    $pag = Start-Process -FilePath $python -ArgumentList $pagArgs `
        -WorkingDirectory $pagRoot `
        -RedirectStandardOutput (Join-Path $pagData "pag-runtime.out.log") `
        -RedirectStandardError (Join-Path $pagData "pag-runtime.err.log") `
        -WindowStyle Hidden -PassThru
    $started.Add($pag)

    Wait-HttpSuccess -Uri "http://127.0.0.1:8787/health/live"
    Wait-HttpSuccess -Uri "http://127.0.0.1:8787/health/ready"
    $pagListener = Get-VerifiedListenerProcess `
        -Port 8787 -ExpectedOwnerSid $identity.sid
    if ($pagListener.Id -ne $pag.Id) {
        $started.Add($pagListener)
    }

    $lmgListener.Refresh()
    $pagListener.Refresh()
    $state = [ordered]@{
        schema_version = 1
        started_at = (Get-Date).ToUniversalTime().ToString("o")
        identity = $identity
        lmg = [ordered]@{
            pid = $lmgListener.Id
            started_at = $lmgListener.StartTime.ToUniversalTime().ToString("o")
            path = $lmgListener.Path
        }
        pag = [ordered]@{
            pid = $pagListener.Id
            started_at = $pagListener.StartTime.ToUniversalTime().ToString("o")
            path = $pagListener.Path
        }
    }
    Write-LocalRuntimeState -Path $statePath -State $state

    Write-StartResult -Result ([ordered]@{
        status = "started"
        identity = $identity.name
        lmg_pid = $lmgListener.Id
        pag_pid = $pagListener.Id
        lmg_ready = $true
        pag_ready = $true
    })
} catch {
    foreach ($process in ($started | Sort-Object StartTime -Descending)) {
        Stop-Process -Id $process.Id -Force -ErrorAction SilentlyContinue
    }
    throw
}
