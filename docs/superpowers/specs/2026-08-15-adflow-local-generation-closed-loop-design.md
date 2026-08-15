# AdFlow 本地生成闭环设计规格

**状态：** 待用户审阅

**日期：** 2026-08-15

**目标版本：** 本地可实际使用版本

**部署范围：** Windows 单机，本地浏览器访问

## 1. 目标

本阶段把当前已能运行的素材上传、时间轴校正、逐镜理解和提示词流程，补齐为可以实际完成一次广告视频生成的本地闭环：

1. 创建项目并选择不可变的业务模式。
2. 上传参考视频和必要参考图片。
3. 用 FFmpeg 建立候选镜头并提取证据帧。
4. 人工校正并确认时间轴。
5. 用豆包完整镜头理解和 GPT 连续关键帧理解生成逐镜事实。
6. 人工修改并确认每个镜头事实。
7. 生成、修改并保存不可变的提示词版本。
8. 在独立的第六步选择生成通道和附加输入。
9. 提交 Seedance，持久化供应商任务 ID，恢复和轮询任务。
10. 将结果下载到本地，支持播放、下载、查看历史和再次生成。

完成后的核心成功标准是：用户关闭页面或重启 API 后，已提交的后台任务不会丢失；再次打开项目能继续看到真实状态和本地结果。

## 2. 非目标

本阶段不增加以下能力：

- Redis、Celery或其他消息队列。
- Docker和容器编排。
- 多用户账户、权限、计费和团队协作。
- 云对象存储。
- 自动生成质量评审。
- Webhook回调。
- 生成任务取消。
- 复杂的前端全局状态管理库。
- 对当前五步页面的整体视觉重做。

## 3. 固定业务规则

### 3.1 严格双模式

项目创建后，`Project.mode` 不允许修改。

#### `preserve_product`

- 始终保留参考视频中的原产品。
- 产品图只用于辅助核对包装、Logo、颜色和结构。
- 镜头编辑、提示词和生成请求都不得表达产品替换。
- 页面中不再显示“替换产品”开关和目标产品上传入口。
- 服务端拒绝任何试图绕过前端开启产品替换的请求。

#### `replace_product`

- 必须在第一步上传目标产品图片。
- 目标产品档案必须分析成功并由用户确认。
- 所有出现原产品的镜头统一使用同一份目标产品档案。
- 最终提示词必须确定性包含完整目标产品锁定档案。
- 产品形态和镜头动作发生明确冲突时阻止生成提示词。

人物替换是两个模式都可使用的独立能力。人物档案必须成功并被用户确认后，才能开启人物替换。

### 3.2 现有素材类型兼容

当前数据库中的 `product_reference_image` 在不同项目模式下含义不同：

- `preserve_product` 项目中代表原产品辅助图。
- `replace_product` 项目中代表目标产品图。

本阶段不批量重命名已有 `Asset.kind`，避免迁移本地素材目录和历史记录。服务端新增统一的“按项目模式解析素材角色”函数，所有路由和服务通过该函数读取产品素材。

历史 `target_product_reference_image` 数据继续保留，但不再由严格双模式界面创建，也不进入新的提示词和生成请求。未来如确有第三种业务流程，再单独设计迁移。

### 3.3 时间轴与镜头确认

- 人工保存时间轴时创建新 `TimelineRevision`，不得修改旧版本。
- 边界完全相同的镜头可复用成功的 AI 事实和人工确认。
- 边界变化的镜头可复制旧内容作为草稿，但必须撤销确认并重新分析。
- 旧时间轴上的活跃分析任务标记为 `superseded`。
- 最终提示词只能读取当前时间轴上已人工确认的 `ShotEdit`。
- 任一当前镜头未确认时，禁止生成提示词。

### 3.4 提示词版本

