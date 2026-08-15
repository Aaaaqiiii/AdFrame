# AdFlow Prompt-Only Closed Loop Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在现有第五步完成提示词异步生成、刷新恢复、版本选择、人工保存和固定源版本修改，不接入任何视频生成能力。

**Architecture:** 后端新增 completed 提示词历史读取，并把 refinement 源文本在入队时快照到 queued revision 自身；项目详情、历史和前端只把 completed 非空 revision 当作可用结果。前端复用现有 Job 列表和轮询，在现有 `PromptStage` 内增加版本选择与状态恢复，同时删除与严格产品模式冲突的旧控件。

**Tech Stack:** FastAPI、SQLAlchemy 2、Pydantic、pytest、React 19、TypeScript、Vitest、现有 CSS。

## Global Constraints

- 只保留现有前五步，不增加视频生成第六步。
- 不调用 Seedance，不创建 Generation，不下载或播放视频。
- 不新增数据库字段或 Alembic migration。
- `replace_product` 完全由 `Project.mode` 决定；前端不能改变它。
- `preserve_product` 的第五步不显示目标产品上传或替换产品开关。
- 历史和项目恢复只读取 `completed` 且非空的 PromptRevision。
- queued refinement revision 的 `text` 是提交时选定源版本的不可变快照，不是完成结果。
- 模型输出通过时间轴完整性和产品规则检查后才能覆盖快照并标记 completed。
- 复用现有 Job API、轮询和 Worker，不增加状态管理或轮询依赖。
- 保持现有页面视觉语言与第五步位置，只做局部布局优化。

---

## File Map

- Modify: `backend/app/api/routes/projects.py` — completed 历史、项目恢复和指定源版本入队。
- Modify: `backend/app/services/final_prompt.py` — refinement Worker 使用 queued revision 快照。
- Modify: `backend/tests/test_projects_api.py` — 历史、项目恢复、指定版本验证。
- Modify: `backend/tests/test_simplified_prompt_workflow.py` — 排队后源版本不漂移和 Worker 完成。
- Create: `frontend/src/promptWorkflow.ts` — 版本选择与 Job 恢复纯函数。
- Create: `frontend/src/promptWorkflow.test.ts` — 纯状态回归。
- Modify: `frontend/src/api.ts` — 提示词历史类型、列表和 source_version 请求。
- Modify: `frontend/src/App.tsx` — 版本状态、刷新恢复和现有轮询接线。
- Modify: `frontend/src/components/PromptStage.tsx` — 当前第五步版本选择、状态和旧产品控件删除。
- Modify: `frontend/src/App.css` — 当前设计语言内的提示词版本布局。

### Task 1: Expose completed prompt history and safe project recovery

**Files:**
- Modify: `backend/app/api/routes/projects.py`
- Modify: `backend/tests/test_projects_api.py`

**Interfaces:**
- Consumes: `PromptRevision`, current latest `TimelineRevision`, `GET /api/projects/{id}`.
- Produces: `GET /api/projects/{id}/prompts?current_timeline_only=true&status=completed -> list[PromptRevisionSummary]` and completed-only project prompt fields.

- [ ] **Step 1: Write failing history, filter and project-recovery tests**

Add tests with literal expected versions and IDs:

