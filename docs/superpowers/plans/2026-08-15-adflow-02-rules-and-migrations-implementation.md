# AdFlow Rules and Migrations Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 固定严格双模式、补齐提示词历史接口，并让 Alembic 成为生产数据库结构的唯一权威。

**Architecture:** 用一个小型素材角色服务消除 `product_reference_image` 的历史语义混乱；路由和提示词 Worker 都从 `Project.mode` 读取规则。测试继续用 SQLAlchemy metadata 创建隔离 SQLite，生产启动只运行 Alembic。

**Tech Stack:** FastAPI、SQLAlchemy 2、Alembic、Pydantic、pytest、PowerShell、PostgreSQL。

## Global Constraints

- `preserve_product` 中 `product_reference_image` 代表原产品辅助图，不存在目标产品。
- `replace_product` 中 `product_reference_image` 代表唯一目标产品档案。
- 历史 `target_product_reference_image` 保留但不进入新提示词或生成。
- `replace_product` 完全由项目模式决定；前端字段不能改变它。
- 人物替换仍需成功并确认的人物档案。
- Alembic 运行时依赖，API 和 Worker 不调用 `create_schema()`。
- 不删除现有列、表、项目数据或 `data/media`。

---

## File Map

- Create: `backend/app/services/product_rules.py` — 按项目模式解析物理素材角色并统一检测替换表达。
- Modify: `backend/app/api/routes/projects.py` — 严格规则、提示词列表。
- Modify: `backend/app/services/final_prompt.py` — Worker 使用统一素材角色。
- Modify: `backend/tests/test_dual_product_workflows.py` — 严格模式回归。
- Modify: `backend/tests/test_projects_api.py` — 提示词历史过滤。
- Modify: `backend/app/db/models.py` — 生成闭环字段与索引。
- Replace: `backend/alembic/versions/0001_adflow_baseline.py` — 全新数据库完整基线。
- Create: `backend/alembic/versions/0002_generation_closed_loop.py` — 保留数据的增量升级。
- Modify: `backend/app/db/session.py` — 删除生产建表和手写 ALTER。
- Create: `backend/app/db/migrations.py` — 检查当前数据库是否位于唯一 Alembic head。
- Modify: `backend/app/main.py` — 删除启动时建表。
- Modify: `backend/app/worker.py` — 删除 Worker 启动时建表。
- Modify: `backend/tests/conftest.py` — 测试专用 metadata 建表。
- Replace: `backend/tests/test_schema.py` — 验证迁移头和模型字段。
- Modify: `backend/pyproject.toml` — Alembic 移入运行时依赖。
- Modify: `scripts/start-adflow-local.ps1` — 启动前升级。
- Modify: `backend/scripts/start-adflow-worker.ps1` — 单独启动 Worker 时也先升级。
- Create: `scripts/backup-adflow-database.ps1` — 明确备份路径的 pg_dump 包装。

### Task 1: Centralize product asset roles

**Files:**
- Create: `backend/app/services/product_assets.py`
- Modify: `backend/app/api/routes/projects.py`
- Modify: `backend/app/services/final_prompt.py`
- Modify: `backend/tests/test_dual_product_workflows.py`
- Modify: `backend/tests/test_projects_api.py`
- Test: `backend/tests/test_dual_product_workflows.py`, `backend/tests/test_projects_api.py`

**Interfaces:**
- Consumes: `Session`, `Project`, physical `Asset.kind`.
- Produces: `product_assets_for_project()`、`confirmed_target_product_assets()` and `contains_product_replacement()`.

- [ ] **Step 1: Write strict-role failing tests**

First remove `STRICT_MODE_DEFECT` from the three tests carried by plan 1 so they fail normally: `test_page_two_rejects_incompatible_actions_with_chinese_shot_details`, `test_preserve_product_mode_rejects_replacement_during_prompt_creation`, and `test_prompt_save_cannot_bypass_product_lock`. `test_page_two_applies_target_product_to_every_product_shot` is already green asynchronous coverage; retain it in the full dual-product module regression command without changing its status. Then add tests proving all three rules:

