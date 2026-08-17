# AdFlow Local Acceptance Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 证明旧数据可升级、新数据库可安装、任务可跨重启恢复，并完成一次真实 Seedance 播放下载闭环。

**Architecture:** 自动化测试覆盖数据库状态和 Worker 恢复；PowerShell 验证脚本只接收用户明确提供的副本数据库 URL，不操作正式库。最后用当前本地 UI 和一段 4—30 秒视频完成真实供应商验收。

**Tech Stack:** pytest、Alembic、PostgreSQL、PowerShell、FastAPI、React/Vite、FFmpeg/FFprobe、Seedance。

## Global Constraints

- 先备份正式数据库并验证备份非空，再对副本迁移。
- 迁移验证脚本不得创建、删除或重命名数据库；只操作显式传入的两个数据库 URL。
- 不删除或移动 `data/media`。
- 不把 `.env`、API Key、数据库 URL、备份文件或生成 MP4 提交到 Git。
- 真实测试使用 4—30 秒参考视频；不使用大于 30 秒素材。
- `submission_uncertain` 必须人工查供应商控制台，不得点击普通重试。
- 最终质量门槛要求自动化测试失败数 0、环境跳过数 0。

---

## File Map

- Create: `backend/tests/test_generation_recovery.py` — API/Worker 重启语义和本地结果恢复。
- Create: `scripts/verify-adflow-migrations.ps1` — 对现有副本和空库运行确定性迁移检查。
- Create: `docs/runbooks/adflow-local-upgrade.md` — 本地备份、stamp、upgrade、回退说明。
- Modify: `README.md` — 六步启动和结果位置。
- Test: all backend/frontend suites plus real local app.

### Task 1: Automate restart and lease recovery semantics

**Files:**
- Create: `backend/tests/test_generation_recovery.py`
- Modify: `backend/tests/test_worker.py`

**Interfaces:**
- Consumes: queued/processing/retryable/uncertain generation rows and `run_once()`.
- Produces: deterministic proof that database state survives new sessions and stale leases recover.

- [x] **Step 1: Write failing restart tests**

```python
def test_processing_task_survives_new_api_session(processing_generation) -> None:
    generation_id = processing_generation.id
    project_id = processing_generation.project_id
    with SessionLocal() as restored:
        generation = restored.get(Generation, generation_id)
        assert generation.project_id == project_id
        assert generation.external_task_id == "provider-task-1"
        assert generation.status == "processing"


def test_expired_generation_lease_is_claimed_after_worker_restart(processing_generation) -> None:
    with SessionLocal() as session:
        generation = session.get(Generation, processing_generation.id)
        generation.leased_by = "dead-worker"
        generation.leased_at = datetime.now(UTC) - timedelta(minutes=11)
        generation.next_attempt_at = datetime.now(UTC) - timedelta(seconds=1)
        session.commit()
        claimed = _claim(
            session, Generation, None, "new-worker", datetime.now(UTC),
            statuses=("queued", "processing", "retryable"),
        )
        assert [item.id for item in claimed] == [processing_generation.id]
```

Add a test that `submission_uncertain` remains untouched across multiple `run_once()` calls, and a completed record returns the same `local_video_url` from a newly constructed `TestClient(create_app())`.

- [x] **Step 2: Run the recovery tests**

```powershell
Set-Location E:\工具-商用\backend
python -m pytest tests/test_generation_recovery.py tests/test_worker.py -q
```

Expected: tests either fail on an interface mismatch or pass without production code changes; fix only real recovery defects.

- [x] **Step 3: Apply the exact recovery correction if a new test fails**

If the stale-lease test fails, change only the lease predicate in `backend/app/worker.py` to accept `leased_at IS NULL OR leased_at <= now - 10 minutes`. If the uncertain-state test fails, remove `submission_uncertain` from the generation claim statuses. If the new-client local URL test fails, make `generation_response()` derive the URL from the persisted `result_path` and `Path.is_file()`. A failure outside these three invariants stops this task for diagnosis under `superpowers:systematic-debugging`; do not add another queue or cache.

- [x] **Step 4: Run full backend suite**

```powershell
python -m pytest -q
```

Expected: PASS and 0 skipped on the real workstation.

- [x] **Step 5: Commit restart recovery coverage**

```powershell
git add backend/tests/test_generation_recovery.py backend/tests/test_worker.py backend/app/worker.py backend/app/api/routes/generations.py backend/app/services/generation_jobs.py
git commit -m "test: prove generation recovery across restarts"
```

### Task 2: Verify migrations against explicit database copies

**Files:**
- Create: `scripts/verify-adflow-migrations.ps1`
- Test: a restored existing database copy and an empty database

