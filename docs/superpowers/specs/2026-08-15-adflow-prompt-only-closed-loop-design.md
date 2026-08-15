# AdFlow 本地提示词闭环设计

**状态：** 用户已确认范围，待书面规格复核后实施  
**范围：** 保留现有前五步，只完成提示词生成、修改、版本、恢复和人工保存；暂不接入 Seedance、视频生成任务、结果播放下载或生成专用迁移字段。

## 1. 目标

用户完成素材、分析、时间线和逐镜事实确认后，可以在现有第五步完成完整提示词工作：

1. 由 GPT 异步生成提示词。
2. 刷新页面后继续看到任务状态，并在完成后恢复结果。
3. 查看当前时间线下所有已完成提示词版本。
4. 选择任一已完成版本继续编辑、人工保存或交给 GPT 修改。
5. 每次人工保存或 GPT 修改都创建新版本，不覆盖旧版本。
6. 严格遵守 `preserve_product` / `replace_product` 服务端规则。

## 2. 明确不做

- 不调用 Seedance。
- 不增加视频生成第六步。
- 不增加 Generation 恢复字段、供应商提交逻辑、视频下载或播放。
- 不在本期统一 Alembic 或增加生成专用迁移。
- 不重新设计整页，不改变前四步工作流。
- 不增加前端状态管理库、轮询库或组件测试框架。

## 3. 现状问题

### 3.1 历史版本不可选择

后端只有创建和修改接口，项目详情只返回一个“最新提示词”。页面刷新后无法可靠区分已完成版本和排队中的空版本，也无法选择旧版本。

### 3.2 刷新后任务状态丢失

`promptTask` 只在当前浏览器操作时设置。页面刷新后虽然通用 Job 轮询仍存在，但界面不会从活动的 `final_prompt_generation` 或 `prompt_refinement` Job 恢复“生成中/修改中”状态。

### 3.3 修改任务输入可能漂移

当前修改 Worker 在执行时查找“当时最新”的已完成提示词。若任务排队后又保存了新版本，Worker 可能修改错误的源版本。修改任务必须在入队时固定源提示词。

### 3.4 页面仍有失效产品控件

第五步在 `preserve_product` 下仍显示“目标产品图”和“替换产品”开关，但服务端现在正确禁止保留产品模式开启替换。该控件与业务规则冲突，必须删除。

## 4. 方案选择

采用现有结构内的最小闭环：

- 后端新增只读提示词历史接口。
- 修改接口接受可选 `source_version`，入队时把所选已完成版本的文本复制到新 queued revision，作为不可变输入快照。
- Worker 修改 queued revision 自身保存的快照，成功后再覆盖为输出。
- 前端复用当前 Job 轮询，不增加第二套定时器。
- 第五步增加紧凑版本选择区和任务状态，不增加新页面。

不新增数据库字段。queued refinement revision 的 `text` 暂存源文本；历史接口只返回 `completed`，因此排队快照不会作为完成版本展示。项目详情的“最新提示词”也必须改为最新 `completed` 且非空版本，避免刷新时用 queued 快照或空文本覆盖编辑框。

## 5. 后端设计

### 5.1 提示词历史接口

新增：

```http
GET /api/projects/{project_id}/prompts?current_timeline_only=true&status=completed
```

响应按 `version DESC` 排序，每项包含：

- `id`
- `version`
- `text`
- `status`
- `source_timeline_revision_id`
- `replace_product`
- `replace_person`
- `created_at`

约束：

- 项目不存在返回 404。
- `status` 只接受 `completed`；其他值返回 422。
- `current_timeline_only=true` 时只返回最新时间线关联的版本；没有时间线返回空数组。
- 前端提示词页面固定请求当前时间线的 completed 版本。

### 5.2 项目详情恢复

项目详情中的 `latest_prompt_version`、`latest_prompt_text` 和提示词选项只从最新 `completed`、非空 revision 读取。queued、retryable、failed 版本不能覆盖最后一个可用结果。

### 5.3 固定修改源版本

修改请求扩展为：

```json
{
  "instruction": "保持时间轴，增强高级感",
  "source_version": 3
}
```

