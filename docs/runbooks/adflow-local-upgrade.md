# AdFlow 本地升级与恢复 Runbook

本文档描述从现有本地安装升级到六步闭环（含本地视频生成结果）的完整操作路径，以及恢复 `submission_uncertain` 和数据库备份回退的具体步骤。

## 前置条件

- Windows 11 本机，管理员权限 PowerShell。
- PostgreSQL 已运行（默认 `localhost:5432`，库名 `adflow`）。
- FFmpeg/FFprobe 已安装且在 PATH 中。
- Python 3.11+ 与 `backend` 的依赖已安装。
- 已存在 `backend/.env`，其中包含 `DATABASE_URL` 与（可选）`VOLCENGINE_API_KEY` / `COMFLY_API_KEY`。

```powershell
ffmpeg -version
ffprobe -version
psql --version
```

## 1. 备份正式数据库

在升级前必须备份。输出目录请使用独立的备份盘/目录，不要放在 `E:\工具-商用` 内（避免被 `.gitignore` 或清理脚本波及）。

```powershell
Set-Location E:\工具-商用
.\scripts\backup-adflow-database.ps1 -OutputDirectory 'D:\AdFlow-Backups'
```

## 2. 验证备份非空

脚本在 `pg_dump` 成功后已校验文件存在且非空。人工复核：

```powershell
Get-ChildItem 'D:\AdFlow-Backups' -Filter *.dump | Sort-Object LastWriteTime -Descending | Select-Object -First 1
```

## 3. 迁移行为

`start-adflow-local.ps1` 在启动 API/Worker 之前会同步执行：

```powershell
python -m alembic -c alembic.ini upgrade head
```

- 若本库从未记录 Alembic 版本（例如由旧版 `create_schema()` 建表），首次启动前需手动 `stamp` 到基线再升级：

```powershell
Set-Location E:\工具-商用\backend
alembic -c alembic.ini stamp 0001_adflow_baseline
alembic -c alembic.ini upgrade head
Set-Location E:\工具-商用
```

- 新安装（空库）不需要 stamp，直接 `upgrade head` 会创建全部表。
- 迁移失败时脚本会抛出错误并**拒绝启动任何进程**。不要绕过该检查。

## 4. 启动

```powershell
Set-Location E:\工具-商用
.\scripts\start-adflow-local.ps1
```

默认 API `8011`、Web `5174`。若端口占用，用 `-ApiPort` / `-WebPort` 指定。

## 5. 健康检查

```powershell
Invoke-RestMethod http://127.0.0.1:8011/api/health
```

应返回 `{"status":"ok"}`。

浏览器打开 `http://localhost:5174`。

## 6. 检查 Worker 进程

后台会启动一个隐藏 PowerShell 运行 `python -m app.worker`。确认其存在：

```powershell
Get-CimInstance Win32_Process -Filter "Name='python.exe'" | Where-Object { $_.CommandLine -match 'app.worker' }
```

## 7. 恢复 `submission_uncertain`

当生成任务状态为 `submission_uncertain`（供应商提交结果不明确）时：

1. **不要点击普通重试**——可能导致重复扣费。
2. 打开对应供应商控制台，按外部任务 ID 查任务是否真实创建。
3. 若任务存在：在第六步选择「绑定供应商任务 ID」，粘贴真实任务 ID。
4. 若任务不存在：选择「确认供应商未创建」，任务转为失败，可再点击「再次生成」。

## 8. 结果位置

每个项目的生成结果保存在本地媒体目录：

```
E:\工具-商用\data\media\{project_id}\generated\v{version}.mp4
```

浏览器播放使用 `/api/projects/{project_id}/generations/{generation_id}/content`，下载返回 MP4。

用 FFprobe 验证结果文件：

```powershell
ffprobe -v error -show_entries format=duration -of default=noprint_wrappers=1 "E:\工具-商用\data\media\<project_id>\generated\v1.mp4"
```

## 9. 迁移失败时回退

若 `upgrade head` 失败导致无法启动：

1. 用最近备份还原数据库：

```powershell
pg_restore --clean --no-owner --no-privileges --host=localhost --port=5432 --username=adflow --dbname=adflow "D:\AdFlow-Backups\adflow-adflow-<timestamp>.dump"
```

2. 重新运行 `start-adflow-local.ps1`。

## 重要说明

- **媒体文件单独备份**：`data/media` 不受数据库迁移影响，迁移绝不删除或移动媒体文件。备份数据库时请另用文件复制方式备份 `data\media`。
- 本 Runbook 假设正式库名为 `adflow`。若你的库名不同，同步修改 `DATABASE_URL` 与上述命令。
- 迁移验证脚本 `verify-adflow-migrations.ps1` 只接收显式传入的副本/空库 URL，**绝不操作正式库**。