```python
def test_prompt_history_filters_to_completed_current_timeline() -> None:
    client = TestClient(create_app())
    project = client.post("/api/projects", json={"name": "prompt history"}).json()
    timeline_v1 = client.put(
        f"/api/projects/{project['id']}/timeline",
        json={"shots": [{"start_sec": 0, "end_sec": 2}]},
    ).json()
    with SessionLocal() as session:
        session.add(PromptRevision(
            project_id=UUID(project["id"]), version=1, text="v1 completed",
            visual_direction="v1", source_timeline_revision_id=UUID(timeline_v1["revision_id"]),
            status="completed",
        ))
        session.commit()
    timeline_v2 = client.put(
        f"/api/projects/{project['id']}/timeline",
        json={"shots": [{"start_sec": 0, "end_sec": 3}]},
    ).json()
    with SessionLocal() as session:
        session.add_all([
            PromptRevision(
                project_id=UUID(project["id"]), version=2, text="source snapshot",
                visual_direction="queued", source_timeline_revision_id=UUID(timeline_v2["revision_id"]),
                status="queued",
            ),
            PromptRevision(
                project_id=UUID(project["id"]), version=3, text="v3 completed",
                visual_direction="v3", source_timeline_revision_id=UUID(timeline_v2["revision_id"]),
                status="completed",
            ),
        ])
        session.commit()

    response = client.get(
        f"/api/projects/{project['id']}/prompts?current_timeline_only=true&status=completed"
    )
    assert response.status_code == 200
    assert [item["version"] for item in response.json()] == [3]
    assert response.json()[0]["source_timeline_revision_id"] == timeline_v2["revision_id"]


def test_project_details_ignore_newer_unfinished_prompt_revision() -> None:
    # Save completed v1, then insert queued v2 with a source snapshot.
    details = client.get(f"/api/projects/{project_id}").json()
    assert details["latest_prompt_version"] == 1
    assert details["latest_prompt_text"] == "stable completed text"


def test_prompt_history_rejects_unknown_status_filter() -> None:
    response = client.get(f"/api/projects/{project_id}/prompts?status=queued")
    assert response.status_code == 422
    assert "completed" in str(response.json()["detail"])
```

Also assert a missing project returns 404 and `current_timeline_only=true` returns `[]` when the project has no timeline.

- [ ] **Step 2: Run the focused tests and preserve RED**

```powershell
Set-Location E:\工具-商用\.worktrees\adflow-local-closed-loop\backend
python -m pytest tests/test_projects_api.py -k "prompt_history or unfinished_prompt" -q
```

Expected: history tests fail with 405 and project details select queued v2 instead of completed v1.

- [ ] **Step 3: Add the response model and list route**

Import `datetime` and `Query`, then add:

```python
class PromptRevisionSummary(BaseModel):
    id: UUID
    version: int
    text: str
    status: str
    source_timeline_revision_id: UUID | None
    replace_product: bool
    replace_person: bool
    created_at: datetime


@router.get("/{project_id}/prompts", response_model=list[PromptRevisionSummary])
def list_prompt_revisions(
    project_id: UUID,
    current_timeline_only: bool = False,
    status_filter: str | None = Query(default=None, alias="status"),
    session: Session = Depends(get_session),
) -> list[PromptRevision]:
    if session.get(Project, project_id) is None:
        raise HTTPException(status_code=404, detail="项目不存在")
    if status_filter not in {None, "completed"}:
        raise HTTPException(status_code=422, detail="提示词状态筛选只支持 completed")
    query = select(PromptRevision).where(PromptRevision.project_id == project_id)
    if status_filter == "completed":
        query = query.where(PromptRevision.status == "completed", PromptRevision.text != "")
    if current_timeline_only:
        current = session.scalar(select(TimelineRevision).where(
            TimelineRevision.project_id == project_id
        ).order_by(TimelineRevision.version.desc()))
        if current is None:
            return []
        query = query.where(PromptRevision.source_timeline_revision_id == current.id)
    return list(session.scalars(query.order_by(PromptRevision.version.desc())))
```

- [ ] **Step 4: Make project details select the latest usable revision**

Replace the unfiltered latest prompt query with:

```python
latest_prompt = session.scalar(select(PromptRevision).where(
    PromptRevision.project_id == project_id,
    PromptRevision.status == "completed",
    PromptRevision.text != "",
).order_by(PromptRevision.version.desc()))
```

All existing response fields continue to read from this one `latest_prompt`.

- [ ] **Step 5: Run focused and complete project API tests**

```powershell
python -m pytest tests/test_projects_api.py -k "prompt_history or unfinished_prompt" -q
python -m pytest tests/test_projects_api.py -q
```

Expected: all pass with no XFAIL.

- [ ] **Step 6: Commit**

```powershell
git add backend/app/api/routes/projects.py backend/tests/test_projects_api.py
git commit -m "feat: expose completed prompt history"
```

### Task 2: Freeze the selected refinement source at queue time

**Files:**
- Modify: `backend/app/api/routes/projects.py`
- Modify: `backend/app/services/final_prompt.py`
- Modify: `backend/tests/test_projects_api.py`
- Modify: `backend/tests/test_simplified_prompt_workflow.py`

