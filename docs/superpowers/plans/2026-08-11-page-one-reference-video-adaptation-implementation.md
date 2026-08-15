# Page One Reference Video Adaptation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the Windows-local first version of the reference-video advertising adaptation page, from upload and analysis through editable time ranges, Seedance submission, task recovery, and versioned results.

**Architecture:** A React client talks only to a FastAPI API. FastAPI persists project state in PostgreSQL and writes media to a local project directory; a separate Python worker leases database jobs, performs FFmpeg, vision, TempFile and Seedance work, then persists every state transition. Provider adapters isolate Volcengine, Comfly, TempFile and visual models from the product workflow.

**Tech Stack:** React 19, TypeScript, Vite, TanStack Query, FastAPI, Pydantic v2, SQLAlchemy 2, Alembic, PostgreSQL 16, httpx, FFmpeg/ffprobe, pytest, Playwright.

## Global Constraints

- Run locally on Windows first; no Docker, Redis, user accounts, billing or automatic pass/fail quality gate.
- Do not send API keys to the browser, database request summaries or logs.
- Store originals, evidence frames and results locally; TempFile URLs are temporary transport copies only.
- Use TempFile only through an adapter and always record its expiry time.
- Reference-video requests always use `ratio: adaptive` and `duration: -1`.
- Page one never permits replacing the original product.
- Keep AI facts, inferences, uncertainties and user corrections separately.
- Keep generated results as immutable versions; regenerate never overwrites prior output.

---

## Planned File Structure

```text
backend/
  app/
    api/routes/{projects,analysis,generation,health}.py
    core/{config,logging,license}.py
    db/{base,models,session}.py
    domain/{enums,schemas}.py
    services/{media,tempfile,vision,seedance,prompting,projects}.py
    workers/{jobs,runner}.py
    main.py
  alembic/versions/0001_initial.py
  tests/{conftest,test_license,test_media,test_tempfile,test_projects,test_analysis,test_prompting,test_seedance,test_worker}.py
  pyproject.toml
  .env.example
frontend/
  src/{api,components,features,lib,pages,types}/
  e2e/page-one.spec.ts
  package.json
README.md
scripts/{start-api.ps1,start-worker.ps1,start-web.ps1}.ps1
```

## Task 1: Bootstrap the local services and authorisation guard

**Files:**
- Create: `backend/pyproject.toml`, `backend/.env.example`, `backend/app/main.py`, `backend/app/core/config.py`, `backend/app/core/logging.py`, `backend/app/core/license.py`, `backend/tests/conftest.py`, `backend/tests/test_license.py`
- Create: `scripts/start-api.ps1`, `scripts/start-worker.ps1`, `scripts/start-web.ps1`, `README.md`

**Interfaces:**
- Produces `Settings` loaded from environment, `create_app() -> FastAPI`, and `verify_license(license_path: Path, public_key_path: Path) -> LicenseClaims`.
- Required environment names: `DATABASE_URL`, `MEDIA_ROOT`, `APP_LICENSE_PATH`, `APP_LICENSE_PUBLIC_KEY_PATH`, `VOLCENGINE_API_KEY`, `COMFLY_API_KEY`, `OPENAI_API_KEY`.

- [ ] **Step 1: Write the failing licence tests.**

```python
def test_verify_license_accepts_valid_ed25519_signature(tmp_path: Path) -> None:
    claims = verify_license(tmp_path / "license.json", tmp_path / "public.pem")
    assert claims.author == "王嘉祺"

def test_verify_license_rejects_changed_payload(tmp_path: Path) -> None:
    with pytest.raises(LicenseError, match="signature"):
        verify_license(tmp_path / "license.json", tmp_path / "public.pem")
```

- [ ] **Step 2: Run `pytest tests/test_license.py -q` and confirm it fails because `verify_license` does not exist.**
- [ ] **Step 3: Implement Pydantic settings, JSON logging with secret redaction, Ed25519 detached-signature verification, and a FastAPI startup health route.**

```python
@dataclass(frozen=True)
class LicenseClaims:
    author: str
    issued_to: str

def verify_license(license_path: Path, public_key_path: Path) -> LicenseClaims:
    # verify canonical JSON bytes with the packaged public key, then return claims
```

- [ ] **Step 4: Add PowerShell scripts that create no processes in the background: API runs `python -m uvicorn app.main:app --reload`, worker runs `python -m app.workers.runner`, web runs `npm run dev`. Document setup, FFmpeg installation and each command in `README.md`.**
- [ ] **Step 5: Run `pytest tests/test_license.py -q` and `python -c "from app.main import create_app; assert create_app()"`; both pass.**
- [ ] **Step 6: Commit `feat: bootstrap local application services`.**

