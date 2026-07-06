$ErrorActionPreference = "Stop"

Set-Location (Join-Path $PSScriptRoot "..")

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

if (-not $env:AGENT_WEB_PORT) {
    $env:AGENT_WEB_PORT = "8787"
}

cloudflared tunnel --url "http://127.0.0.1:$env:AGENT_WEB_PORT"