**Interfaces:**
- Consumes: `POST /api/projects/{id}/prompts/refine` with `{instruction, source_version?}`.
- Produces: queued refinement revision whose `text` is the immutable source snapshot; Worker overwrites it only after successful validation.

- [ ] **Step 1: Write source-version validation and snapshot RED tests**

```python
def test_refinement_queues_the_selected_completed_version_snapshot() -> None:
    # Persist completed v1="first source" and completed v2="second source".
    response = client.post(
        f"/api/projects/{project_id}/prompts/refine",
        json={"instruction": "只修改第一版", "source_version": 1},
    )
    assert response.status_code == 202
    assert response.json()["status"] == "queued"
    assert response.json()["text"] == ""
    with SessionLocal() as session:
        queued = session.scalar(select(PromptRevision).where(
            PromptRevision.project_id == UUID(project_id),
            PromptRevision.version == response.json()["version"],
        ))
        assert queued.text == "first source"


def test_refinement_rejects_unknown_or_unfinished_source_version() -> None:
    missing = client.post(
        f"/api/projects/{project_id}/prompts/refine",
        json={"instruction": "修改", "source_version": 999},
    )
    assert missing.status_code == 422
```

Add a Worker regression that queues from v1, saves completed v2 afterward, executes the queued job, and proves the Worker still calls refinement with v1.

- [ ] **Step 2: Run focused tests and preserve RED**

```powershell
python -m pytest tests/test_projects_api.py tests/test_simplified_prompt_workflow.py -k "selected_completed_version or source_version or source_snapshot" -q
```

Expected: request rejects `source_version` as an unknown field behaviorally by still choosing latest v2, or queued revision text remains empty; Worker uses the later latest completed version.

- [ ] **Step 3: Extend the request and queue a source snapshot**

```python
class RefinePromptRequest(BaseModel):
    instruction: str = Field(min_length=1, max_length=8000)
    source_version: int | None = Field(default=None, ge=1)
```

Build the source query from the project, completed status and non-empty text. When `source_version` is supplied, add `PromptRevision.version == payload.source_version`; otherwise order by version descending. If no valid source exists, return 422.

Create the target revision with the selected source text:

```python
revision = PromptRevision(
    project_id=project_id,
    version=version,
    text=source.text,
    visual_direction=payload.instruction.strip(),
    audio_mode=source.audio_mode,
    audio_style=source.audio_style,
    replace_product=project.mode == "replace_product",
    replace_person=source.replace_person,
    source_timeline_revision_id=source.source_timeline_revision_id,
    status="queued",
)
```

Continue returning `PromptResponse(version=version, text="", status="queued")` so queued input snapshots are not presented as generated output.

- [ ] **Step 4: Make the Worker consume only its own snapshot**

Replace the “latest prior completed revision” query with:

```python
source_text = revision.text.strip()
if not source_text:
    raise ValueError("没有可供修改的完整提示词快照")
refined_text = refine_prompt(source_text, revision.visual_direction, settings)
```

Keep the existing project-mode override, product-rule validation, local output variable, assign-after-validation order and commit behavior unchanged.

- [ ] **Step 5: Prove the snapshot does not drift**

In the Worker test:

```python
source_text = "00:00.00–00:03.00\nfirst source"
refined_text = "00:00.00–00:03.00\nrevised v1"
with patch("app.services.final_prompt.refine_prompt", return_value=refined_text) as refine:
    execute_prompt_refinement_job(session, job, Settings(comfly_api_key="test-key"))
assert refine.call_args.args[0] == source_text
session.refresh(queued)
assert queued.text == refined_text
assert queued.status == "completed"
```

The expected output must retain any time labels used in the source fixture so the real completeness contract is not weakened.

- [ ] **Step 6: Run prompt modules and commit**

```powershell
python -m pytest tests/test_projects_api.py tests/test_simplified_prompt_workflow.py tests/test_prompting.py tests/test_dual_product_workflows.py -q
git add backend/app/api/routes/projects.py backend/app/services/final_prompt.py backend/tests/test_projects_api.py backend/tests/test_simplified_prompt_workflow.py
git commit -m "fix: freeze prompt refinement source"
```

