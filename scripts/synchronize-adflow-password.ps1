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
$psql = Join-Path $PostgresBin 'psql.exe'

try {
  $env:PGPASSWORD = $plainAdminPassword
  $sqlPassword = $plainAppPassword.Replace("'", "''")
  & $psql -h $HostName -p $Port -U $AdminUser -d postgres -v ON_ERROR_STOP=1 -c "ALTER ROLE $AppUser WITH LOGIN PASSWORD '$sqlPassword'"
  if ($LASTEXITCODE -ne 0) { throw 'Unable to update the adflow database role.' }

  $databaseExists = & $psql -h $HostName -p $Port -U $AdminUser -d postgres -tAc "SELECT 1 FROM pg_database WHERE datname = '$DatabaseName'"
  if ($LASTEXITCODE -ne 0) { throw 'Unable to check whether the adflow database exists.' }
  if ($databaseExists -ne '1') {
    & $psql -h $HostName -p $Port -U $AdminUser -d postgres -v ON_ERROR_STOP=1 -c "CREATE DATABASE $DatabaseName OWNER $AppUser"
    if ($LASTEXITCODE -ne 0) { throw 'Unable to create the adflow database.' }
  }

  $env:PGPASSWORD = $plainAppPassword
  $connectedUser = & $psql -h $HostName -p $Port -U $AppUser -d $DatabaseName -v ON_ERROR_STOP=1 -tAc 'SELECT current_user'
  if ($LASTEXITCODE -ne 0 -or $connectedUser -ne $AppUser) { throw 'Application database login verification failed.' }

  $encodedPassword = [Uri]::EscapeDataString($plainAppPassword)
$projectRoot = Split-Path -Parent $PSScriptRoot
$envPath = Join-Path $projectRoot 'backend\.env'
$mediaRoot = Join-Path $projectRoot 'data\media'
  $content = @"
DATABASE_URL=postgresql+psycopg://$AppUser`:$encodedPassword@$HostName`:$Port/$DatabaseName
MEDIA_ROOT=$mediaRoot
APP_LICENSE_PATH=D:\AdFlow\license.json
APP_LICENSE_PUBLIC_KEY_PATH=D:\AdFlow\license-public.pem
VOLCENGINE_API_KEY=
VOLCENGINE_SEEDANCE_BASE_URL=https://ark.cn-beijing.volces.com
VOLCENGINE_SEEDANCE_TASK_PATH=/api/v3/contents/generations/tasks
VOLCENGINE_SEEDANCE_MODEL=doubao-seedance-2-5-260628
COMFLY_API_KEY=
COMFLY_BASE_URL=https://ai.comfly.chat
COMFLY_SEEDANCE_TASK_PATH=/seedance/v3/contents/generations/tasks
COMFLY_SEEDANCE_MODEL=doubao-seedance-2.5
OPENAI_API_KEY=
"@
  [System.IO.File]::WriteAllText($envPath, $content, [System.Text.UTF8Encoding]::new($false))
  Write-Host "Database role and local backend configuration are synchronized."
}
finally {
  $env:PGPASSWORD = ''
}
