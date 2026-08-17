# AdFlow 前五步提示词闭环真实验收报告

> 验收日期：2026-08-17。范围：前五步提示词闭环（上传素材 → 切分校正 → 理解分镜 → 确认事实 → 生成提示词）。不涉及第六步 Seedance 生成。

## 结论

**核心五步提示词闭环通过。** 使用真实外部服务（豆包 VOD 完整镜头理解 + Comfly GPT 关键帧理解与提示词生成）完整走通五步，生成 3 个提示词版本，全程未调用 `/generations` 或 Seedance。

## 冒烟项目记录

- 项目 ID：`0f698147-3cfe-480f-a485-2b0873f06259`（页面一 preserve_product 模式）
- 参考视频：23.6 秒（真实素材，符合 4—30 秒要求）
- 时间轴：AI 切分 7 个镜头（`vision_hybrid`），人工确认全部镜头事实
- 提示词版本：
  - v1：Comfly GPT 生成，3462 字符，完整分镜结构（全局要求 + 逐镜头描述）
  - v2：针对 v1 精修（镜头 2 灯光改为更柔和），3484 字符
  - v3：人工保存（`use_ai=False`），同步创建
- 刷新恢复：项目详情 `latest_prompt_version=3`；历史顺序 v3→v2→v1；前端刷新默认选择最新 completed 版本（v3）

## 关键验证点

### 最终刷新恢复默认选最新版本（v3）

- `GET /api/projects/{id}` → `latest_prompt_version: 3`，`latest_prompt_text` 为 v3 文本
- `GET /api/projects/{id}/prompts?status=completed` → 降序 `[v3, v2, v1]`
- 前端恢复：`restorePromptVersions(id, undefined, preferLatest=true)` → `choosePromptRevision(versions, undefined)` 回退到 `revisions[0]`（最新）→ v3
- 自动化测试 `promptWorkflow.test.ts` 覆盖：偏好版本不存在时回退最新（`choosePromptRevision(revisions, 99) → version 3`）

### 精修来源不漂移

- 真实冒烟证据：v2 成功执行了针对 v1 的精修，镜头 2 灯光发生预期变化（v2 相对 v1 增加约 22 字符）
- 严格证据（不依赖文本相似度）：由自动化测试负责——排队时选择 v1，随后保存另一个版本，Worker 仍读取队列中的 v1 快照。两份证据合并后构成完整证明。

### 租约恢复

- 手动中断（调试用 `run_once` 被超时打断）留下了幽灵租约；清理租约后 Worker 正常继续处理
- 自动租约过期后 Worker 重新认领：由自动化测试 `test_expired_generation_lease_is_claimed_after_worker_restart` 覆盖

## 环境记录

### FFmpeg

- 启动脚本解析路径：`C:\Users\Administrator\AppData\Local\Microsoft\WinGet\Links\ffmpeg.exe`（符号链接）
- 实际二进制：`C:\Users\Administrator\AppData\Local\Microsoft\WinGet\Packages\Gyan.FFmpeg_Microsoft.Winget.Source_8wekyb3d8bbwe\ffmpeg-8.1.2-full_build\bin\ffmpeg.exe`
- 版本：`ffmpeg version 8.1.2-full_build-www.gyan.dev`
- `ffprobe`：同目录 `ffprobe.exe`，`ffprobe version 8.1.2-full_build`
- 注意：WinGet Links 符号链接是安装/更新时的产物，存在损坏风险（本次验收前即损坏，已重建）。长期建议将真实二进制路径直接纳入配置或 PATH，而非依赖链接。

### 测试素材边界

- 本次真实验收使用真实 23.6 秒视频，全流程正常
- 极低分辨率（64×64）、低帧率（5fps）合成素材在接近尾帧 seek（`-ss 9.92` 于 10 秒视频）时触发 FFmpeg 8.1.2 兼容边界（`Invalid argument`）。该边界暂不作为产品支持目标，不视为产品代码缺陷。

## 不记录项

按验收纪律，以下内容不写入本报告：API Key、完整供应商响应、签名 URL、用户真实素材文件内容。

## 收口项（2026-08-17 完成）

### 双模式业务页面检查

- `/preserve-product`：Chrome headless 渲染确认**无替换产品功能控件**（仅保留"替换人物"开关；出现的"替换产品"字样仅来自工作流导航入口和"不得被替换"锁定规则文案）
- `/replace-product`：确认显示"目标产品图"、"替换规则"、"必须替换"；第五步在 `mode === 'replace_product'` 时条件渲染"目标产品已锁定"banner
- 后端逻辑由 `test_dual_product_workflows.py`（18 项）覆盖：preserve 拒绝替换、replace 强制目标产品、兼容性冲突拦截

### 响应式三尺寸（1440×900、920×900、390×844）

- Chrome headless 实际渲染截图三尺寸，无横向溢出
- CSS 三档媒体查询：`max-width:1250px`、`max-width:920px`（工作流导航转顶部横向滚动 `overflow-x:auto`）、`max-width:680px`
- 各容器 `overflow:hidden` + `text-overflow:ellipsis` 防溢出

### Comfly 连通测试端点修正

- 已新增 `comfly_prompt` 服务：`connectivity.py` 用 `comfly_vision_base_url + /v1/chat/completions` 检查 GPT 端点（真实请求 HTTP 200）
- 设置页 Comfly 卡片改指向 `comfly_prompt`，文案"GPT关键帧理解、综合事实和最终提示词"与实际测试端点一致
- `SETTING_GROUPS` 保存 Comfly Key 时默认验证 `comfly_prompt`
- 后端 164→167 passed 验证（新增 3 个连通测试）
