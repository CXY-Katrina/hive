param(
    [string]$EnvFile = 'data/preview.env',
    [string]$InterfaceAlias = 'WLAN'
)
$ErrorActionPreference = 'Stop'
$workspace = Split-Path $PSScriptRoot -Parent
$python = Join-Path $workspace '.venv/Scripts/python.exe'
$configPath = if ([IO.Path]::IsPathRooted($EnvFile)) { $EnvFile } else { Join-Path $workspace $EnvFile }
if (!(Test-Path -LiteralPath $python) -or !(Test-Path -LiteralPath $configPath)) {
    throw 'Python environment or Hive configuration file is missing.'
}
$addresses = @(Get-NetIPAddress -InterfaceAlias $InterfaceAlias -AddressFamily IPv4 |
    Where-Object { $_.AddressState -eq 'Preferred' -and $_.IPAddress -notlike '169.254.*' })
if ($addresses.Count -ne 1) { throw 'Expected one active IPv4 address on the selected interface.' }
$origin = "http://$($addresses[0].IPAddress):18000"
$apiProcesses = @(Get-CimInstance Win32_Process | Where-Object {
    $_.CommandLine -and $_.CommandLine.Contains($python.Replace('/', '\')) -and
    $_.CommandLine -match '-m hive\.cli api\s' -and $_.CommandLine -match '--port[ =]+18000(?:\s|$)'
})
$listeners = @(Get-NetTCPConnection -State Listen -LocalPort 18000 -ErrorAction SilentlyContinue)
foreach ($listener in $listeners) {
    if ($listener.OwningProcess -notin $apiProcesses.ProcessId) {
        throw 'Port 18000 is occupied by another process; it has not been stopped.'
    }
}
$config = [IO.File]::ReadAllText($configPath)
foreach ($setting in @(@('HIVE_ORIGIN', $origin), @('HIVE_COOKIE_SECURE', 'false'))) {
    $pattern = '(?m)^' + $setting[0] + '=.*$'
    $line = $setting[0] + '=' + $setting[1]
    $config = if ($config -match $pattern) { [regex]::Replace($config, $pattern, $line) } else { $config.TrimEnd() + "`r`n" + $line + "`r`n" }
}
[IO.File]::WriteAllText($configPath, $config, [Text.UTF8Encoding]::new($false))
foreach ($apiProcess in $apiProcesses) { Stop-Process -Id $apiProcess.ProcessId -ErrorAction SilentlyContinue }
$data = Join-Path $workspace 'data'
New-Item -ItemType Directory -Path $data -Force | Out-Null
$previousOrigin, $previousCookie = $env:HIVE_ORIGIN, $env:HIVE_COOKIE_SECURE
try {
    $env:HIVE_ORIGIN = $origin
    $env:HIVE_COOKIE_SECURE = 'false'
    $api = Start-Process -FilePath $python -ArgumentList @('-m', 'hive.cli', 'api', '--host', '0.0.0.0', '--port', '18000', '--env-file', ('"' + $configPath + '"')) -WorkingDirectory $workspace -WindowStyle Hidden -RedirectStandardOutput (Join-Path $data 'api.out.log') -RedirectStandardError (Join-Path $data 'api.err.log') -PassThru
    $api.Id | Set-Content -LiteralPath (Join-Path $data 'api.pid')
} finally {
    $env:HIVE_ORIGIN = $previousOrigin
    $env:HIVE_COOKIE_SECURE = $previousCookie
}
Write-Output "Hive API started (PID $($api.Id)): $origin/login"
Write-Output 'Use this address on both this computer and other Wi-Fi devices. The worker is unchanged.'
