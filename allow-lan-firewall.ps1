$ErrorActionPreference = 'Stop'

$identity = [Security.Principal.WindowsIdentity]::GetCurrent()
$principal = [Security.Principal.WindowsPrincipal]::new($identity)
if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    $args = "-NoProfile -ExecutionPolicy Bypass -File `"$PSCommandPath`""
    Start-Process -FilePath 'powershell.exe' -Verb RunAs -ArgumentList $args
    exit
}

$python = Join-Path $PSScriptRoot 'venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $python)) {
    throw "Traceframe Python was not found at $python"
}

$privateProfiles = Get-NetConnectionProfile | Where-Object { $_.NetworkCategory -eq 'Private' }
if (-not $privateProfiles) {
    throw 'No network is marked Private. On your trusted Wi-Fi, open Settings > Network & internet > Wi-Fi > its properties, choose Private network, then run this helper again.'
}

$displayName = 'Traceframe LAN HTTPS'
$existing = Get-NetFirewallRule -DisplayName $displayName -ErrorAction SilentlyContinue
if ($existing) {
    Write-Host "The '$displayName' firewall rule already exists."
} else {
    New-NetFirewallRule -DisplayName $displayName -Direction Inbound -Action Allow `
        -Protocol TCP -LocalPort 8443 -Program $python -Profile Private `
        -RemoteAddress LocalSubnet | Out-Null
    Write-Host 'Added a rule for the Traceframe Python runtime on port 8443, limited to Private networks and the local subnet.'
}
Write-Host 'Open the host URL printed by start-lan.ps1 from a device on this same trusted Wi-Fi.'
