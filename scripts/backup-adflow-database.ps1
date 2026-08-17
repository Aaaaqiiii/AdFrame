param(
    [Parameter(Mandatory)][string]$OutputDirectory
)
$ErrorActionPreference = 'Stop'

$root = Split-Path -Parent $PSScriptRoot
$envPath = Join-Path $root 'backend\.env'
if (-not (Test-Path -LiteralPath $envPath)) {
    throw "Cannot find backend\.env at $envPath"
}

$databaseUrl = (Get-Content -LiteralPath $envPath | Where-Object { $_ -match '^\s*DATABASE_URL\s*=' } | Select-Object -First 1)
if (-not $databaseUrl) {
    throw 'DATABASE_URL is not set in backend\.env'
}
$url = [System.Uri]($databaseUrl -replace '^\s*DATABASE_URL\s*=\s*', '')

if ($url.Scheme -ne 'postgresql' -and $url.Scheme -ne 'postgres') {
    throw "DATABASE_URL must use postgresql scheme, got $($url.Scheme)"
}

$username = [System.Uri]::UnescapeDataString($url.UserInfo.Split(':')[0])
$password = [System.Uri]::UnescapeDataString($url.UserInfo.Split(':')[1])
$database = [System.Uri]::UnescapeDataString($url.AbsolutePath.TrimStart('/'))

if (-not (Test-Path -LiteralPath $OutputDirectory)) {
    New-Item -ItemType Directory -Path $OutputDirectory -Force | Out-Null
}

$timestamp = Get-Date -Format 'yyyyMMdd-HHmmss'
$outputPath = Join-Path $OutputDirectory "adflow-$database-$timestamp.dump"

$pgDump = Get-Command pg_dump -ErrorAction SilentlyContinue
if (-not $pgDump) {
    throw 'pg_dump is not on PATH. Install PostgreSQL tools or add pg_dump to PATH.'
}

try {
    $env:PGPASSWORD = $password
    & $pgDump.Source --format=custom --no-owner --no-privileges --host=$($url.Host) --port=$($url.Port) --username=$username --file=$outputPath $database
} finally {
    Remove-Item Env:PGPASSWORD -ErrorAction SilentlyContinue
}

if ($LASTEXITCODE -ne 0) {
    throw "pg_dump failed with exit code $LASTEXITCODE."
}
if (-not (Test-Path -LiteralPath $outputPath) -or (Get-Item -LiteralPath $outputPath).Length -eq 0) {
    throw "Backup file was not created or is empty at $outputPath"
}

Write-Output $outputPath
