param(
  [string]$PostgresBin = 'D:\PostgreSQL\16\bin',
  [string]$HostName = 'localhost',
  [int]$Port = 5432,
  [string]$AdminUser = 'postgres',
  [string]$AppUser = 'adflow',
  [string]$DatabaseName = 'adflow'
)

$adminPassword = Read-Host "PostgreSQL administrator password for $AdminUser" -AsSecureString
$appPassword = Read-Host "New password for local application role $AppUser" -AsSecureString
$plainAdminPassword = [System.Net.NetworkCredential]::new('', $adminPassword).Password
$plainAppPassword = [System.Net.NetworkCredential]::new('', $appPassword).Password
$env:PGPASSWORD = $plainAdminPassword

$psql = Join-Path $PostgresBin 'psql.exe'
$createUser = "DO `$`$ BEGIN IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '$AppUser') THEN CREATE ROLE $AppUser LOGIN; END IF; END `$`$;"
& $psql -h $HostName -p $Port -U $AdminUser -d postgres -v ON_ERROR_STOP=1 -c $createUser
& $psql -h $HostName -p $Port -U $AdminUser -d postgres -v ON_ERROR_STOP=1 -c "ALTER ROLE $AppUser PASSWORD '$plainAppPassword'"
$exists = & $psql -h $HostName -p $Port -U $AdminUser -d postgres -tAc "SELECT 1 FROM pg_database WHERE datname = '$DatabaseName'"
if ($exists -notmatch '1') { & $psql -h $HostName -p $Port -U $AdminUser -d postgres -v ON_ERROR_STOP=1 -c "CREATE DATABASE $DatabaseName OWNER $AppUser" }

$env:PGPASSWORD = ''
Write-Host "Created or verified database '$DatabaseName' and local role '$AppUser'."