```python
def test_preserve_mode_rejects_replace_product_even_with_legacy_target_asset() -> None:
    client = _client()
    project = _project(client, "preserve_product")
    with SessionLocal() as session:
        session.add(Asset(
            project_id=UUID(project["id"]),
            kind="target_product_reference_image",
            original_path="C:/legacy.png",
            profile_text="旧目标产品",
            analysis_status="succeeded",
        ))
        session.commit()
    response = client.post(
        f"/api/projects/{project['id']}/prompts",
        json={"visual_direction": "保持节奏", "replace_product": True, "use_ai": False},
    )
    assert response.status_code == 422


def test_replace_mode_forces_replacement_when_client_sends_false() -> None:
    client = _client()
    project = _project(client, "replace_product")
    _upload_product(client, project["id"])
    client.put(
        f"/api/projects/{project['id']}/product-profile",
        json={"profile": "已确认盒装产品", "structure": {"summary_confirmed": True}},
    )
    timeline = client.put(
        f"/api/projects/{project['id']}/timeline",
        json={"shots": [{"start_sec": 0, "end_sec": 3}]},
    ).json()
    client.put(
        f"/api/projects/{project['id']}/shots/{timeline['shots'][0]['id']}/edit",
        json={"product": "原产品", "confirmed": True},
    )
    response = client.post(
        f"/api/projects/{project['id']}/prompts",
        json={"visual_direction": "保持节奏", "replace_product": False, "use_ai": False},
    )
    assert response.status_code == 201
    with SessionLocal() as session:
        revision = session.scalar(select(PromptRevision).where(
            PromptRevision.project_id == UUID(project["id"])
        ))
        assert revision.replace_product is True
```

The second test must create a `product_reference_image` whose latest `profile_json` contains `{"summary_confirmed": true}` and confirm every current `ShotEdit`.

- [ ] **Step 2: Run strict-role tests and verify failure**

```powershell
Set-Location E:\工具-商用\backend
python -m pytest tests/test_dual_product_workflows.py tests/test_projects_api.py -k "legacy_target or forces_replacement or prompt_save_cannot_bypass_product_lock" -q
```

Expected: FAIL because preserve mode currently accepts `payload.replace_product`.

- [ ] **Step 3: Create the role service**

```python
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import Asset, Project


def product_assets_for_project(session: Session, project: Project) -> list[Asset]:
    return list(session.scalars(
        select(Asset)
        .where(Asset.project_id == project.id, Asset.kind == "product_reference_image")
        .order_by(Asset.id)
    ))


def confirmed_target_product_assets(session: Session, project: Project) -> list[Asset]:
    if project.mode != "replace_product":
        return []
    return product_assets_for_project(session, project)
```

Move the existing `_contains_product_replacement` regular-expression implementation out of `projects.py` as public `contains_product_replacement(text: str) -> bool` in the same service; preserve its negative-phrase handling and Chinese/English patterns byte-for-byte before adding tests.

- [ ] **Step 4: Enforce project-owned replacement in both HTTP and Worker paths**

In `create_prompt_revision`:

```python
if project.mode == "preserve_product" and payload.replace_product:
    raise HTTPException(status_code=422, detail="保留产品模式不能开启产品替换。")
if project.mode == "preserve_product" and contains_product_replacement(payload.visual_direction):
    raise HTTPException(status_code=422, detail="保留产品模式的提示词不能替换产品。")
replace_product = project.mode == "replace_product"
product_assets = confirmed_target_product_assets(session, project)
product_profile = _combined_reference_profile(product_assets)
```

For replace mode, run `check_product_compatibility(product_profile, current_shots)` before creating the revision and return the existing structured 422 payload when conflicts exist.

In `execute_final_prompt_job`, load `Project`, call `confirmed_target_product_assets`, and pass an empty product profile when the mode is preserve. After GPT returns and before setting `completed`, reject output for which `contains_product_replacement()` is true in preserve mode. Apply the same post-generation check in `execute_prompt_refinement_job`. Remove every new-flow read of `target_product_reference_image`.

- [ ] **Step 5: Run dual-mode and prompt tests**

```powershell
python -m pytest tests/test_dual_product_workflows.py tests/test_prompting.py tests/test_simplified_prompt_workflow.py tests/test_projects_api.py -q
```

Expected: PASS.

- [ ] **Step 6: Commit strict mode rules**

```powershell
git add backend/app/services/product_rules.py backend/app/api/routes/projects.py backend/app/services/final_prompt.py backend/tests/test_dual_product_workflows.py backend/tests/test_projects_api.py
git commit -m "fix: enforce immutable product modes"
```

