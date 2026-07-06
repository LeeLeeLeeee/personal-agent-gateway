$ErrorActionPreference = "Stop"

Set-Location (Join-Path $PSScriptRoot "..")

$requestedHost = $env:AGENT_WEB_HOST
$requestedPort = $env:AGENT_WEB_PORT

if (Test-Path ".env") {
    Get-Content ".env" | ForEach-Object {
        $line = $_.Trim()
        if (-not $line -or $line.StartsWith("#")) {
            return
        }
        $name, $value = $line.Split("=", 2)
        if ($name) {
            [Environment]::SetEnvironmentVariable($name.Trim(), $value.Trim(), "Process")
        }
    }
}

if ($requestedHost) {
    $env:AGENT_WEB_HOST = $requestedHost
}
if ($requestedPort) {
    $env:AGENT_WEB_PORT = $requestedPort
}

$python = "python"
$venvPython = Join-Path ".venv" "Scripts\python.exe"
if (Test-Path $venvPython) {
    $python = $venvPython
}

if ($env:PYTHONPATH) {
    $env:PYTHONPATH = "src;$env:PYTHONPATH"
} else {
    $env:PYTHONPATH = "src"
}

$hostName = $env:AGENT_WEB_HOST
if (-not $hostName) {
    $hostName = "127.0.0.1"
}
$port = $env:AGENT_WEB_PORT
if (-not $port) {
    $port = "8787"
}

& $python -m uvicorn personal_agent_gateway.app:create_app --factory --host $hostName --port $port