## Task 2: Persist projects, assets, timelines, jobs and immutable versions

**Files:**
- Create: `backend/app/db/{base,models,session}.py`, `backend/alembic.ini`, `backend/alembic/env.py`, `backend/alembic/versions/0001_initial.py`, `backend/app/domain/{enums,schemas}.py`, `backend/tests/test_projects.py`

**Interfaces:**
- Produces SQLAlchemy models: `Project`, `Asset`, `TimelineRevision`, `Shot`, `ShotChange`, `Generation`, `Job`.
- Produces `ProjectMode.REFERENCE_ADAPTATION`, `AssetKind.REFERENCE_VIDEO`, `JobKind.{EXTRACT_MEDIA,ANALYZE_VIDEO,ANALYZE_SHOT,PUBLISH_ASSET,SUBMIT_GENERATION,POLL_GENERATION,DOWNLOAD_RESULT}`.

- [ ] **Step 1: Write failing tests for project creation and immutable generation versions.**

```python
def test_generation_versions_never_overwrite_prior_result(db_session):
    project = create_project(db_session, "Spring campaign")
    first = create_generation(db_session, project.id, prompt_version=1)
    second = create_generation(db_session, project.id, prompt_version=1)
    assert first.version == 1 and second.version == 2
```

- [ ] **Step 2: Run `pytest tests/test_projects.py -q` and confirm it fails before the models exist.**
- [ ] **Step 3: Implement models and Alembic migration. Add database constraints for non-negative times, `end_sec > start_sec`, unique `(project_id, generation_version)`, and FK ownership. Store `observations`, `inferences`, `uncertainties`, and `user_corrections` in separate JSONB columns.**
- [ ] **Step 4: Run `alembic upgrade head` against a test PostgreSQL database, then run `pytest tests/test_projects.py -q`; both pass.**
- [ ] **Step 5: Commit `feat: add persistent project and version models`.**

## Task 3: Safely ingest media and derive factual evidence

**Files:**
- Create: `backend/app/services/media.py`, `backend/tests/test_media.py`

**Interfaces:**
- Produces `probe_video(path: Path) -> VideoMetadata`, `validate_reference_duration(metadata: VideoMetadata) -> list[ValidationIssue]`, `extract_keyframes(source: Path, destination: Path, timestamps: list[float]) -> list[EvidenceFrame]`, and `detect_candidate_cuts(source: Path) -> list[float]`.

- [ ] **Step 1: Write failing tests using a generated 13-second MP4 fixture.**

```python
def test_reference_duration_over_30_blocks_submission_only(video_31_seconds: Path) -> None:
    metadata = probe_video(video_31_seconds)
    assert validate_reference_duration(metadata)[0].code == "reference_video_too_long"

def test_candidate_cuts_are_real_timestamps(video_with_cut: Path) -> None:
    assert any(4.8 < cut < 5.2 for cut in detect_candidate_cuts(video_with_cut))
```

- [ ] **Step 2: Run `pytest tests/test_media.py -q` and confirm it fails before implementation.**
- [ ] **Step 3: Implement ffprobe JSON parsing, media decode validation, keyframe extraction and scene-change candidate extraction. Preserve the original file and return exact float timestamps; do not create fake shots.**
- [ ] **Step 4: Run `pytest tests/test_media.py -q`; it passes.**
- [ ] **Step 5: Commit `feat: add media ingestion and evidence extraction`.**

## Task 4: Expose projects, upload assets and validate product-locking

**Files:**
- Create: `backend/app/api/routes/projects.py`, `backend/app/services/projects.py`, `backend/tests/test_projects_api.py`
- Modify: `backend/app/main.py`

**Interfaces:**
- Produces `POST /api/projects`, `POST /api/projects/{project_id}/reference-video`, `GET /api/projects/{project_id}`, `POST /api/projects/{project_id}/assets`.
- `CreateProjectRequest(name: str)` creates only `REFERENCE_ADAPTATION` projects.

- [ ] **Step 1: Write failing API tests for multipart video upload and product-replacement rejection.**

