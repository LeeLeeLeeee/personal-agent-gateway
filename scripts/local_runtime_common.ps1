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
