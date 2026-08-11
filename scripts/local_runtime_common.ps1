$ErrorActionPreference = "Stop"

function Test-CodexSandboxIdentity {
    param([Parameter(Mandatory = $true)][string]$Name)

    $account = ($Name -split '\\')[-1]
    return $account -ieq "CodexSandboxOnline" -or
        $account -ieq "CodexSandboxOffline"
}

function Get-HostRuntimeIdentity {
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    return [pscustomobject]@{
        name = $identity.Name
        sid = $identity.User.Value
    }
}

function Assert-HostRuntimeIdentity {
    $identity = Get-HostRuntimeIdentity
    if (Test-CodexSandboxIdentity -Name $identity.name) {
        throw "sandbox_identity_forbidden: $($identity.name) cannot host PAG/LMG; run this launcher through approved outside-sandbox execution"
    }
    return $identity
}

function Get-ProcessOwnerSid {
    param([Parameter(Mandatory = $true)][int]$ProcessId)

    $instance = Get-CimInstance Win32_Process -Filter "ProcessId = $ProcessId"
    if ($null -eq $instance) {
        throw "process_owner_unavailable: pid=$ProcessId"
    }
    $result = Invoke-CimMethod -InputObject $instance -MethodName GetOwnerSid
    if ($result.ReturnValue -ne 0 -or [string]::IsNullOrWhiteSpace($result.Sid)) {
        throw "process_owner_unavailable: pid=$ProcessId code=$($result.ReturnValue)"
    }
    return [string]$result.Sid
}

function Test-RuntimeProcessMatches {
    param(
        [Parameter(Mandatory = $true)]$Entry,
        [Parameter(Mandatory = $true)]$Process
    )

    if ([int]$Entry.pid -ne [int]$Process.Id) {
        return $false
    }
    $expectedStart = ([datetime]$Entry.started_at).ToUniversalTime()
    $actualStart = ([datetime]$Process.StartTime).ToUniversalTime()
    if ([math]::Abs(($expectedStart - $actualStart).TotalSeconds) -gt 1) {
        return $false
    }
    return [string]::Equals(
        [IO.Path]::GetFullPath([string]$Entry.path),
        [IO.Path]::GetFullPath([string]$Process.Path),
        [StringComparison]::OrdinalIgnoreCase
    )
}

function Read-LocalRuntimeState {
    param([Parameter(Mandatory = $true)][string]$Path)

    if (-not (Test-Path -LiteralPath $Path)) {
        return $null
    }
    return Get-Content -Raw -LiteralPath $Path | ConvertFrom-Json
}

function Write-LocalRuntimeState {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)]$State
    )

    $parent = Split-Path -Parent $Path
    New-Item -ItemType Directory -Force -Path $parent | Out-Null
    $temporary = "$Path.tmp"
    $State | ConvertTo-Json -Depth 5 |
        Set-Content -LiteralPath $temporary -Encoding UTF8
    Move-Item -Force -LiteralPath $temporary -Destination $Path
}

function Get-ListenerProcessId {
    param([Parameter(Mandatory = $true)][int]$Port)

    $listener = Get-NetTCPConnection -State Listen -LocalPort $Port `
        -ErrorAction SilentlyContinue |
        Select-Object -First 1
    if ($null -eq $listener) {
        return $null
    }
    return [int]$listener.OwningProcess
}

function Get-VerifiedListenerProcess {
    param(
        [Parameter(Mandatory = $true)][int]$Port,
        [Parameter(Mandatory = $true)][string]$ExpectedOwnerSid
    )

    $processId = Get-ListenerProcessId -Port $Port
    if ($null -eq $processId) {
        throw "runtime_listener_missing: port=$Port"
    }
    $process = Get-Process -Id $processId -ErrorAction Stop
    $ownerSid = Get-ProcessOwnerSid -ProcessId $process.Id
    if (-not [string]::Equals(
        $ownerSid,
        $ExpectedOwnerSid,
        [StringComparison]::OrdinalIgnoreCase
    )) {
        throw "runtime_owner_mismatch: port=$Port pid=$processId"
    }
    return $process
}

function Write-RuntimeResult {
    param([Parameter(Mandatory = $true)]$Result)

    $json = $Result | ConvertTo-Json -Compress -Depth 5
    Write-Host $json
}

function Wait-RuntimeProcessExit {
    param(
        [Parameter(Mandatory = $true)][int]$ProcessId,
        [int]$Seconds = 10
    )

    $deadline = (Get-Date).AddSeconds($Seconds)
    while ((Get-Date) -lt $deadline) {
        if ($null -eq (
            Get-Process -Id $ProcessId -ErrorAction SilentlyContinue
        )) {
            return
        }
        Start-Sleep -Milliseconds 100
    }
    throw "process_still_running: pid=$ProcessId"
}

function Wait-PortReleased {
    param(
        [Parameter(Mandatory = $true)][int] $Port,
        [int] $TimeoutSeconds = 10
    )
    # Reporting "stopped" while the port is still bound sends the operator
    # straight into a port_conflict on the next start.
    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    while ((Get-Date) -lt $deadline) {
        $listener = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
        if ($null -eq $listener) { return $true }
        Start-Sleep -Milliseconds 250
    }
    return $false
}