```python
def test_upload_enqueues_media_extraction(client, mp4_bytes):
    project = client.post("/api/projects", json={"name": "Spring"}).json()
    response = client.post(f"/api/projects/{project['id']}/reference-video", files={"file": ("ad.mp4", mp4_bytes, "video/mp4")})
    assert response.status_code == 202

def test_page_one_rejects_product_replacement(client, project_id):
    response = client.post(f"/api/projects/{project_id}/prompt/validate", json={"text": "replace the serum with shampoo"})
    assert response.status_code == 422
```

- [ ] **Step 2: Run `pytest tests/test_projects_api.py -q` and confirm it fails.**
- [ ] **Step 3: Implement streamed uploads to `MEDIA_ROOT/<project-id>/original/`, MIME/decode validation, asset records, extraction job enqueueing, and Chinese validation messages. Use explicit product-change markers plus the locked product profile to prevent submission.**
- [ ] **Step 4: Run `pytest tests/test_projects_api.py -q`; it passes.**
- [ ] **Step 5: Commit `feat: add project upload APIs`.**

## Task 5: Build visual-analysis adapters and human-authoritative timelines

**Files:**
- Create: `backend/app/services/vision.py`, `backend/app/api/routes/analysis.py`, `backend/tests/test_analysis.py`
- Modify: `backend/app/domain/schemas.py`, `backend/app/main.py`

**Interfaces:**
- Produces `VisionProvider.analyze_video(...) -> VideoAnalysis`, `VisionProvider.analyze_images(...) -> ImageProfile`, `POST /api/projects/{id}/analysis/start`, `PUT /api/projects/{id}/timeline`, `POST /api/projects/{id}/timeline/{revision_id}/split`, `POST /api/projects/{id}/timeline/{revision_id}/merge`.
- `VideoAnalysis` has `summary`, `shots`, `observations`, `inferences`, `uncertainties`; each `Shot` contains only its validated `start_sec` and `end_sec`.

- [ ] **Step 1: Write failing tests using a fake `VisionProvider`.**

```python
def test_confirmed_timeline_reanalyses_only_changed_shots(client, project_with_five_shots):
    response = client.put("/api/projects/p1/timeline", json={"shots": [{"id": "s3", "start_sec": 4, "end_sec": 7.36}]})
    assert response.status_code == 202
    assert response.json()["enqueued_shot_ids"] == ["s2", "s3", "s4"]
```

- [ ] **Step 2: Run `pytest tests/test_analysis.py -q` and confirm it fails.**
- [ ] **Step 3: Implement the Volcengine video-analysis provider, OpenAI image-only verifier, JSON schema validation, timeline revision creation, drag-boundary validation, split/merge logic, and exact affected-shot recomputation. A no-cut video creates one shot; optional editing intervals are stored as changes, not cuts.**
- [ ] **Step 4: Run `pytest tests/test_analysis.py -q`; it passes.**
- [ ] **Step 5: Commit `feat: add visual analysis and editable timelines`.**

## Task 6: Generate editable prompts and audio/reference-image options

**Files:**
- Create: `backend/app/services/prompting.py`, `backend/tests/test_prompting.py`
- Modify: `backend/app/api/routes/analysis.py`, `backend/app/domain/schemas.py`

**Interfaces:**
- Produces `build_prompt(project: Project, timeline: TimelineRevision, settings: PromptSettings) -> str`, `validate_prompt_submission(...) -> list[ValidationIssue]`, `PUT /api/projects/{id}/prompt`.
- `PromptSettings.audio_mode` is `KEEP_ORIGINAL_BGM | STYLE`; only `STYLE` adds audio instructions. `use_person_reference_image: bool` defaults to `False`.

- [ ] **Step 1: Write failing prompt tests.**

```python
def test_keep_bgm_does_not_add_audio_instruction():
    assert "audio" not in build_prompt(project, timeline, PromptSettings(audio_mode="KEEP_ORIGINAL_BGM")).lower()

def test_style_audio_and_reference_image_are_explicit():
    prompt = build_prompt(project, timeline, PromptSettings(audio_mode="STYLE", audio_style="soft luxury ambient", use_person_reference_image=True))
    assert "soft luxury ambient" in prompt
```

- [ ] **Step 2: Run `pytest tests/test_prompting.py -q` and confirm it fails.**
- [ ] **Step 3: Implement the fact-first prompt template: global reference constraint, quality requirement, time-range changes, unchanged constraints, continuity constraints and prohibitions. Validate coverage, ordering, conflicts, product replacement and 4–30 second submission restriction.**
- [ ] **Step 4: Run `pytest tests/test_prompting.py -q`; it passes.**
- [ ] **Step 5: Commit `feat: add prompt generation and validation`.**

## Task 7: Publish temporary public media through TempFile