Expected: all pass, no XFAIL.

### Task 3: Add typed prompt API and pure recovery state

**Files:**
- Create: `frontend/src/promptWorkflow.ts`
- Create: `frontend/src/promptWorkflow.test.ts`
- Modify: `frontend/src/api.ts`

**Interfaces:**
- Consumes: `PromptRevisionSummary[]`, newest-first `AnalysisJob[]`.
- Produces: `choosePromptRevision()`, `promptTaskFromJobs()`, `latestPromptJob()`, typed list/refine API calls.

- [ ] **Step 1: Write pure state RED tests**

```typescript
import { describe, expect, it } from 'vitest'
import type { PromptRevisionSummary } from './api'
import { choosePromptRevision, latestPromptJob, promptTaskFromJobs } from './promptWorkflow'

const versions: PromptRevisionSummary[] = [
  { id: 'p3', version: 3, text: 'third', status: 'completed', source_timeline_revision_id: 't2', replace_product: false, replace_person: false, created_at: '2026-08-15T03:00:00Z' },
  { id: 'p1', version: 1, text: 'first', status: 'completed', source_timeline_revision_id: 't2', replace_product: false, replace_person: false, created_at: '2026-08-15T01:00:00Z' },
]

describe('prompt-only recovery', () => {
  it('keeps a selected completed version and otherwise chooses the latest', () => {
    expect(choosePromptRevision(versions, 1)?.text).toBe('first')
    expect(choosePromptRevision(versions, 99)?.version).toBe(3)
  })

  it('restores an active refinement or generation task', () => {
    expect(promptTaskFromJobs([{ job_id: 'r', kind: 'prompt_refinement', status: 'retryable' }])).toBe('refine')
    expect(promptTaskFromJobs([{ job_id: 'g', kind: 'final_prompt_generation', status: 'queued' }])).toBe('generate')
    expect(promptTaskFromJobs([{ job_id: 'f', kind: 'prompt_refinement', status: 'failed' }])).toBeNull()
  })

  it('returns the newest prompt job because the API is newest-first', () => {
    expect(latestPromptJob([
      { job_id: 'new', kind: 'prompt_refinement', status: 'failed', error_message: 'bad' },
      { job_id: 'old', kind: 'final_prompt_generation', status: 'completed' },
    ])?.job_id).toBe('new')
  })
})
```

- [ ] **Step 2: Run RED**

```powershell
Set-Location E:\工具-商用\.worktrees\adflow-local-closed-loop\frontend
npm.cmd test -- promptWorkflow.test.ts
```

Expected: FAIL because `promptWorkflow.ts` does not exist.

- [ ] **Step 3: Add API types and calls**

In `api.ts`:

```typescript
export type PromptRevisionSummary = {
  id: string
  version: number
  text: string
  status: AnalysisState
  source_timeline_revision_id: string | null
  replace_product: boolean
  replace_person: boolean
  created_at: string
}

export function listPromptRevisions(projectId: string) {
  return request<PromptRevisionSummary[]>(`/api/projects/${projectId}/prompts?current_timeline_only=true&status=completed`)
}

export function refinePrompt(projectId: string, instruction: string, sourceVersion?: number) {
  return request<{ version: number; text: string; status: AnalysisState }>(
    `/api/projects/${projectId}/prompts/refine`,
    { method: 'POST', ...json({ instruction, source_version: sourceVersion }) },
  )
}
```

JSON serialization may include `source_version: undefined`; `JSON.stringify` omits it, preserving backward compatibility.

- [ ] **Step 4: Implement the minimum pure domain**

```typescript
import type { AnalysisJob, PromptRevisionSummary } from './api'

export type PromptTask = 'generate' | 'refine' | null

const active = (status: string) => ['queued', 'uploaded', 'running', 'processing', 'retryable'].includes(status)
const promptKind = (job: AnalysisJob) => job.kind === 'final_prompt_generation' || job.kind === 'prompt_refinement'

export function choosePromptRevision(revisions: PromptRevisionSummary[], preferredVersion?: number) {
  return revisions.find((item) => item.version === preferredVersion) ?? revisions[0]
}

export function promptTaskFromJobs(jobs: AnalysisJob[]): PromptTask {
  const job = jobs.find((item) => promptKind(item) && active(item.status))
  return job?.kind === 'prompt_refinement' ? 'refine' : job?.kind === 'final_prompt_generation' ? 'generate' : null
}

export function latestPromptJob(jobs: AnalysisJob[]) {
  return jobs.find(promptKind)
}
```