- `source_version` 可选；缺省时使用最新 completed、非空版本，保持旧调用兼容。
- 指定版本必须属于当前项目、状态为 completed 且文本非空，否则返回 422。
- 新建 queued refinement revision 时，`text` 先复制源版本正文，`visual_direction` 保存修改指令。
- Worker 直接以 queued revision 当前的 `text` 作为不可变源快照。
- 模型输出先保存在局部变量，通过时间轴完整性和产品规则校验后才覆盖 `revision.text` 并标记 completed。
- 失败或重试时保留源快照，但历史接口和项目详情不会将它当作完成结果展示。

## 6. 前端设计

### 6.1 保持现有第五步

不增加第六步，不重排工作流导航。第五步继续包含：

- 人物参考档案与人物替换选择。
- 改编要求。
- 完整提示词编辑框。
- GPT 修改要求。
- 人工保存与 GPT 生成按钮。

删除：

- `preserve_product` 下的目标产品上传卡片。
- 前端“替换产品”复选框。
- 对 `target_product_reference_image` 的第五步上传、重试和保存回调。

`replace_product` 页面只显示“目标产品已由第一步锁定”的只读提示，不允许第五步改变模式。

### 6.2 版本选择区

在“最终提示词”编辑框标题附近增加紧凑版本选择：

- 显示 `vN` 和创建时间。
- 默认选择当前时间线最新 completed 版本。
- 选择旧版本后，把该版本正文载入编辑框并更新当前版本号。
- 编辑不会自动覆盖历史；点击“保存人工版本”才创建新版本。
- GPT 修改请求携带当前选择的 `source_version`。

### 6.3 状态恢复

复用现有 `listAnalysisJobs` 轮询：

- 若存在活动 `final_prompt_generation`，恢复 `promptTask = generate`。
- 若存在活动 `prompt_refinement`，恢复 `promptTask = refine`。
- 活动状态包括 queued、processing、retryable。
- 任务完成后刷新项目详情和提示词历史，选择最新 completed 版本。
- 任务失败后保留当前编辑框与已完成版本，显示 Job 错误；按钮恢复可用，用户可重新提交。

页面不会因为 queued/failed revision 而清空已完成提示词。

## 7. 状态与数据流

```text
人工确认镜头
  -> POST /prompts (use_ai=true)
  -> queued PromptRevision + final_prompt_generation Job
  -> 现有 Worker
  -> completed PromptRevision
  -> 通用 Job 轮询发现完成
  -> 刷新 completed 历史
  -> 编辑框显示最新版本

选择 completed vN
  -> POST /prompts/refine (source_version=N)
  -> queued revision.text 保存 vN 快照
  -> prompt_refinement Worker 修改固定快照
  -> 校验通过后覆盖为新正文并 completed
  -> 历史增加新版本，旧版本不变
```

## 8. 错误处理

- 时间线或镜头未确认：沿用现有第五步阻塞提示。
- 所选源版本不存在、未完成或为空：422，前端保留当前内容。
- GPT 配置缺失或调用失败：Job 进入现有 retryable/failed 流程；旧 completed 版本继续可用。
- GPT 返回缺少镜头或违反产品规则：不覆盖源快照，不产生 completed 版本。
- 页面刷新：从 completed 历史恢复正文，从 Job 列表恢复忙碌状态。

## 9. 测试策略

### 后端

- 历史接口排序、current timeline 过滤、completed 过滤、404/422。
- 项目详情忽略 queued/failed/空文本版本。
- 指定 `source_version` 的修改任务固定正确源文本。
- 排队后创建新人工版本，Worker 仍修改原快照。
- Worker 成功、重试和规则拒绝时的文本与状态。

### 前端

不增加测试依赖。提取小型纯函数并用现有 Vitest 验证：

- completed 版本排序与默认选择。
- 选择旧版本载入正文。
- 从 Job 列表恢复 generate/refine/idle 状态。
- 完成后选择最新版本，失败时保留已完成正文。
- API payload 携带 `source_version`。

最后运行：

- 后端提示词相关模块和完整 pytest。
- 前端 24 个既有测试、新增纯函数测试、lint、build。
- 手工刷新恢复检查，不调用 Seedance。

## 10. 完成标准

- 当前页面第五步可生成、恢复、查看版本、选择版本、人工保存和继续修改。
- 刷新不会丢失活动任务，也不会用 queued/failed 数据清空已完成正文。
- 修改任务始终使用提交时选择的源版本。
- 页面不再显示与严格模式冲突的产品替换控件。
- 所有提示词规则测试无 XFAIL。
- 不产生任何 Seedance 请求、Generation 记录或视频文件。
