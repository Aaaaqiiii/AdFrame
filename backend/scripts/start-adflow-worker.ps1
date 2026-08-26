param(
    [ValidateSet('all', 'ai', 'generation')][string]$Queue = 'all',
    [ValidateRange(1, 4)][int]$Slot = 1
)
$ErrorActionPreference = 'Stop'
Set-Location (Join-Path $PSScriptRoot '..')
python -m alembic -c alembic.ini upgrade head
if ($LASTEXITCODE -ne 0) { throw "Database migration failed with exit code $LASTEXITCODE." }
python -m app.worker --queue $Queue --slot $Slot