### Task 2: Add prompt revision listing for generation

**Files:**
- Modify: `backend/app/api/routes/projects.py`
- Modify: `backend/tests/test_projects_api.py`

**Interfaces:**
- Consumes: `GET /api/projects/{project_id}/prompts?current_timeline_only=true&status=completed`.
- Produces: `list[PromptRevisionSummary]` ordered by version descending.

- [ ] **Step 1: Write failing history and filter tests**

```python
def test_prompt_history_filters_to_completed_current_timeline() -> None:
    # Create timeline v1 + completed prompt v1, timeline v2 + queued v2 and completed v3.
    response = client.get(
        f"/api/projects/{project_id}/prompts?current_timeline_only=true&status=completed"
    )
    assert response.status_code == 200
    assert [item["version"] for item in response.json()] == [3]
    assert response.json()[0]["source_timeline_revision_id"] == timeline_v2_id


def test_prompt_history_rejects_unknown_status_filter() -> None:
    response = client.get(f"/api/projects/{project_id}/prompts?status=queued")
    assert response.status_code == 422
```

- [ ] **Step 2: Run the new tests**

```powershell
python -m pytest tests/test_projects_api.py -k "prompt_history" -q
```

Expected: FAIL with 405 or validation mismatch.

- [ ] **Step 3: Add the response model and route**

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
    project = session.get(Project, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="项目不存在")
    if status_filter not in {None, "completed"}:
        raise HTTPException(status_code=422, detail="提示词状态筛选只支持 completed")
    query = select(PromptRevision).where(PromptRevision.project_id == project_id)
    if status_filter:
        query = query.where(PromptRevision.status == status_filter)
    if current_timeline_only:
        current = session.scalar(select(TimelineRevision).where(
            TimelineRevision.project_id == project_id
        ).order_by(TimelineRevision.version.desc()))
        if current is None:
            return []
        query = query.where(PromptRevision.source_timeline_revision_id == current.id)
    return list(session.scalars(query.order_by(PromptRevision.version.desc())))
```

Import `datetime` and `Query` explicitly.

- [ ] **Step 4: Run prompt history and project API tests**

```powershell
python -m pytest tests/test_projects_api.py -q
```

Expected: PASS.

- [ ] **Step 5: Commit the prompt list API**

```powershell
git add backend/app/api/routes/projects.py backend/tests/test_projects_api.py
git commit -m "feat: list current prompt revisions"
```

### Task 3: Add generation closed-loop model fields

**Files:**
- Modify: `backend/app/db/models.py`
- Modify: `backend/tests/test_models.py`

**Interfaces:**
- Consumes: existing `Generation` rows.
- Produces: nullable snapshots plus status indexes without deleting existing fields.

- [ ] **Step 1: Write the model contract test**

```python
def test_generation_has_recovery_snapshot_fields() -> None:
    columns = Generation.__table__.columns
    assert {"request_snapshot", "reference_asset_ids", "provider_response_summary", "submission_fingerprint", "completed_at"} <= set(columns.keys())
    assert {tuple(index.columns.keys()) for index in Generation.__table__.indexes} >= {
        ("project_id", "status"),
        ("status", "next_attempt_at"),
        ("submission_fingerprint",),
    }
```

- [ ] **Step 2: Run the test and verify missing columns**

```powershell
python -m pytest tests/test_models.py::test_generation_has_recovery_snapshot_fields -q
```

Expected: FAIL.

- [ ] **Step 3: Add the exact model fields and indexes**

```python
from sqlalchemy import Index

