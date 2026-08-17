param(
    [Parameter(Mandatory)][string]$ExistingCloneUrl,
    [Parameter(Mandatory)][string]$FreshDatabaseUrl
)
$ErrorActionPreference = 'Stop'
if ($ExistingCloneUrl -eq $FreshDatabaseUrl) { throw 'ExistingCloneUrl and FreshDatabaseUrl must be different databases.' }
foreach ($url in @($ExistingCloneUrl, $FreshDatabaseUrl)) {
    if ($url -notmatch '^postgresql(\+psycopg)?://') { throw "Only PostgreSQL URLs are accepted: $url" }
    if ($url -match '/adflow(?:\?|$)') { throw 'Do not pass the production adflow database; restore or create explicit verification databases first.' }
}

$root = Split-Path -Parent $PSScriptRoot
$backend = Join-Path $root 'backend'
$tempCounts = Join-Path ([System.IO.Path]::GetTempPath()) "adflow-clone-counts-$PID.json"

function Invoke-DbPython {
    param([string]$Url, [string]$Code)
    $env:DATABASE_URL = $Url
    $tempPy = Join-Path ([System.IO.Path]::GetTempPath()) "adflow-db-helper-$PID.py"
    try {
        Set-Content -LiteralPath $tempPy -Value $Code -Encoding UTF8
        $output = (& python $tempPy 2>&1 | Out-String).Trim()
        if ($LASTEXITCODE -ne 0) { throw "Database helper failed for $Url`: $output" }
        return $output
    } finally {
        Remove-Item -LiteralPath $tempPy -ErrorAction SilentlyContinue
    }
}

function Get-Counts {
    param([string]$Url)
    $code = @'
import json, os, sys
from sqlalchemy import create_engine, text
e = create_engine(os.environ["DATABASE_URL"])
with e.connect() as c:
    r = {t: c.execute(text("SELECT COUNT(*) FROM " + t)).scalar() for t in ("projects", "assets", "timeline_revisions", "prompt_revisions", "generations")}
json.dump(r, sys.stdout)
'@
    $output = Invoke-DbPython -Url $Url -Code $code
    return ($output | ConvertFrom-Json)
}

function Get-AlembicRevision {
    param([string]$Url)
    $code = @'
import os, sys
from sqlalchemy import create_engine, inspect
from alembic.migration import MigrationContext
e = create_engine(os.environ["DATABASE_URL"])
with e.connect() as c:
    rev = MigrationContext.configure(c).get_current_revision()
    print(rev or "none")
    tables = set(inspect(c).get_table_names())
    print("tables:" + ",".join(sorted(tables)))
    if "generations" in tables:
        cols = [col["name"] for col in inspect(c).get_columns("generations")]
        idx = [i["name"] for i in inspect(c).get_indexes("generations")]
        print("gencols:" + ",".join(sorted(cols)))
        print("genidx:" + ",".join(sorted(idx)))
'@
    return (Invoke-DbPython -Url $Url -Code $code)
}

try {
    Write-Host "=== Step 2: capture existing-clone counts before migration ==="
    $before = Get-Counts -Url $ExistingCloneUrl
    $before | ConvertTo-Json | Set-Content -LiteralPath $tempCounts -Encoding UTF8

    Write-Host "=== Step 3: stamp and upgrade existing clone ==="
    Push-Location $backend
    try {
        $env:DATABASE_URL = $ExistingCloneUrl
        $revisionInfo = Get-AlembicRevision -Url $ExistingCloneUrl
        if ($revisionInfo -match '^none') {
            python -m alembic -c alembic.ini stamp 0001_adflow_baseline
            if ($LASTEXITCODE -ne 0) { throw 'Alembic stamp failed for existing clone.' }
        }
        python -m alembic -c alembic.ini upgrade head
        if ($LASTEXITCODE -ne 0) { throw 'Alembic upgrade failed for existing clone.' }
    } finally {
        Pop-Location
    }
    $after = Get-Counts -Url $ExistingCloneUrl
    $beforeObj = Get-Content -LiteralPath $tempCounts -Raw | ConvertFrom-Json
    foreach ($table in @('projects', 'assets', 'timeline_revisions', 'prompt_revisions', 'generations')) {
        if (($beforeObj.$table) -ne $after.$table) {
            throw "Row count changed for $table after upgrade: before=$($beforeObj.$table) after=$($after.$table)"
        }
    }
    $cloneInfo = Get-AlembicRevision -Url $ExistingCloneUrl
    if ($cloneInfo -notmatch '0002_generation_closed_loop') { throw "Existing clone is not at head: $cloneInfo" }
    if ($cloneInfo -notmatch 'gencols:.*request_snapshot' -or $cloneInfo -notmatch 'gencols:.*completed_at') { throw 'Existing clone missing generation closed-loop columns.' }
    if ($cloneInfo -notmatch 'genidx:.*ix_generations_project_status' -or $cloneInfo -notmatch 'genidx:.*ix_generations_submission_fingerprint') { throw 'Existing clone missing generation indexes.' }
    Write-Host "Existing clone verified: head 0002_generation_closed_loop, counts unchanged."

    Write-Host "=== Step 4: upgrade empty database from base ==="
    Push-Location $backend
    try {
        $env:DATABASE_URL = $FreshDatabaseUrl
        python -m alembic -c alembic.ini upgrade head
        if ($LASTEXITCODE -ne 0) { throw 'Alembic upgrade failed for fresh database.' }
    } finally {
        Pop-Location
    }
    $freshInfo = Get-AlembicRevision -Url $FreshDatabaseUrl
    if ($freshInfo -notmatch '0002_generation_closed_loop') { throw "Fresh database is not at head: $freshInfo" }
    if ($freshInfo -notmatch 'tables:.*projects' -or $freshInfo -notmatch 'tables:.*generations') { throw 'Fresh database missing core tables.' }
    $freshCounts = Get-Counts -Url $FreshDatabaseUrl
    foreach ($table in @('projects', 'assets', 'timeline_revisions', 'prompt_revisions', 'generations')) {
        if ($freshCounts.$table -ne 0) { throw "Fresh database table $table is not empty after creation." }
    }
    Write-Host "Fresh database verified: head 0002_generation_closed_loop, all core tables present, all counts zero."
    Write-Host "VERIFICATION PASSED"
}
finally {
    Remove-Item Env:DATABASE_URL -ErrorAction SilentlyContinue
    Remove-Item -LiteralPath $tempCounts -ErrorAction SilentlyContinue
}