- `PromptRevision` 创建后不可原地修改。
- 人工保存、AI生成和GPT修改都创建新版本。
- AI提示词任务只能读取已确认镜头事实和已确认参考档案。
- `replace_product` 由项目模式确定，不再接受前端自由开关。
- `PromptRevision.replace_product` 继续保留，作为生成当时规则的历史快照。
- 提交Seedance时，提示词必须为 `completed`，并且 `source_timeline_revision_id` 必须等于项目当前时间轴版本。
- 旧时间轴对应的提示词继续显示在历史中，但不能用于新生成。

## 4. 总体架构

继续采用当前本地优先结构：

```text
React 工作流界面
        |
        v
FastAPI 业务 API
   |           |
   v           v
PostgreSQL   本地 media 目录
   |
   v
独立数据库 Worker
   |        |        |
   v        v        v
视觉理解  提示词GPT  Seedance供应商
```

职责边界：

- 前端负责采集选择、呈现状态和触发明确操作，不决定产品模式规则。
- FastAPI负责业务校验、版本创建、任务入队、文件访问和安全边界。
- PostgreSQL是项目版本和任务状态的唯一权威来源。
- Worker负责所有耗时外部调用、自动重试、供应商轮询和结果下载。
- 本地媒体目录是原始素材和最终结果的永久副本。
- TempFile链接只是临时传输介质，不是永久素材存储。
- Alembic是数据库结构升级的唯一机制。

本阶段不把现有单体拆成微服务，也不增加仓储层、依赖注入容器或只有一个实现的抽象接口。

## 5. 页面与交互设计

### 5.1 保留现有页面框架

必须保留当前实际界面结构：

- 顶部 `AppHeader`。
- 页面一和页面二模式入口。
- 左侧 `WorkflowRail`。
- 右侧 `stage-host` 和白色 `stage-content`。
- 当前橙色强调色、按钮、圆角、间距和全局状态条。

禁止把左侧工作流改成顶部步骤条，也不重做前五步页面。

### 5.2 六步流程

左侧流程变为：

1. 上传素材。
2. 切分与校正。
3. 理解分镜。
4. 确认分镜事实。
5. 生成提示词。
6. 生成与结果。

第五步只负责提示词，不能直接提交视频生成。第六步负责所有付费生成操作和结果管理。

### 5.3 第六步布局

新增 `GenerationStage`，复用当前第五步的“主内容区加320px侧栏”布局。

主内容区按顺序显示：

1. **生成依据：** 选择当前时间轴下状态为 `completed` 的提示词版本，可展开查看全文。
2. **生成通道：** 火山官方或Comfly。
3. **附加输入：** 生成音频、人物参考图、背景参考图。
4. **临时上传清单：** 明确列出本次将上传的本地文件和未选择的文件。
5. **提交摘要：** 显示将创建的新生成版本并提供唯一主按钮“确认并生成视频”。

右侧栏显示：

- 当前活跃任务及状态。
- 供应商、提示词版本、运行时间和任务ID。
- 当前任务完成后直接切换为播放器。
- 最新完成结果的播放和下载入口。

主内容区下方显示生成历史：

- 生成版本。
- 供应商。
- 提示词版本。
- 创建时间。
- 当前状态。
- 查看详情、播放、下载、再次生成或失败重试操作。

### 5.4 页面状态

第六步必须覆盖以下状态：

- 未保存有效提示词：显示阻止原因和返回第五步入口。
- 供应商未配置：显示阻止原因和打开设置入口。
- 无生成历史：右侧显示空状态，不显示伪播放器。
- `queued`：已进入本地队列。
- `processing`：显示供应商任务ID和自动刷新状态。
- `retryable`：显示自动重试次数和下一次重试提示。
- `submission_uncertain`：显示风险说明及人工处理入口，禁止普通重试按钮。
- `failed`：显示经过清理的错误信息和“创建新生成版本”操作。
- `completed`：优先播放本地文件，支持下载和再次生成。
- 结果记录存在但本地文件丢失：明确显示本地文件丢失，不静默切换到临时供应商URL。

### 5.5 前端文件边界

新增最少文件：

- `frontend/src/components/GenerationStage.tsx`：第六步界面。
- `frontend/src/generationDomain.ts`：纯函数，包括终态判断、状态文案、可提交校验和历史排序。
- `frontend/src/generationDomain.test.ts`：纯函数测试。