- [ ] **Step 5: Run tests, lint and commit**

```powershell
npm.cmd test -- promptWorkflow.test.ts
npm.cmd run lint
git add frontend/src/api.ts frontend/src/promptWorkflow.ts frontend/src/promptWorkflow.test.ts
git commit -m "feat: model prompt version recovery"
```

### Task 4: Connect the existing fifth step without redesigning the page

**Files:**
- Modify: `frontend/src/App.tsx`
- Modify: `frontend/src/components/PromptStage.tsx`
- Modify: `frontend/src/App.css`
- Test: `frontend/src/promptWorkflow.test.ts`

**Interfaces:**
- Consumes: Task 3 API/domain, existing `listAnalysisJobs` polling and `MaterialState` for person only.
- Produces: current fifth-step version selection, recovered prompt task state and strict-mode-aligned controls.

- [ ] **Step 1: Add selection/recovery integration cases to the pure tests**

Add cases proving:

```typescript
expect(choosePromptRevision([], 3)).toBeUndefined()
expect(promptTaskFromJobs([
  { job_id: 'done', kind: 'prompt_refinement', status: 'completed' },
  { job_id: 'active', kind: 'final_prompt_generation', status: 'processing' },
])).toBe('generate')
```

- [ ] **Step 2: Simplify `PromptStage` props and remove invalid product controls**

Remove these props and all associated JSX:

```text
productReady
replaceProduct
onReplaceProduct
targetProduct
onTargetProduct
onTargetProducts
onTargetProductRetry
onTargetProductProfile
onTargetProductProfileSave
```

Keep the replace-mode read-only “目标产品已锁定” banner. Keep the person card and person replacement checkbox.

Add:

```typescript
versions: PromptRevisionSummary[]
selectedVersion: number
taskStatus?: AnalysisJob
onSelectVersion: (version: number) => void
```

Render a native `<select>` near the prompt editor header. Each option label is `v${version} · ${new Date(created_at).toLocaleString()}`. When there are no completed versions, show `尚无已完成版本`.

Render task copy from the newest prompt Job:

- active: `GPT 提示词任务处理中`
- failed: `上次 GPT 任务失败：{error_message}`
- completed: no persistent warning banner

Do not use the failed/queued revision text as editor content.

- [ ] **Step 3: Add version state and completed-history loading in `App.tsx`**

Add:

```typescript
const [promptVersions, setPromptVersions] = useState<PromptRevisionSummary[]>([])
```

Create one callback:

```typescript
const restorePromptVersions = useCallback(async (
  id: string,
  preferredVersion?: number,
  preferLatest = false,
) => {
  const versions = await listPromptRevisions(id)
  setPromptVersions(versions)
  const selected = choosePromptRevision(versions, preferLatest ? undefined : preferredVersion)
  setPromptVersion(selected?.version ?? 0)
  setPromptText(selected?.text ?? '')
  return selected
}, [])
```

On initial project restore, request project details and completed history, then select latest completed. Do not assign `latest_prompt_text` from an unfinished revision; Task 1 already makes the project fallback safe.

When the user chooses a version, find it in `promptVersions`, then set `promptVersion` and `promptText`. Editing remains local until save.

- [ ] **Step 4: Recover prompt jobs through the existing polling loop**

After every `listAnalysisJobs` result:

```typescript
const recoveredTask = promptTaskFromJobs(next)
if (recoveredTask) setPromptTask(recoveredTask)
```

Inside the timer, use `const effectivePromptTask = promptTask ?? promptTaskFromJobs(current)` and locate the newest matching job from the newest-first list. When it becomes terminal:

- success: call `restoreProject(projectId)` and `restorePromptVersions(projectId, undefined, true)`, clear refinement text for refinement jobs, clear `promptTask`.
- failed: keep editor and versions unchanged, clear `promptTask`, display `error_message`.
- retryable/active: keep buttons disabled and display attempts using existing notice behavior.

