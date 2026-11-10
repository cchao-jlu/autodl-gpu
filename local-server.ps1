param([switch]$Stop, [switch]$NoBrowser)
$ErrorActionPreference = 'Stop'
$projectDir = $PSScriptRoot
$pythonPath = Join-Path $projectDir '.venv\Scripts\python.exe'
$appPath = Join-Path $projectDir 'app.py'
$pidPath = Join-Path $projectDir 'runtime\data\web.pid'
$stdoutPath = Join-Path $projectDir 'runtime\logs\web.stdout.log'
$stderrPath = Join-Path $projectDir 'runtime\logs\web.stderr.log'
$url = 'http://127.0.0.1:7860'
$serverProcess = $null
if (Test-Path -LiteralPath $pidPath) {
    $serverId = [int](Get-Content -LiteralPath $pidPath -Raw).Trim()
    $candidate = Get-CimInstance Win32_Process -Filter "ProcessId = $serverId"
    if ($candidate -and $candidate.CommandLine -and $candidate.CommandLine.Contains($appPath)) {
        $serverProcess = Get-Process -Id $serverId -ErrorAction SilentlyContinue
    }
}
if ($Stop) {
    if ($serverProcess) {
        Stop-Process -Id $serverProcess.Id
        Write-Host 'AutoDL GPU Hunter stopped.'
    } else {
        Write-Host 'No managed server is running.'
    }
    if (Test-Path -LiteralPath $pidPath) { Remove-Item -LiteralPath $pidPath }
    exit 0
}
if (-not $serverProcess) {
    if (Get-NetTCPConnection -State Listen -LocalPort 7860 -ErrorAction SilentlyContinue) {
        throw 'Port 7860 is already occupied. Stop its owner before starting this app.'
    }
    if (-not (Test-Path -LiteralPath $pythonPath)) { throw 'Missing .venv. See LOCAL-SETUP.md.' }
    $env:PYTHONUTF8 = '1'
    $env:GRADIO_ANALYTICS_ENABLED = 'False'
    $env:GRADIO_SERVER_PORT = '7860'
    $env:NO_PROXY = 'localhost,127.0.0.1,::1'
    $serverProcess = Start-Process -FilePath $pythonPath -ArgumentList @('-u', ('"' + $appPath + '"')) -WorkingDirectory $projectDir -RedirectStandardOutput $stdoutPath -RedirectStandardError $stderrPath -WindowStyle Hidden -PassThru
    $serverProcess.Id | Set-Content -LiteralPath $pidPath
}
$ready = $false
$webClient = New-Object System.Net.WebClient
$webClient.Proxy = $null
$webClient.Encoding = [System.Text.Encoding]::UTF8
try {
    for ($attempt = 0; $attempt -lt 60; $attempt++) {
        $serverProcess.Refresh()
        if ($serverProcess.HasExited) { throw "Server exited. See $stderrPath" }
        try {
            $configuration = $webClient.DownloadString("$url/config") | ConvertFrom-Json
            if ($configuration.title -eq 'AutoDL GPU Hunter') { $ready = $true; break }
        } catch [System.Net.WebException] {}
        Start-Sleep -Milliseconds 500
    }
} finally { $webClient.Dispose() }
if (-not $ready) { throw "Server did not become ready. See $stderrPath" }
Write-Host "AutoDL GPU Hunter is running: $url (PID $($serverProcess.Id))"
if (-not $NoBrowser) { Start-Process $url }