修改：

- `frontend/src/App.tsx`：只增加生成状态、API回调和第六步挂载。
- `frontend/src/api.ts`：增加生成列表、详情、重试和不明确提交处理API。
- `frontend/src/workspaceDomain.ts`：增加 `generation` 工作流阶段。
- `frontend/src/components/WorkflowRail.tsx`：增加第六步。
- `frontend/src/App.css`：仅增加与现有设计一致的生成页面样式。

不引入Redux、Zustand、React Query、路由库和新组件库。

生成任务轮询封装为一个局部 `useEffect`，只依赖项目ID和活跃生成ID集合。不得复用当前素材轮询的大依赖数组，避免无关状态变化反复创建定时器。

## 6. 生成数据设计

继续使用现有 `Generation` 表，并增加：

- `request_snapshot: Text | null`：去除密钥后的最终供应商请求JSON。
- `reference_asset_ids: Text | null`：本次选择的参考素材ID JSON数组。
- `provider_response_summary: Text | null`：提交响应摘要，不保存敏感请求头。
- `submission_fingerprint: String(64) | null`：本次输入的SHA-256指纹。
- `completed_at: DateTime | null`：本地结果验证并保存成功时间。

继续使用现有字段：

- `project_id`
- `version`
- `prompt_version`
- `provider`
- `ratio`
- `duration`
- `generate_audio`
- `external_task_id`
- `status`
- `result_url`
- `result_path`
- `error_message`
- `attempts`
- `next_attempt_at`
- `leased_at`
- `leased_by`
- `created_at`

新增索引：

- `(project_id, status)`，用于恢复活跃任务。
- `(status, next_attempt_at)`，用于Worker领取任务。
- `submission_fingerprint` 普通索引，用于检测活跃重复提交。

不对 `submission_fingerprint` 建唯一约束，因为用户允许在旧任务完成后使用相同输入再次生成。

生成版本号在锁定项目行后计算，避免两个快速请求得到相同版本。

## 7. API设计

### 7.1 生成列表

```http
GET /api/projects/{project_id}/generations
```

返回该项目全部生成版本，按版本倒序。每项包含：

```json
{
  "id": "uuid",
  "version": 4,
  "prompt_version": 4,
  "provider": "volcengine",
  "status": "processing",
  "external_task_id": "task-id",
  "attempts": 0,
  "next_attempt_at": null,
  "local_video_url": null,
  "error_message": null,
  "created_at": "2026-08-15T11:18:00Z",
  "completed_at": null
}
```

### 7.2 创建生成

```http
POST /api/projects/{project_id}/generations
```

请求：

```json
{
  "provider": "volcengine",
  "prompt_version": 4,
  "generate_audio": true,
  "include_person_reference": false,
  "include_background_reference": false
}
```

前端不再提交 `ratio` 和 `duration`。服务端固定为：

```json
{
  "ratio": "adaptive",
  "duration": -1
}
```

创建前必须验证：

- 项目存在且模式合法。
- 提示词存在并为 `completed`。
- 提示词来源是当前时间轴。
- 当前时间轴所有镜头仍已确认。
- 严格双模式规则成立。
- 参考视频存在、可读且不超过30秒。
- 被选择的参考图片存在并已确认。
- 供应商API Key已经配置。
- 同一 `submission_fingerprint` 没有活跃任务。

API只创建数据库记录并返回 `202`，不在HTTP请求内调用生成供应商。

### 7.3 获取详情

```http
GET /api/projects/{project_id}/generations/{generation_id}
```

必须正确计算 `local_video_url`。当前实现直接返回ORM对象时不会填充该字段，本阶段必须统一使用响应构造函数。

### 7.4 失败后再次生成

```http
POST /api/projects/{project_id}/generations/{generation_id}/retry
```

仅允许对 `failed` 任务调用。该接口复制原任务设置并创建新的 `Generation.version`，不得修改原记录。

技术性 `retryable` 由Worker自动处理，不显示人工重试按钮。