**Files:**
- Create: `backend/app/services/tempfile.py`, `backend/tests/test_tempfile.py`

**Interfaces:**
- Produces `TempFilePublisher.publish(path: Path, media_type: Literal["image", "video"]) -> PublishedAsset` and `assert_not_expired(asset: PublishedAsset, now: datetime) -> None`.
- `PublishedAsset` holds `file_id`, `public_url`, `expires_at`, `provider="tempfile"`; video URL ends `/download`, image URL ends `/preview`.

- [ ] **Step 1: Write failing tests with `httpx.MockTransport`.**

```python
def test_video_publisher_returns_download_url(mock_transport, sample_mp4):
    asset = TempFilePublisher(client).publish(sample_mp4, "video")
    assert asset.public_url.endswith("/download")
    assert asset.expires_at > datetime.now(UTC)

def test_expired_asset_cannot_be_submitted():
    with pytest.raises(ExpiredPublicAssetError):
        assert_not_expired(expired_asset, datetime.now(UTC))
```

- [ ] **Step 2: Run `pytest tests/test_tempfile.py -q` and confirm it fails.**
- [ ] **Step 3: Implement multipart upload with `files` and `expiryHours=24`, response schema validation, 100 MB preflight validation, expiry persistence and delete support. Never upload local paths or expose TempFile URLs outside authenticated project responses.**
- [ ] **Step 4: Run `pytest tests/test_tempfile.py -q`; it passes.**
- [ ] **Step 5: Commit `feat: add temporary public asset publisher`.**

## Task 8: Submit to both Seedance providers and recover task state

**Files:**
- Create: `backend/app/services/seedance.py`, `backend/app/api/routes/generation.py`, `backend/tests/test_seedance.py`
- Modify: `backend/app/main.py`

**Interfaces:**
- Produces `SeedanceProvider.submit(request: GenerationRequest) -> SubmittedTask`, `SeedanceProvider.get_status(task_id: str) -> ProviderTask`, `POST /api/projects/{id}/generations`, `GET /api/generations/{generation_id}`.
- Provider names are `volcengine` and `comfly`; normalized statuses are `queued`, `running`, `processing`, `succeeded`, `failed`, `cancelled`, `expired`.

- [ ] **Step 1: Write failing adapter tests for both providers.**

```python
def test_reference_video_forces_adaptive_and_minus_one(volcengine_client, generation_request):
    payload = volcengine_client.build_payload(generation_request)
    assert payload["ratio"] == "adaptive" and payload["duration"] == -1

def test_task_id_and_result_url_variants_are_normalized(comfly_client):
    assert comfly_client.parse_submit({"task_id": "t1"}).task_id == "t1"
    assert comfly_client.extract_video_url({"data": {"data": {"video_url": "https://x/video.mp4"}}}) == "https://x/video.mp4"
```

- [ ] **Step 2: Run `pytest tests/test_seedance.py -q` and confirm it fails.**
- [ ] **Step 3: Implement provider-specific endpoints/model IDs/headers, request builders, task-ID variants, status normalization, result URL variants, error normalization, and creation idempotency protection. The generation endpoint must persist a `Generation(status="submitting")` before network I/O and enqueue submission rather than blocking.**
- [ ] **Step 4: Run `pytest tests/test_seedance.py -q`; it passes.**
- [ ] **Step 5: Commit `feat: add Seedance provider adapters`.**

## Task 9: Run durable jobs and download completed results

**Files:**
- Create: `backend/app/workers/{jobs,runner}.py`, `backend/tests/test_worker.py`

**Interfaces:**
- Produces `lease_next_job(session, worker_id) -> Job | None`, `run_job(job_id: UUID) -> None`, and Worker job handlers for every `JobKind`.

- [ ] **Step 1: Write failing worker tests.**

```python
def test_worker_recovers_poll_job_after_restart(db_session):
    job = enqueue_poll_job(db_session, generation_id)
    run_job(job.id)
    assert db_session.get(Generation, generation_id).status in {"queued", "running", "succeeded"}

def test_successful_download_creates_new_result_version(db_session, http_server):
    run_job(enqueue_download_job(db_session, generation_id).id)
    assert latest_result(db_session, generation_id).local_path.exists()
```

