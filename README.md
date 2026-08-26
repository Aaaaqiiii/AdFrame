# AdFrame

AdFrame 是一个本地运行的广告视频改编工作台。它把参考视频切分、逐镜视觉理解、人物/产品替换约束、提示词版本管理和 Seedance 分批生成放在同一条可人工校对的工作流中。

> 当前项目面向 Windows 本地部署。供应商调用会产生外部 API 费用；只有用户主动创建生成批次后，系统才会向 Seedance 提交视频任务。

## 核心能力

- 上传参考视频，以及产品、人物和背景参考图。
- 使用 FFmpeg 生成候选切点，并通过人工时间轴逐帧校正镜头边界。
- 使用 Qwen 读取完整镜头；多个 AI worker 可并行理解不同镜头。
- 保存和确认逐镜事实，人工修改不会被后续 AI 结果静默覆盖。
- 支持保留产品与替换产品两种流程，并约束人物身份、包装形态和产品卖点。
- 生成参考视频改编指令或可独立使用的视频复刻提示词，保留历史版本。
- 使用火山或 Comfly Seedance 分批生成、自动轮询、失败重试和本地结果落盘。
- 所有片段完成后，通过 FFmpeg 无损合并并下载完整视频。
- 历史项目可以恢复、重命名和删除；后台任务不依赖当前打开的页面继续执行。

## 工作流

1. **上传素材**：参考视频、目标产品图，以及可选人物/背景参考图。
2. **切分与校正**：本地检测候选切点，人工确认镜头边界。
3. **生成分段**：按照供应商时长限制规划生成片段。
4. **理解分镜**：Qwen 逐镜分析完整视频片段。
5. **确认分镜事实**：人工修改并确认人物、动作、产品、场景和镜头事实。
6. **生成提示词**：生成、编辑、精修并保存提示词版本。
7. **生成与结果**：创建 Seedance 批次，播放、重试、下载或合并结果。

## 技术结构

```text
frontend/                 React 19 + TypeScript + Vite
backend/                  FastAPI + SQLAlchemy + PostgreSQL
backend/app/worker.py     AI 与视频生成后台 worker
backend/alembic/          数据库迁移
scripts/                  Windows 初始化、启动、备份与迁移验证脚本
docs/                     设计、验收说明和运维 Runbook
data/                     本地媒体与生成结果（不会提交到 Git）
```

默认启动进程包括一个 API、三个 AI worker、两个生成 worker 和一个 Vite 前端。逐镜理解与视频生成使用相互独立的队列。

## 环境要求

- Windows PowerShell 5.1 或 PowerShell 7
- Python 3.11+
- Node.js 20+
- PostgreSQL 16（其他受支持版本需自行验证）
- FFmpeg 与 FFprobe，且两者均已加入 `PATH`

## 安装

克隆仓库：

```powershell
git clone https://github.com/Aaaaqiiii/AdFrame.git
Set-Location AdFrame
```

安装后端与前端依赖：

```powershell
python -m venv .venv
& .\.venv\Scripts\Activate.ps1
python -m pip install -e ".\backend[dev]"

Push-Location .\frontend
npm ci
Pop-Location
```

初始化 PostgreSQL。若 PostgreSQL 不在默认目录，请传入实际的 `bin` 路径：

```powershell
& .\scripts\initialize-adflow-database.ps1 -PostgresBin "C:\Program Files\PostgreSQL\16\bin"
& .\scripts\configure-adflow-local-env.ps1
```

随后编辑 `backend/.env`，或者启动系统后在“设置”页面填写供应商凭证。可用变量清单见 [`backend/.env.example`](./backend/.env.example)。至少需要：

- `COMFLY_API_KEY`：Qwen 视觉理解、提示词处理和 Comfly Seedance。
- `VOLCENGINE_API_KEY`：火山 Seedance；仅使用 Comfly 生成时可以留空。
- `DATABASE_URL`：本地 PostgreSQL 连接地址。
- `MEDIA_ROOT`：参考素材与生成结果的本地目录。

`backend/.env`、`data/`、前端构建产物和依赖目录均已加入 `.gitignore`。

## 启动

```powershell
& .\scripts\start-adflow-local.ps1
```

默认地址：

- 前端：`http://127.0.0.1:5174`
- API：`http://127.0.0.1:8011`

修改端口或 worker 数量：

```powershell
& .\scripts\start-adflow-local.ps1 -ApiPort 8012 -WebPort 5175 -AiWorkers 3 -GenerationWorkers 2
```

局域网访问默认关闭。需要局域网测试时，应先配置 Windows 防火墙，再显式传入监听地址与本机局域网 IP：

```powershell
& .\scripts\start-adflow-local.ps1 -BindHost 0.0.0.0 -ApiPublicHost 192.168.1.20
```

## 测试与构建

后端：

```powershell
Push-Location .\backend
python -m pytest -q
Pop-Location
```

前端：

```powershell
Push-Location .\frontend
npm test
npm run build
npm run lint
Pop-Location
```

## 数据库升级与恢复

启动脚本会在启动服务前自动执行 `alembic upgrade head`。升级已有安装前应先备份数据库：

```powershell
& .\scripts\backup-adflow-database.ps1
```

完整升级、迁移验证和恢复流程见 [本地升级 Runbook](./docs/runbooks/adflow-local-upgrade.md)。

## 安全说明

- 不要提交 `backend/.env`、数据库备份、参考素材或生成视频。
- tempfile.org 临时链接用于向视频供应商传递参考素材，并会按服务端有效期失效。
- 对外开放 API 前，应另行配置身份认证、HTTPS、访问控制和持久化对象存储；当前默认配置只面向受信任的本机环境。

## 当前状态

本地工作流、任务恢复、并行逐镜理解、提示词版本、Seedance 分批生成和完整视频合并均已有自动化测试覆盖。真实供应商是否可用仍取决于账号权限、模型开通状态、网络质量和临时素材服务可用性。