### 7.5 处理不明确提交

```http
POST /api/projects/{project_id}/generations/{generation_id}/resolve
```

两种操作：

```json
{"action": "attach_task", "external_task_id": "provider-task-id"}
```

或：

```json
{"action": "confirm_not_created"}
```

只有 `submission_uncertain` 可以调用。附加任务ID后进入 `processing`；确认供应商未创建后进入 `failed`，用户随后可以创建新版本。

### 7.6 本地结果

```http
GET /api/projects/{project_id}/generations/{generation_id}/content
```

必须验证生成记录属于项目、`result_path` 指向存在的普通文件，并以 `video/mp4` 下载。不得接受客户端传入任意文件路径。

## 8. 生成状态机

合法状态：

- `queued`
- `processing`
- `retryable`
- `submission_uncertain`
- `failed`
- `completed`

状态转换：

```text
queued -> processing
queued -> retryable
queued -> submission_uncertain
processing -> processing
processing -> retryable
processing -> failed
processing -> completed
retryable -> queued
submission_uncertain -> processing
submission_uncertain -> failed
```

定义：

- `queued`：本地任务已创建，尚未取得供应商任务ID。
- `processing`：已持久化供应商任务ID，正在查询结果。
- `retryable`：发生确定可恢复的技术错误，Worker将在 `next_attempt_at` 后重试。
- `submission_uncertain`：创建请求结果不明确，不能证明供应商未创建任务。
- `failed`：明确失败或人工确认未创建。
- `completed`：供应商成功、本地视频下载完成且FFprobe验证通过。

供应商显示成功但本地下载失败时必须保持 `retryable`，不能提前标记 `completed`。

## 9. Worker设计

继续使用数据库租约和Windows单进程锁：

- 每批最多领取5条记录。
- 租约10分钟过期。
- `queued`、`processing` 和到期的 `retryable` 可被领取。
- `submission_uncertain` 不参与自动领取。
- 自动重试使用现有指数退避和最大尝试次数。
- 任务处理结束后释放租约。

提交步骤：

1. 读取不可变的提示词版本和本地参考素材ID。
2. 检查临时公网链接是否存在且未过期。
3. 仅发布本次需要的文件。
4. 构建不含密钥的请求快照并保存。
5. 调用供应商创建接口。
6. 得到任务ID后立即保存 `external_task_id` 和 `processing`。

若请求在取得明确响应前超时：

- 不自动重新提交。
- 保存可安全记录的响应摘要。
- 标记 `submission_uncertain`。

轮询步骤：

1. 使用已持久化的任务ID查询。
2. 供应商仍在运行时保持 `processing` 并设置下一次查询时间。
3. 查询暂时失败时进入 `retryable`。
4. 供应商明确失败时进入 `failed`。
5. 供应商成功时下载到项目 `generated/v{version}.mp4.part`。
6. 限制最大下载体积。
7. 用FFprobe验证文件。
8. 原子重命名为 `generated/v{version}.mp4`。
9. 保存 `result_path`、`completed_at` 并进入 `completed`。

## 10. 临时公网素材

- 用户点击“确认并生成视频”表示同意本次上传清单中的文件被临时发布。
- 参考视频始终需要公网可读地址。
- 人物图和背景图只有勾选后才发布。
- 临时URL约24小时有效。
- URL过期后，Worker根据本地素材ID自动重新发布。
- 本地文件始终是永久副本。
- 前端不展示完整带查询参数的临时URL。
- 手动“发布参考视频”接口不再作为正常工作流入口；如测试仍需要，可保留为内部兼容接口，但界面不显示。

## 11. 项目恢复与轮询

打开项目时：

1. 获取项目详情。
2. 获取生成历史。
3. 识别所有非终态任务。
4. 每3秒刷新非终态任务。
5. 所有任务进入 `completed`、`failed` 或 `submission_uncertain` 后停止轮询。

前端不能把 `localStorage` 当作任务状态来源。`localStorage` 只保存当前项目ID；生成状态全部来自API。