Pass `latestPromptJob(jobs)` to `PromptStage` so a refresh after a failed task still shows the last failure without clearing content.

- [ ] **Step 5: Send the selected version when refining and refresh history after saves**

Change:

```typescript
const saved = await refinePrompt(projectId, promptRefinement.trim(), promptVersion || undefined)
```

After manual save, reload versions and prefer the returned version. After AI generation/refinement completion, reload and prefer latest. Do not reload completed history immediately after queueing because the queued revision must not appear.

Send `replace_product: MODE === 'replace_product'`; delete the `replaceProduct` state and setter. Keep server-side validation authoritative.

- [ ] **Step 6: Add local layout styles**

Add only selectors scoped under `.prompt-stage`, for example:

```css
.prompt-version-row { display: flex; align-items: center; gap: 12px; flex-wrap: wrap; }
.prompt-version-select { min-width: 210px; }
.prompt-task-state { margin-left: auto; color: var(--muted); }
.prompt-task-state.failed { color: var(--danger); }
```

Reuse existing colors, radii, buttons, banners and typography. Do not change `WorkflowRail`, global header, other stages or overall shell dimensions.

- [ ] **Step 7: Run frontend gates and commit**

```powershell
npm.cmd test
npm.cmd run lint
npm.cmd run build
git add frontend/src/App.tsx frontend/src/components/PromptStage.tsx frontend/src/App.css frontend/src/promptWorkflow.test.ts
git commit -m "feat: complete prompt-only workspace"
```

Expected: all tests pass, lint has no diagnostics, TypeScript/Vite build succeeds.

### Task 5: Verify the prompt-only local release gate

**Files:**
- Test: all prompt-related backend and frontend files
- Read: `docs/superpowers/specs/2026-08-15-adflow-prompt-only-closed-loop-design.md`

**Interfaces:**
- Consumes: Tasks 1–4.
- Produces: exact automated and local smoke evidence with zero Seedance requests.

- [ ] **Step 1: Run focused backend prompt modules**

```powershell
Set-Location E:\工具-商用\.worktrees\adflow-local-closed-loop\backend
python -m pytest tests/test_projects_api.py tests/test_prompting.py tests/test_simplified_prompt_workflow.py tests/test_dual_product_workflows.py tests/test_worker.py -q
```

Expected: exit 0, no XFAIL, no skipped prompt test.

- [ ] **Step 2: Run the full backend gate with real media tools available**

```powershell
python -m pytest -q
```

Expected: exit 0, failed 0, errors 0, xfailed 0, skipped 0. If the sandbox cannot execute FFmpeg, rerun with approved host permissions; do not remove media markers.

- [ ] **Step 3: Run all frontend gates**

```powershell
Set-Location E:\工具-商用\.worktrees\adflow-local-closed-loop\frontend
npm.cmd test
npm.cmd run lint
npm.cmd run build
```

Expected: all exit 0.

- [ ] **Step 4: Run a local prompt-only smoke test**

Using the existing local backend/frontend and configured Comfly key:

1. Open an existing project whose timeline and shots are confirmed.
2. Generate a prompt and observe queued/processing without leaving the fifth step.
3. Refresh while active and confirm busy state returns from Job data.
4. After completion, confirm the new completed version appears and its text remains after refresh.
5. Select an older version with visibly distinct wording, request a refinement, and confirm the result is based on that selected version; the queue-time concurrent-save race is covered by Task 2's backend regression because the UI intentionally disables saves while busy.
6. Confirm `preserve_product` has no target product upload or replace-product checkbox; `replace_product` only shows a locked read-only banner.
7. Confirm no request path contains `/generations` or Seedance task endpoints and no new Generation row is created.

If no Comfly key is configured, record the exact blocker and rely only on automated external-boundary tests; do not fabricate a real-call pass.

- [ ] **Step 5: Record exact counts and worktree state**

Record backend passed/failed/errors/skipped/xfailed, frontend Vitest count, lint/build exit codes, local smoke result and `git status --short`. This task creates no commit when verification changes no tracked files.
