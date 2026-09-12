param([string]$InterfaceAlias = 'WLAN')
$ErrorActionPreference = 'Stop'
$workspace = Split-Path $PSScriptRoot -Parent
$resultPath = Join-Path $workspace 'data/lan-firewall-result.json'
try {
    $identity = [Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()
    if (!$identity.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
        throw 'Run this script as a Windows administrator to configure the firewall.'
    }
    Get-NetAdapter -Name $InterfaceAlias -ErrorAction Stop | Out-Null
    $ruleName = 'Hive-WiFi-TCP-18000'
    if (Get-NetFirewallRule -Name $ruleName -ErrorAction SilentlyContinue) {
        Set-NetFirewallRule -Name $ruleName -Enabled True -Direction Inbound -Action Allow -Profile Any -InterfaceAlias $InterfaceAlias -Protocol TCP -LocalPort 18000 -RemoteAddress LocalSubnet | Out-Null
    } else {
        New-NetFirewallRule -Name $ruleName -DisplayName 'Hive Wi-Fi TCP 18000' -Enabled True -Direction Inbound -Action Allow -Profile Any -InterfaceAlias $InterfaceAlias -Protocol TCP -LocalPort 18000 -RemoteAddress LocalSubnet | Out-Null
    }
    @{ ok = $true; rule = $ruleName; port = 18000; interface = $InterfaceAlias; remote = 'LocalSubnet' } | ConvertTo-Json | Set-Content -LiteralPath $resultPath -Encoding UTF8
    Write-Output 'Wi-Fi access enabled for TCP 18000 from the local subnet.'
} catch {
    @{ ok = $false; error = $_.Exception.Message } | ConvertTo-Json | Set-Content -LiteralPath $resultPath -Encoding UTF8
    throw
}