项目恢复时必须保持用户当前正在编辑的步骤和镜头选择。生成轮询不得调用会重置整个项目编辑状态的全量恢复函数，只更新生成状态。

## 12. 数据库迁移统一

### 12.1 唯一权威

完成迁移后：

- Alembic是唯一数据库结构变更机制。
- `create_app()` 不再执行 `Base.metadata.create_all()`。
- `create_schema()` 中的手写 `ALTER TABLE` 全部删除。
- 应用启动时只检查数据库版本，不自行修改结构。

### 12.2 保留现有数据

升级流程：

1. 用 `pg_dump` 备份当前 `adflow` 数据库。
2. 验证备份文件存在且大小大于0。
3. 检查是否存在 `alembic_version`。
4. 未纳入Alembic的现有安装执行 `alembic stamp 0001_adflow_baseline`。
5. 执行 `alembic upgrade head`。
6. 验证核心表、列、索引和当前项目数量。
7. 不修改或删除 `data/media`。

### 12.3 新安装

重写当前 `0001_adflow_baseline`，使全新空数据库执行该迁移后可以创建完整现有结构。已存在并已stamp到0001的数据库不会重新执行0001。

新增 `0002_generation_closed_loop`：

- 增加本规格第6节字段。
- 增加生成任务索引。
- 不删除历史列和历史数据。

### 12.4 启动脚本

`scripts/start-adflow-local.ps1` 在启动API和Worker前执行：

```powershell
python -m alembic -c alembic.ini upgrade head
```

迁移失败时禁止继续启动。Alembic从开发依赖移动到运行时依赖。

新增 `scripts/backup-adflow-database.ps1`，使用当前 `.env` 中的数据库连接参数调用 `pg_dump`，备份到用户指定目录，不把密码写入命令日志。

## 13. 错误处理和安全边界

### 13.1 用户可见错误

错误信息按动作分类：

- 配置缺失：指出缺少哪个供应商配置。
- 素材失效：指出具体文件或档案。
- 业务校验失败：指出模式、镜头或提示词版本。
- 临时发布失败：保留本地文件并允许重新提交。
- 供应商失败：显示供应商返回的清理后消息。
- 本地下载失败：明确是结果保存失败，不误报生成失败。
- 数据库版本落后：提示先运行迁移。

前端3秒轮询可以容忍一次短暂失败，但连续失败后必须在第六步显示连接异常，不能永久静默吞掉错误。

### 13.2 密钥

- API Key只保存在后端 `.env`。
- 设置接口继续限制为本机回环地址。
- API响应、数据库请求快照和日志不得包含密钥。
- 请求头和临时URL查询参数在日志中脱敏。
- 不在前端保存密钥。

### 13.3 文件

- 上传文件大小、类型和可解码性继续在服务端验证。
- 生成结果先写 `.part`，验证后原子替换。
- 文件下载只能使用数据库中已验证的路径。
- 不允许通过API提交本地路径。

## 14. 测试设计

### 14.1 后端测试先恢复可信状态

当前后端测试结果为84通过、17失败、3错误。实施开始前先分类修复：

- 更新仍假设HTTP内同步生成提示词的旧测试。
- SQLAlchemy UUID列查询统一使用 `UUID` 对象。
- FFmpeg测试在工具不可执行时明确跳过，并在本地完整环境中运行。
- 保证测试始终使用隔离SQLite，绝不连接正式PostgreSQL。

不得为了让测试变绿而删除仍然有效的业务断言。

### 14.2 严格双模式测试

必须覆盖：

- 保留产品项目无法通过API开启产品替换。
- 替换产品项目缺少已确认目标产品时不能生成提示词或视频。
- 模式从项目读取而不是从前端请求读取。
- 历史 `target_product_reference_image` 不进入新生成请求。
- 人物替换缺少确认档案时被阻止。

### 14.3 提示词测试

必须覆盖：

- 每个镜头未确认时不能生成提示词。
- AI提示词在Worker中异步生成。
- 人工保存创建新版本。
- GPT修改创建新版本且旧版本不变。
- 旧时间轴提示词不能提交生成。
- 只有 `completed` 提示词可选。

