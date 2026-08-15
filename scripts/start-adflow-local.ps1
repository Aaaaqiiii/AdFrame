param(
    [int]$ApiPort = 8011,
    [int]$WebPort = 5174,
    [string]$BindHost = '127.0.0.1',
    [string]$ApiPublicHost = 'localhost'
)
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$ffmpeg = Get-Command ffmpeg -ErrorAction SilentlyContinue
$ffprobe = Get-Command ffprobe -ErrorAction SilentlyContinue
if (-not $ffmpeg -or -not $ffprobe) {
    throw 'FFmpeg/FFprobe is unavailable. Install FFmpeg and restart the terminal before starting AdFlow.'
}
try {
    & $ffprobe.Source -version *> $null
    & $ffmpeg.Source -version *> $null
} catch {
    throw 'FFmpeg/FFprobe is installed but cannot be executed. Check its Windows permissions before starting AdFlow.'
}
foreach ($port in @($ApiPort, $WebPort)) {
    if (Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue) {
        throw "Port $port is already in use. Choose another port, for example: -ApiPort 8012 -WebPort 5175"
    }
}

Start-Process powershell -WindowStyle Hidden -ArgumentList '-NoProfile', '-Command', "`$env:CORS_ORIGIN_REGEX='http://(localhost|127\.0\.0\.1|[a-zA-Z0-9.-]+):\d+'; Set-Location '$root\backend'; python -m uvicorn app.main:app --host $BindHost --port $ApiPort"
Start-Process powershell -WindowStyle Hidden -ArgumentList '-NoProfile', '-Command', "Set-Location '$root\backend'; python -m app.worker"
Start-Process powershell -WindowStyle Hidden -ArgumentList '-NoProfile', '-Command', "`$env:VITE_API_BASE_URL='http://$ApiPublicHost`:$ApiPort'; Set-Location '$root\frontend'; npm.cmd run dev -- --host $BindHost --port $WebPort"

Write-Host "AdFlow is starting. Open http://localhost:$WebPort"
