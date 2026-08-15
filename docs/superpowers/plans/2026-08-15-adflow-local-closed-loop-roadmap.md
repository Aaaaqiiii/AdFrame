# AdFlow Local Closed Loop Implementation Roadmap

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 按可独立验收的五个实施包完成 AdFlow 本地 Seedance 生成闭环。

**Architecture:** 保留 React、FastAPI、PostgreSQL、本地媒体目录和数据库 Worker。先恢复可信测试基线，再固定业务规则与迁移，随后完成生成后端、第六步前端和真实环境验收。

**Tech Stack:** Python 3.11、FastAPI、SQLAlchemy 2、Alembic、PostgreSQL、pytest、React 19、TypeScript 6、Vite 8、Vitest、PowerShell、FFmpeg/FFprobe。

## Global Constraints

- Windows 单机部署，本地浏览器访问。
- `Project.mode` 创建后不可修改；只允许 `preserve_product` 和 `replace_product`。
- `preserve_product` 永远不得替换产品；`replace_product` 永远统一替换为已确认目标产品。
- 保留当前 `AppHeader + WorkflowRail + stage-host + stage-content` 页面结构和橙色视觉体系。
- 第五步只管理提示词；第六步独立负责生成、恢复、播放、下载和历史。
- Seedance 请求固定 `ratio="adaptive"`、`duration=-1`。
- PostgreSQL 与本地媒体目录是权威数据源；`localStorage` 只保存项目 ID。
- Alembic 是生产数据库结构变更的唯一机制；迁移失败时禁止启动。
- 不增加 Redis、Celery、Docker、账户系统、云存储、路由库、状态库或组件库。
- 每项实现遵循 TDD：先失败测试，再最小实现，再相关测试，再提交。

---

## Execution Order

1. [计划 1：恢复可信测试基线](./2026-08-15-adflow-01-test-baseline-implementation.md)
2. [计划 2：严格规则与统一迁移](./2026-08-15-adflow-02-rules-and-migrations-implementation.md)
3. [计划 3：可靠生成后端](./2026-08-15-adflow-03-generation-backend-implementation.md)
4. [计划 4：现有页面内的第六步](./2026-08-15-adflow-04-generation-frontend-implementation.md)
5. [计划 5：迁移、恢复与真实闭环验收](./2026-08-15-adflow-05-local-acceptance-implementation.md)

每个计划必须在自己的质量门槛通过后才进入下一份。计划 1—4 的每个任务单独提交；计划 5 只提交自动化测试、验收脚本和文档，不提交密钥、数据库备份或生成视频。

## Specification Coverage

| Design specification | Implemented and verified by |
|---|---|
| 严格双模式、素材语义、时间轴确认、提示词不可变版本 | 计划 2 任务 1—2；计划 3 任务 2；计划 4 任务 4 |
| 现有本地优先架构和依赖限制 | 所有计划的 Global Constraints |
| 保留当前页面并增加第六步 | 计划 4 任务 3—6 |
| 生成持久化字段和索引 | 计划 2 任务 3—4 |
| 提示词历史和生成 API | 计划 2 任务 2；计划 3 任务 1—3 |
| 六状态生成状态机 | 计划 3 任务 3—6；计划 4 任务 1、5 |
| Worker 提交、轮询、租约和原子下载 | 计划 3 任务 4—6 |
| 临时公网素材披露和 URL 续期 | 计划 3 任务 2、5；计划 4 任务 3 |
| 页面、API、Worker 重启恢复 | 计划 4 任务 5；计划 5 任务 1、5 |
| Alembic 唯一权威和保留数据迁移 | 计划 2 任务 4—5；计划 5 任务 2 |
| 错误、密钥脱敏和安全文件访问 | 计划 3 任务 2、4、6 |
| 自动化测试和 PostgreSQL 验证 | 计划 1—5 |
| 真实 Seedance 播放下载验收 | 计划 5 任务 4—5 |

## Cross-Plan Interfaces

后续计划统一使用以下名称，不得自行改名：

| File | Stable interface |
|---|---|
| `backend/app/services/product_rules.py` | `product_assets_for_project(session: Session, project: Project) -> list[Asset]` |
| `backend/app/services/product_rules.py` | `confirmed_target_product_assets(session: Session, project: Project) -> list[Asset]` |
| `backend/app/services/product_rules.py` | `contains_product_replacement(text: str) -> bool` |
| `backend/app/api/routes/projects.py` | `PromptRevisionSummary` |
| `backend/app/api/routes/generations.py` | `GenerationResponse`, `CreateGenerationRequest`, `ResolveGenerationRequest` |
| `backend/app/api/routes/generations.py` | `generation_response(project_id: UUID, generation: Generation) -> GenerationResponse` |
| `backend/app/services/generation_jobs.py` | `execute_generation_job(session: Session, generation: Generation, gateway: GenerationGateway, payload: dict) -> Generation` |
| `frontend/src/generationDomain.ts` | `GenerationStatus = 'queued' \| 'processing' \| 'retryable' \| 'submission_uncertain' \| 'failed' \| 'completed'` |
| `frontend/src/generationDomain.ts` | `isActiveGeneration(status: GenerationStatus): boolean` |
| `frontend/src/generationDomain.ts` | `canSubmitGeneration(input: GenerationSubmitInput): string` |
| `frontend/src/generationDomain.ts` | `sortGenerations(items: GenerationSummary[]): GenerationSummary[]` |

## Final Quality Gate

```powershell
Set-Location E:\工具-商用\backend
python -m pytest -q

Set-Location E:\工具-商用\frontend
npm.cmd test
npm.cmd run lint
npm.cmd run build
```

预期：后端全部通过且环境跳过为 0；前端测试、lint、build 全部成功。随后按计划 5 在 PostgreSQL 副本、新数据库和真实 4—30 秒参考视频上完成验收。