class Generation(Base):
    __tablename__ = "generations"
    __table_args__ = (
        UniqueConstraint("project_id", "version"),
        Index("ix_generations_project_status", "project_id", "status"),
        Index("ix_generations_status_next_attempt", "status", "next_attempt_at"),
        Index("ix_generations_submission_fingerprint", "submission_fingerprint"),
    )
    request_snapshot: Mapped[str | None] = mapped_column(Text, nullable=True)
    reference_asset_ids: Mapped[str | None] = mapped_column(Text, nullable=True)
    provider_response_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    submission_fingerprint: Mapped[str | None] = mapped_column(String(64), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(nullable=True)
```

- [ ] **Step 4: Run model tests**

```powershell
python -m pytest tests/test_models.py -q
```

Expected: PASS.

- [ ] **Step 5: Commit the model contract**

```powershell
git add backend/app/db/models.py backend/tests/test_models.py
git commit -m "feat: persist generation recovery metadata"
```

### Task 4: Make Alembic the production schema authority

**Files:**
- Replace: `backend/alembic/versions/0001_adflow_baseline.py`
- Create: `backend/alembic/versions/0002_generation_closed_loop.py`
- Modify: `backend/app/db/session.py`
- Modify: `backend/app/main.py`
- Modify: `backend/app/worker.py`
- Modify: `backend/tests/conftest.py`
- Replace: `backend/tests/test_schema.py`
- Modify: `backend/pyproject.toml`

**Interfaces:**
- Consumes: existing installations stamped at `0001_adflow_baseline` and empty databases at base.
- Produces: head revision `0002_generation_closed_loop`.

- [ ] **Step 1: Write migration metadata tests**

```python
from pathlib import Path
from alembic.config import Config
from alembic.script import ScriptDirectory


def test_alembic_has_one_head_at_generation_closed_loop() -> None:
    config = Config(str(Path(__file__).parents[1] / "alembic.ini"))
    script = ScriptDirectory.from_config(config)
    assert script.get_heads() == ["0002_generation_closed_loop"]


def test_application_does_not_create_production_schema(monkeypatch) -> None:
    monkeypatch.setattr("app.db.session.Base.metadata.create_all", lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("create_all called")))
    create_app()
```

- [ ] **Step 2: Run migration metadata tests**

```powershell
python -m pytest tests/test_schema.py -q
```

Expected: FAIL because the current app calls `create_schema()` and only 0001 exists.

- [ ] **Step 3: Rewrite 0001 as a fixed full-schema snapshot**

Implement `upgrade()` exclusively with `op.create_table`, `op.create_index`, `op.create_unique_constraint`, and the four existing check constraints. Create these tables in dependency order with the columns exactly matching the pre-Task-3 model schema: `projects`, `assets`, `timeline_revisions`, `shots`, `shot_evidence`, `shot_ai_summaries`, `shot_edits`, `prompt_revisions`, `video_analyses`, `jobs`, `generations`. `downgrade()` drops them in reverse dependency order.

Do not import `Base` or live ORM models into the migration. Use PostgreSQL `UUID(as_uuid=True)` for UUID columns and `sa.DateTime(timezone=True)` for timestamps.

Use this fixed schema matrix when writing the `op.create_table` calls; nullable columns are marked `?`, and every unmarked column is non-null:

```text
projects: id UUID PK; name VARCHAR(200); mode VARCHAR(30); created_at TIMESTAMPTZ
assets: id UUID PK; project_id UUID FK projects; kind VARCHAR(50); original_path VARCHAR(500); original_filename VARCHAR(255)?; content_type VARCHAR(100)?; size_bytes INTEGER?; duration_sec FLOAT?; width INTEGER?; height INTEGER?; fps FLOAT?; public_url VARCHAR(1000)?; public_url_expires_at VARCHAR(40)?; profile_text TEXT?; profile_json TEXT?; profile_user_edited BOOLEAN; analysis_status VARCHAR(20)?; analysis_error TEXT?
timeline_revisions: id UUID PK; project_id UUID FK projects; version INTEGER; source VARCHAR(20); UNIQUE(project_id, version)
shots: id UUID PK; timeline_revision_id UUID FK timeline_revisions; position INTEGER; start_sec FLOAT; end_sec FLOAT; people TEXT?; action TEXT?; product TEXT?; product_interaction TEXT?; background TEXT?; camera TEXT?; lighting TEXT?; visual_style TEXT?; keep_unchanged TEXT?; on_screen_text TEXT?; observations TEXT?; inferences TEXT?; uncertainties TEXT?; analysis_status VARCHAR(20); analysis_error TEXT?; UNIQUE(timeline_revision_id, position)
shot_evidence: id UUID PK; shot_id UUID FK shots; timestamp_sec FLOAT; image_path VARCHAR(500); source VARCHAR(30); observation TEXT?
shot_ai_summaries: id UUID PK; shot_id UUID FK shots; version INTEGER; content TEXT; created_at TIMESTAMPTZ; UNIQUE(shot_id, version)
shot_edits: id UUID PK; project_id UUID FK projects; shot_id UUID FK shots; people TEXT?; action TEXT?; product TEXT?; product_interaction TEXT?; background TEXT?; camera TEXT?; lighting TEXT?; visual_style TEXT?; visible_text TEXT?; uncertainties TEXT?; keep_unchanged TEXT?; confirmed BOOLEAN; version INTEGER; ai_summary_version INTEGER; UNIQUE(project_id, shot_id)
prompt_revisions: id UUID PK; project_id UUID FK projects; version INTEGER; text TEXT; visual_direction TEXT; audio_mode VARCHAR(30); audio_style VARCHAR(500); replace_product BOOLEAN; replace_person BOOLEAN; source_timeline_revision_id UUID? FK timeline_revisions; status VARCHAR(20); error_message TEXT?; created_at TIMESTAMPTZ; UNIQUE(project_id, version)
video_analyses: id UUID PK; project_id UUID FK projects; job_id UUID? FK jobs; shot_id UUID? FK shots; provider VARCHAR(50); raw_content TEXT; observations TEXT?; inferences TEXT?; uncertainties TEXT?
jobs: id UUID PK; project_id UUID FK projects; shot_id UUID? FK shots; kind VARCHAR(50); status VARCHAR(20); provider VARCHAR(50)?; external_task_id VARCHAR(255)?; provider_input_id VARCHAR(255)?; error_message TEXT?; attempts INTEGER; next_attempt_at TIMESTAMPTZ?; leased_at TIMESTAMPTZ?; leased_by VARCHAR(100)?; created_at TIMESTAMPTZ
generations: id UUID PK; project_id UUID FK projects; version INTEGER; prompt_version INTEGER; provider VARCHAR(50); ratio VARCHAR(10); duration INTEGER; generate_audio BOOLEAN; reference_image_urls TEXT?; external_task_id VARCHAR(255)?; status VARCHAR(30); result_url VARCHAR(1000)?; error_message TEXT?; attempts INTEGER; next_attempt_at TIMESTAMPTZ?; leased_at TIMESTAMPTZ?; leased_by VARCHAR(100)?; created_at TIMESTAMPTZ; result_path VARCHAR(500)?; UNIQUE(project_id, version)
```

Because `video_analyses.job_id` references `jobs` while `jobs` has no dependency on `video_analyses`, create `jobs` before `video_analyses` even though the list above groups analysis tables together. Apply server defaults matching current ORM defaults for existing required booleans, counters, statuses and timestamps.

- [ ] **Step 4: Add the additive 0002 migration**

```python
revision = "0002_generation_closed_loop"
down_revision = "0001_adflow_baseline"