### 14.4 生成API测试

必须覆盖：

- 创建API只入队，不调用供应商。
- 服务端强制 `adaptive` 和 `-1`。
- 重复活跃指纹被拒绝。
- 完成后允许相同输入再次生成。
- 被勾选的素材ID正确保存。
- 未勾选图片不发布。
- 临时URL过期时重新发布。
- `GET`详情正确返回 `local_video_url`。
- 失败重试创建新版本且不修改原任务。
- 不明确提交不能自动重试。
- 不明确提交可以附加任务ID或确认未创建。

### 14.5 Worker测试

必须覆盖：

- 任务ID取得后立即持久化。
- 供应商轮询状态正确映射。
- 查询暂时失败进入退避。
- 租约过期后可以恢复任务。
- 已完成供应商任务在本地下载失败时保持可重试。
- 结果超过限制时失败。
- FFprobe失败不保留正式结果文件。
- `.part` 文件得到清理。
- 完成时设置 `completed_at`。

### 14.6 前端测试

继续使用Vitest纯函数测试，不新增React Testing Library：

- 第六步解锁条件。
- 生成状态中文文案和色调。
- 活跃任务和终态判断。
- 生成历史排序。
- 可提交阻止原因。
- 轮询只在存在活跃任务时继续。

验证命令：

```powershell
cd backend
python -m pytest -q

cd ..\frontend
npm.cmd test
npm.cmd run lint
npm.cmd run build
```

### 14.7 PostgreSQL迁移验证

SQLite测试不能替代PostgreSQL迁移验证。至少执行：

1. 从现有数据库备份恢复一份副本。
2. 对副本执行stamp和upgrade。
3. 验证项目、素材、时间轴、提示词和生成数量不变。
4. 对全新空数据库执行完整upgrade。
5. 启动API并验证health和项目列表。

## 15. 验收标准

### 15.1 完整闭环

- 用户能从新项目完成素材上传、镜头校正、逐镜确认和提示词确认。
- 第六步能选择火山官方或Comfly并创建生成任务。
- 创建响应立即返回，浏览器不会等待外部生成完成。
- 页面关闭后Worker继续运行。
- 重新打开项目能恢复任务和历史。
- 成功结果下载到本地并可播放、下载。
- 再次生成创建新版本，不覆盖旧结果。

### 15.2 业务规则

- 保留产品项目不存在任何产品替换入口。
- 直接调用API也不能绕过保留产品规则。
- 替换产品项目没有确认目标产品时不能继续。
- 生成只使用当前有效时间轴和已完成提示词。

### 15.3 可靠性

- API或浏览器重启不丢任务。
- Worker重启后能继续查询已持久化任务ID。
- 不明确提交不会自动重复创建任务。
- 临时URL过期时可以从本地文件重新发布。
- 供应商结果URL过期后，本地结果仍可播放。

### 15.4 数据升级

- 现有项目和媒体数据完整保留。
- 全新数据库可以仅靠Alembic建立。
- 应用启动不再自行修改数据库结构。

### 15.5 质量门槛

- 后端完整测试通过，非环境跳过项为0。
- 前端测试、lint和build通过。
- 在4至30秒真实参考视频上完成至少一次官方通道端到端生成。
- 在同一项目中验证关闭页面后恢复任务和下载结果。

## 16. 实施顺序

实施拆为五个可独立验证的阶段：

1. **基线恢复：** 修复旧测试，使当前行为有可信基线。
2. **规则和迁移：** 落实严格双模式，Alembic接管数据库。
3. **生成后端：** 完成状态机、可靠提交、恢复、结果下载和API。
4. **第六步前端：** 在现有页面结构内接通生成与结果。
5. **真实验收：** PostgreSQL升级验证和一次受控的真实Seedance闭环。

每个阶段必须在进入下一阶段前通过其相关测试。不得先重构无关代码，也不得在本阶段引入消息队列或新的前端框架。