- [ ] **Step 2: Run `pytest tests/test_worker.py -q` and confirm it fails.**
- [ ] **Step 3: Implement PostgreSQL row-lock job leasing, retry schedule for polling 429/500/502/503, per-request timeouts, restart-safe unfinished-job pickup, result download to `MEDIA_ROOT/<project-id>/results/<generation-version>/`, and terminal error persistence.**
- [ ] **Step 4: Run `pytest tests/test_worker.py -q`; it passes.**
- [ ] **Step 5: Commit `feat: add durable background worker`.**

## Task 10: Build the React project workspace and editing flow

**Files:**
- Create: `frontend/package.json`, `frontend/src/main.tsx`, `frontend/src/api/client.ts`, `frontend/src/types/index.ts`, `frontend/src/pages/{ProjectsPage,ProjectWorkspacePage}.tsx`
- Create: `frontend/src/features/{upload,timeline,editor,prompt,generation}/`, `frontend/src/components/{AppShell,VideoPlayer,StatusBadge}.tsx`
- Create: `frontend/e2e/page-one.spec.ts`

**Interfaces:**
- Consumes the REST endpoints from Tasks 4–9.
- Produces routes `/` and `/projects/:projectId`, with explicit UI states `uploading`, `analyzing`, `editing`, `ready_to_submit`, `generating`, `completed`, `failed`.

- [ ] **Step 1: Write a failing Playwright workflow test.**

```ts
test("user can correct a cut, edit a shot, inspect prompt, and create a generation", async ({ page }) => {
  await page.goto("/");
  await page.getByLabel("参考视频").setInputFiles("fixtures/reference.mp4");
  await page.getByRole("button", { name: "确认镜头划分" }).click();
  await page.getByRole("button", { name: "预览最终提示词" }).click();
  await expect(page.getByRole("button", { name: "提交生成" })).toBeEnabled();
});
```

- [ ] **Step 2: Run `npm run test:e2e -- page-one.spec.ts` and confirm it fails before the app exists.**
- [ ] **Step 3: Implement the three-column workspace: workflow/shot list, original video plus timeline, and current-shot editor. Include dedicated timeline-correction mode with drag boundaries, split, merge and restore-AI actions; hide complex ranges for one-take videos until “添加编辑区间” is selected.**
- [ ] **Step 4: Implement prompt review, audio fixed choices, optional person-reference-image toggle, provider choice, 30-second submit block, task state display, result playback/download, and regenerate action.**
- [ ] **Step 5: Run `npm run lint`, `npm run test:e2e -- page-one.spec.ts`, and a production build; all pass.**
- [ ] **Step 6: Commit `feat: build reference video adaptation workspace`.**

## Task 11: Integrate, secure and verify the local release

**Files:**
- Modify: `README.md`, `backend/.env.example`, `frontend/e2e/page-one.spec.ts`
- Create: `backend/tests/test_security.py`, `docs/operations/windows-local-runbook.md`

**Interfaces:**
- Verifies that the complete local workflow runs with fake providers and that secrets are redacted.

- [ ] **Step 1: Write failing security and recovery tests.**

```python
def test_api_never_returns_provider_key(client, monkeypatch):
    monkeypatch.setenv("VOLCENGINE_API_KEY", "secret-value")
    assert "secret-value" not in client.get("/api/projects/p1").text

def test_logs_redact_bearer_tokens(caplog):
    log_provider_error("Authorization: Bearer abc123")
    assert "abc123" not in caplog.text
```

- [ ] **Step 2: Run `pytest tests/test_security.py -q` and confirm it fails.**
- [ ] **Step 3: Implement redaction assertions, Windows firewall/reverse-proxy notes for future external deployment, database backup/restore commands, media-directory backup, TempFile expiry operation, and a real-provider smoke-test checklist requiring authorised low-cost assets.**
- [ ] **Step 4: Run backend tests, frontend lint/build and Playwright E2E together; all pass.**

```powershell
Set-Location backend; pytest -q
Set-Location ..\frontend; npm run lint; npm run build; npm run test:e2e
```

- [ ] **Step 5: Commit `docs: add local operations and security verification`.**

## Plan Self-Review

- **Spec coverage:** Tasks 1–11 cover Windows-local operation, the three-column editor, human time-axis correction, one-take optional intervals, product locking, asset text profiles and optional image reference, audio modes, both Seedance adapters, TempFile transport, restart-safe job recovery, versioned results, secret isolation and the requested hidden authorship signature.
- **Intentional exclusions:** Page two, page three, accounts, billing, Docker, Redis, automatic result quality gates and public deployment setup remain out of scope.
- **Placeholder scan:** No unfinished implementation markers are present.
- **Type consistency:** Project modes, job names, request settings and provider interfaces are defined before their later consumers.