def upgrade() -> None:
    op.add_column("generations", sa.Column("request_snapshot", sa.Text(), nullable=True))
    op.add_column("generations", sa.Column("reference_asset_ids", sa.Text(), nullable=True))
    op.add_column("generations", sa.Column("provider_response_summary", sa.Text(), nullable=True))
    op.add_column("generations", sa.Column("submission_fingerprint", sa.String(length=64), nullable=True))
    op.add_column("generations", sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True))
    op.create_index("ix_generations_project_status", "generations", ["project_id", "status"])
    op.create_index("ix_generations_status_next_attempt", "generations", ["status", "next_attempt_at"])
    op.create_index("ix_generations_submission_fingerprint", "generations", ["submission_fingerprint"])
```

`downgrade()` drops the three indexes first, then the five columns in reverse order.

- [ ] **Step 5: Remove production schema mutation and keep test schema creation explicit**

Reduce `backend/app/db/session.py` to engine/session/get-session responsibilities; delete `create_schema()` and imports of `text` and `Base`. Remove `create_schema()` calls from `main.py` and `worker.py`.

Create `backend/app/db/migrations.py` so production processes fail closed on a stale schema without modifying it:

```python
from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.script import ScriptDirectory

from app.core.config import BACKEND_ROOT
from app.db.session import engine


def require_database_at_head() -> None:
    if engine.dialect.name != "postgresql":
        return
    config = Config(str(BACKEND_ROOT / "alembic.ini"))
    expected = ScriptDirectory.from_config(config).get_current_head()
    with engine.connect() as connection:
        actual = MigrationContext.configure(connection).get_current_revision()
    if actual != expected:
        raise RuntimeError(f"Database revision {actual or 'none'} is behind required revision {expected}. Run Alembic upgrade first.")
