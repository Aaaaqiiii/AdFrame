param(
  [string]$AppUser = 'adflow',
  [string]$DatabaseName = 'adflow',
  [string]$HostName = 'localhost',
  [int]$Port = 5432
)

$appPassword = Read-Host "Password for local application role $AppUser" -AsSecureString
$plainAppPassword = [System.Net.NetworkCredential]::new('', $appPassword).Password
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

Write-Host "Local backend configuration written to $envPath"
