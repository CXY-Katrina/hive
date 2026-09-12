param(
    [string]$InterfaceAlias = 'WLAN',
    [string]$ClientAddress = ''
)
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
    $displayName = 'Hive Wi-Fi TCP 18000'
    $remoteAddress = 'LocalSubnet'
    if ($ClientAddress) {
        $clientIP = [System.Net.IPAddress]::Parse($ClientAddress)
        if ($clientIP.AddressFamily -ne [System.Net.Sockets.AddressFamily]::InterNetwork) {
            throw 'ClientAddress must be one IPv4 address.'
        }
        $remoteAddress = $clientIP.ToString()
        $ruleName += '-Client-' + $remoteAddress.Replace('.', '-')
        $displayName += ' client ' + $remoteAddress
    }
    if (Get-NetFirewallRule -Name $ruleName -ErrorAction SilentlyContinue) {
        Set-NetFirewallRule -Name $ruleName -Enabled True -Direction Inbound -Action Allow -Profile Any -InterfaceAlias $InterfaceAlias -Protocol TCP -LocalPort 18000 -RemoteAddress $remoteAddress | Out-Null
    } else {
        New-NetFirewallRule -Name $ruleName -DisplayName $displayName -Enabled True -Direction Inbound -Action Allow -Profile Any -InterfaceAlias $InterfaceAlias -Protocol TCP -LocalPort 18000 -RemoteAddress $remoteAddress | Out-Null
    }
    @{ ok = $true; rule = $ruleName; port = 18000; interface = $InterfaceAlias; remote = $remoteAddress } | ConvertTo-Json | Set-Content -LiteralPath $resultPath -Encoding UTF8
    Write-Output "Wi-Fi access enabled for TCP 18000 from $remoteAddress."
} catch {
    @{ ok = $false; error = $_.Exception.Message } | ConvertTo-Json | Set-Content -LiteralPath $resultPath -Encoding UTF8
    throw
}