```

Call `require_database_at_head()` near the start of `create_app()` and `worker.main()`. SQLite tests bypass the PostgreSQL-only check.

In `backend/tests/conftest.py`, after the environment variables and imports are safe:

```python
from app.db.base import Base
from app.db import models  # noqa: F401
from app.db.session import engine


def pytest_sessionstart(session) -> None:
    Base.metadata.create_all(engine)
```

- [ ] **Step 6: Move Alembic into runtime dependencies**

```toml
dependencies = [
  "alembic>=1.14,<2",
  # existing runtime dependencies remain unchanged
]

[project.optional-dependencies]
dev = ["httpx>=0.28,<1", "pytest>=8.3,<9"]
```

- [ ] **Step 7: Run schema and full backend tests**

```powershell
python -m pytest tests/test_schema.py -q
python -m pytest -q
```

Expected: PASS; `create_app()` no longer creates tables.

- [ ] **Step 8: Commit Alembic authority**

```powershell
git add backend/alembic/versions/0001_adflow_baseline.py backend/alembic/versions/0002_generation_closed_loop.py backend/app/db/session.py backend/app/db/migrations.py backend/app/main.py backend/app/worker.py backend/tests/conftest.py backend/tests/test_schema.py backend/pyproject.toml
git commit -m "refactor: make alembic the schema authority"
```

### Task 5: Gate local startup on migration and add backup script

**Files:**
- Modify: `scripts/start-adflow-local.ps1`
- Modify: `backend/scripts/start-adflow-worker.ps1`
- Create: `scripts/backup-adflow-database.ps1`
- Test: PowerShell parse and failure behavior

**Interfaces:**
- Consumes: `backend/.env` and a user-supplied backup directory.
- Produces: a non-empty `.dump` file and migration-before-process startup.

- [ ] **Step 1: Add a parse-level test command before editing**

```powershell
$errors = $null
[System.Management.Automation.Language.Parser]::ParseFile('E:\工具-商用\scripts\start-adflow-local.ps1', [ref]$null, [ref]$errors) | Out-Null
if ($errors.Count) { throw ($errors | Out-String) }
```

- [ ] **Step 2: Run Alembic synchronously before opening background processes**

Insert after FFmpeg checks and before port checks:

```powershell
Push-Location (Join-Path $root 'backend')
try {
    python -m alembic -c alembic.ini upgrade head
    if ($LASTEXITCODE -ne 0) { throw "Database migration failed with exit code $LASTEXITCODE." }
} finally {
    Pop-Location
}
```

Update `backend/scripts/start-adflow-worker.ps1` with the same synchronous Alembic command before `python -m app.worker`; throw when `$LASTEXITCODE -ne 0`.

- [ ] **Step 3: Implement explicit backup output handling**

The new script accepts:

```powershell
param([Parameter(Mandatory)][string]$OutputDirectory)
```

It reads `DATABASE_URL` from `backend/.env`, parses the PostgreSQL URI with `[System.Uri]`, URL-decodes username/password/database, creates the supplied directory, sets `PGPASSWORD` only for the current process, and invokes:

```powershell
& $pgDump.Source --format=custom --no-owner --no-privileges --host=$($uri.Host) --port=$($uri.Port) --username=$username --file=$outputPath $database
```

After the command, throw unless `$LASTEXITCODE -eq 0`, the output exists, and `(Get-Item -LiteralPath $outputPath).Length -gt 0`. Remove `Env:PGPASSWORD` in `finally`. Return the absolute backup path with `Write-Output`.

- [ ] **Step 4: Parse both PowerShell scripts**

```powershell
$files = @('E:\工具-商用\scripts\start-adflow-local.ps1','E:\工具-商用\backend\scripts\start-adflow-worker.ps1','E:\工具-商用\scripts\backup-adflow-database.ps1')
foreach ($file in $files) {
    $tokens = $null; $errors = $null
    [System.Management.Automation.Language.Parser]::ParseFile($file, [ref]$tokens, [ref]$errors) | Out-Null
    if ($errors.Count) { throw "$file`n$($errors | Out-String)" }
}
```

Expected: no parser errors.

- [ ] **Step 5: Commit startup migration and backup**

```powershell
git add scripts/start-adflow-local.ps1 backend/scripts/start-adflow-worker.ps1 scripts/backup-adflow-database.ps1
git commit -m "feat: migrate and back up local database safely"
```