**Interfaces:**
- Consumes: `-ExistingCloneUrl` and `-FreshDatabaseUrl`, both PostgreSQL URLs supplied by the operator.
- Produces: pre/post row counts and `alembic current` output at `0002_generation_closed_loop`.

- [x] **Step 1: Implement safe parameter and URL guards**

```powershell
param(
    [Parameter(Mandatory)][string]$ExistingCloneUrl,
    [Parameter(Mandatory)][string]$FreshDatabaseUrl
)
$ErrorActionPreference = 'Stop'
if ($ExistingCloneUrl -eq $FreshDatabaseUrl) { throw 'ExistingCloneUrl and FreshDatabaseUrl must be different databases.' }
foreach ($url in @($ExistingCloneUrl, $FreshDatabaseUrl)) {
    if ($url -notmatch '^postgresql(\+psycopg)?://') { throw "Only PostgreSQL URLs are accepted: $url" }
    if ($url -match '/adflow(?:\?|$)') { throw 'Do not pass the production adflow database; restore or create explicit verification databases first.' }
}
```

- [x] **Step 2: Capture existing-clone counts before migration**

Invoke Python from `backend` with `DATABASE_URL` set only for that child process. Query exact counts for:

```sql
SELECT COUNT(*) FROM projects;
SELECT COUNT(*) FROM assets;
SELECT COUNT(*) FROM timeline_revisions;
SELECT COUNT(*) FROM prompt_revisions;
SELECT COUNT(*) FROM generations;
```

Serialize counts to a temporary JSON file created under `[System.IO.Path]::GetTempPath()`.

- [x] **Step 3: Stamp and upgrade only the existing clone**

If the clone lacks `alembic_version`, run:

```powershell
$env:DATABASE_URL = $ExistingCloneUrl
python -m alembic -c alembic.ini stamp 0001_adflow_baseline
if ($LASTEXITCODE -ne 0) { throw 'Alembic stamp failed for existing clone.' }
python -m alembic -c alembic.ini upgrade head
if ($LASTEXITCODE -ne 0) { throw 'Alembic upgrade failed for existing clone.' }
```

If it already has a revision, do not stamp; only upgrade. Re-query the five counts and throw on any difference. Verify the five new generation columns and three indexes via SQLAlchemy `inspect()`.

- [x] **Step 4: Upgrade the empty database from base**

```powershell
$env:DATABASE_URL = $FreshDatabaseUrl
python -m alembic -c alembic.ini upgrade head
if ($LASTEXITCODE -ne 0) { throw 'Alembic upgrade failed for fresh database.' }
```

Verify all core tables exist, the Alembic revision is `0002_generation_closed_loop`, and every core table count is zero. Clear `Env:DATABASE_URL` in `finally` and remove temporary count files.

- [x] **Step 5: Parse and execute the verification script**

```powershell
$tokens = $null; $errors = $null
[System.Management.Automation.Language.Parser]::ParseFile('E:\工具-商用\scripts\verify-adflow-migrations.ps1', [ref]$tokens, [ref]$errors) | Out-Null
if ($errors.Count) { throw ($errors | Out-String) }
```

Then run it only with two prepared non-production databases:

```powershell
E:\工具-商用\scripts\verify-adflow-migrations.ps1 -ExistingCloneUrl $existingCloneUrl -FreshDatabaseUrl $freshDatabaseUrl
```

Expected: both databases report head revision and the clone counts remain identical.

- [x] **Step 6: Commit migration verification**

```powershell
git add scripts/verify-adflow-migrations.ps1
git commit -m "test: verify local postgres migrations safely"
```

### Task 3: Document the exact local upgrade and recovery procedure

**Files:**
- Create: `docs/runbooks/adflow-local-upgrade.md`
- Modify: `README.md`

**Interfaces:**
- Consumes: backup/start/verification scripts.
- Produces: one operator path from existing install to six-step local app.

- [x] **Step 1: Write the runbook with executable commands**

The runbook must contain these ordered sections and commands:

```powershell
Set-Location E:\工具-商用
.\scripts\backup-adflow-database.ps1 -OutputDirectory 'D:\AdFlow-Backups'
.\scripts\start-adflow-local.ps1
```

Sections: prerequisites; verify FFmpeg/FFprobe/PostgreSQL; back up; verify non-empty dump; migration behavior; start; check `/api/health`; open `http://localhost:5174`; inspect Worker process; recover `submission_uncertain`; locate `data/media/{project_id}/generated/v{version}.mp4`; restore database backup if migration fails. State explicitly that media files are backed up separately and are never deleted by migration.

- [x] **Step 2: Update README to describe six steps**

