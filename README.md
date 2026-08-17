# AdFlow 本地启动

AdFlow 是独立广告视频生成系统：上传参考视频 → AI 切分与校正 → 理解分镜 → 确认分镜事实 → 生成提示词 → 生成与结果，共六步。

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

## 六步工作流

1. **上传素材**：上传参考视频（4—30 秒）与目标产品/人物参考图。
2. **切分与校正**：AI 自动切分镜头，人工校正时间轴。
3. **理解分镜**：双模型（火山方舟 + Comfly GPT）逐镜分析画面事实。
4. **确认分镜事实**：人工确认每个镜头的事实与动作。
5. **生成提示词**：基于确认后的事实生成最终提示词，可保存多个版本。
6. **生成与结果**：提交 Seedance 生成，完成后本地播放、下载，支持版本重试与结果历史。

结果保存在 `data/media/{project_id}/generated/v{version}.mp4`。

## 数据库与迁移

数据库升级由 Alembic 追踪（`backend/alembic`）。启动脚本会在打开进程前自动执行 `upgrade head`。从旧安装升级、备份、回退的完整步骤见 [升级与恢复 Runbook](./docs/runbooks/adflow-local-upgrade.md)。

已有本地安装被标记为基线：

```powershell
Push-Location "E:\工具-商用\backend"
alembic -c alembic.ini stamp 0001_adflow_baseline
Pop-Location
```

迁移验证只对显式传入的副本/空库执行：

```powershell
.\scripts\verify-adflow-migrations.ps1 -ExistingCloneUrl "postgresql+psycopg://<user>:<pass>@localhost:5432/<clone>" -FreshDatabaseUrl "postgresql+psycopg://<user>:<pass>@localhost:5432/<fresh>"
```

## 配置

后台 Worker 会处理 Vision 与生成任务。真实火山能力需要在 `backend/.env` 配置 AK/SK、VOD 空间和 `VOLCENGINE_API_KEY`；Comfly 需要单独配置 API Key 与 Base URL。

参考视频发布到 tempfile.org 是 Worker 执行生成时自动触发的临时公网化步骤，链接约 24 小时有效；HTTP 创建生成请求本身不会外发素材。

## 设计参考

完整设计见 `docs/superpowers/specs/2026-08-15-adflow-local-generation-closed-loop-design.md`。
