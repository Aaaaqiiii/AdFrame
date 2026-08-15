# AdFlow 本地启动

首次使用先完成 PostgreSQL 初始化与本地环境配置：

```powershell
& "E:\工具-商用\scripts\initialize-adflow-database.ps1"
& "E:\工具-商用\scripts\configure-adflow-local-env.ps1"
```

启动页面一、API 和后台 Worker：

```powershell
& "E:\工具-商用\scripts\start-adflow-local.ps1"
```

默认打开 `http://127.0.0.1:5174`，API 使用 `8011`，避免占用常见开发端口。若端口已被使用，可指定其他端口：

```powershell
& "E:\工具-商用\scripts\start-adflow-local.ps1" -ApiPort 8012 -WebPort 5175
```

For a company LAN test, first allow the selected ports in Windows Firewall, then replace `192.168.1.20` with this computer's LAN IP:
```powershell
& "E:\工具-商用\scripts\start-adflow-local.ps1" -BindHost 0.0.0.0 -ApiPublicHost 192.168.1.20
```

Database upgrades are tracked in `backend/alembic`. After a database backup, existing local installations can be marked as the baseline using:
```powershell
Push-Location "E:\工具-商用\backend"
alembic -c alembic.ini stamp 0001_adflow_baseline
Pop-Location
```

后台 Worker 会处理 Vision 与生成任务。真实火山能力需要在 `backend/.env` 配置 AK/SK、VOD 空间和 `VOLCENGINE_API_KEY`；Comfly 需要单独配置 API Key 与 Base URL。

参考视频发布到 tempfile.org 是用户主动触发的临时公网化步骤，链接约 24 小时有效。
