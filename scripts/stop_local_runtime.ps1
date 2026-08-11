$ErrorActionPreference = "Stop"

. (Join-Path $PSScriptRoot "local_runtime_common.ps1")
$identity = Assert-HostRuntimeIdentity

$pagRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot ".."))
$statePath = Join-Path $pagRoot "data\local-runtime-state.json"
$state = Read-LocalRuntimeState -Path $statePath

if ($null -eq $state) {
    Write-RuntimeResult -Result ([ordered]@{
        status = "not_running"
        identity = $identity.name
    })
    exit 0
}

if (-not [string]::Equals(
    $state.identity.sid,
    $identity.sid,
    [StringComparison]::OrdinalIgnoreCase
)) {
    throw "runtime_state_identity_mismatch: remove no processes automatically"
}

$verified = @{}
foreach ($name in "pag", "lmg") {
    $entry = $state.$name
    $process = Get-Process -Id ([int]$entry.pid) -ErrorAction SilentlyContinue
    if ($null -eq $process) {
        $verified[$name] = $null
        continue
    }
    if (-not (Test-RuntimeProcessMatches -Entry $entry -Process $process)) {
        throw "runtime_state_mismatch: name=$name pid=$($entry.pid)"
    }
    $ownerSid = Get-ProcessOwnerSid -ProcessId $process.Id
    if (-not [string]::Equals(
        $ownerSid,
        $identity.sid,
        [StringComparison]::OrdinalIgnoreCase
    )) {
        throw "runtime_owner_mismatch: name=$name pid=$($entry.pid)"
    }
    $verified[$name] = $process
}

$stopped = @()
$failures = @()
foreach ($name in "pag", "lmg") {
    $process = $verified[$name]
    if ($null -ne $process) {
        try {
            # Stop-Process ends this PID only. The gateway spawns CLI trees,
            # so kill the descendants with it -- otherwise they outlive the
            # stop, holding workspace handles and burning provider quota.
            & taskkill.exe /PID $process.Id /T /F | Out-Null
            if ($LASTEXITCODE -ne 0) { throw "taskkill_failed: $($process.Id) exit $LASTEXITCODE" }
            Wait-RuntimeProcessExit -ProcessId $process.Id
            $stopped += $name
        } catch {
            $failures += [pscustomobject]@{
                name = $name
                pid = $process.Id
                error = $_.Exception.Message
            }
        }
    }
}

foreach ($entry in @(@{ name = "pag"; port = 8787 }, @{ name = "lmg"; port = 8788 })) {
    if (-not (Wait-PortReleased -Port $entry.port)) {
        $failures += [pscustomobject]@{
            name  = $entry.name
            pid   = $null
            error = "port_still_listening: $($entry.port)"
        }
    }
}

if ($failures.Count -gt 0) {
    Write-RuntimeResult -Result ([ordered]@{
        status = "partial_failure"
        identity = $identity.name
        stopped = $stopped
        failures = $failures
    })
    exit 1
}

Remove-Item -LiteralPath $statePath -Force

Write-RuntimeResult -Result ([ordered]@{
    status = "stopped"
    identity = $identity.name
    stopped = $stopped
})