Replace any five-step or direct-generation text with: 上传素材、切分与校正、理解分镜、确认分镜事实、生成提示词、生成与结果. Link the runbook and design spec. Do not duplicate the full runbook.

- [x] **Step 3: Scan documentation for stale direct-submit instructions**

```powershell
Get-ChildItem E:\工具-商用\README.md,E:\工具-商用\docs -Recurse -File -Include *.md |
    Select-String -Pattern '手动发布参考视频|第五步.*生成视频|ratio.*16:9|duration.*5'
```

Expected: no active instruction contradicts the new flow; historical design documents may be labeled superseded instead of rewritten.

- [x] **Step 4: Commit operator documentation**

```powershell
git add README.md docs/runbooks/adflow-local-upgrade.md
git commit -m "docs: explain local generation upgrade and recovery"
```

### Task 4: Run the complete automated release gate

**Files:**
- Test: backend and frontend

**Interfaces:**
- Consumes: all prior plans.
- Produces: exact passing counts attached to the implementation report.

- [x] **Step 1: Run backend tests in the executable FFmpeg environment**

```powershell
Set-Location E:\工具-商用\backend
python -m pytest -q
```

Expected: failures 0, errors 0, skipped 0.

- [x] **Step 2: Run all frontend gates**

```powershell
Set-Location E:\工具-商用\frontend
npm.cmd test
npm.cmd run lint
npm.cmd run build
```

Expected: all exit 0.

- [x] **Step 3: Start the app through the production local script**

```powershell
Set-Location E:\工具-商用
.\scripts\start-adflow-local.ps1
```

Expected: migration succeeds before API/Worker/Web processes start; `/api/health` returns `{"status":"ok"}`.

- [ ] **Step 4: Inspect both modes and responsive widths in the actual browser**

Verify `/preserve-product` contains no product replacement control and `/replace-product` requires the confirmed target product. Verify desktop 1440×900, tablet 920×900, and mobile 390×844 with no horizontal overflow.

Do not commit build output or screenshots unless the repository already tracks a dedicated visual-baseline directory.

### Task 5: Complete one controlled real Seedance closed loop

**Files:**
- Data only: local test project, media files and generated result
- No Git commit

**Interfaces:**
- Consumes: configured Volcengine or Comfly key and a 4—30 second reference video.
- Produces: one persistent completed generation plus restart-recovery evidence.

- [ ] **Step 1: Check provider readiness without exposing keys**

Open Settings, refresh preflight, and verify the chosen `volcengine_generation` or `comfly_generation` entry is ready. Run its connection test. Do not print `.env`.

- [ ] **Step 2: Create a controlled project through all six steps**

Use a 4—30 second decodable video. Complete local cut detection, save the timeline, finish dual-model shot analysis, confirm every shot fact, and save one `completed` prompt version tied to the current timeline.

- [ ] **Step 3: Verify the upload disclosure and submit once**

In step six, confirm the upload list contains the reference video; for replace mode, every confirmed target product image; the person image only when the prompt replaces the person; the background only when selected. Confirm `adaptive` and automatic duration are displayed as fixed. Click “确认并生成视频” once.

- [ ] **Step 4: Prove task recovery during processing**

After a supplier task ID appears, close the browser. Stop and restart only the API process while leaving PostgreSQL and Worker data intact; reopen the project. Verify the same generation version and external task ID are shown. Do not create another generation.

- [ ] **Step 5: Prove Worker recovery if the task is still active**

If status remains processing, stop the Worker, wait until the 10-minute lease expires only if a lease is currently held, restart the Worker, and verify polling resumes from the saved external task ID. Do not shorten production lease settings for this test.

- [ ] **Step 6: Verify permanent result behavior**

When status reaches completed, verify:

- browser player uses `/api/projects/{project_id}/generations/{generation_id}/content`;
- download returns an MP4;
- local file exists under `data/media/{project_id}/generated/v{version}.mp4`;
- FFprobe reads duration, width, height and frame rate;
- restarting API, Worker and browser still plays the same local result;
- “再次生成” creates version `v+1` and does not overwrite the earlier MP4.

- [ ] **Step 7: Handle a real uncertain submission conservatively if it occurs**

If the UI shows `submission_uncertain`, inspect the chosen supplier console. Attach the real supplier task ID if present; otherwise confirm no task exists and choose “确认供应商未创建”. Never use retry before that determination.

- [ ] **Step 8: Record the release evidence**

Record in the final implementation report: test counts; migration clone/fresh results; project ID; generation versions; provider; recovery actions; local result paths; FFprobe summary. Redact external task IDs except the last 6 characters and omit all signed URLs and credentials.
