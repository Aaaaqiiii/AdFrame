# AdFlow Generation Backend Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 完成可恢复、不会因模糊超时重复扣费、能把 Seedance 结果可靠保存到本地的生成后端。

**Architecture:** 创建 API 只验证不可变输入并入队；数据库记录保存素材 ID 和输入快照。Worker 在执行时发布或续期临时 URL、提交供应商、立即持久化任务 ID、轮询并原子下载结果；浏览器和 API 重启不改变任务权威状态。

**Tech Stack:** FastAPI、Pydantic、SQLAlchemy 2、requests、PostgreSQL/SQLite、pytest、FFprobe。

## Global Constraints

- HTTP 创建请求不得调用 TempFile 或 Seedance。
- 活跃重复指纹状态为 `queued`、`processing`、`retryable`、`submission_uncertain`。
- 模糊提交超时进入 `submission_uncertain`，不得自动再次提交。
- `completed` 仅表示本地 MP4 下载完成、FFprobe 成功且原子重命名完成。
- `ratio` 固定 `adaptive`，`duration` 固定 `-1`，客户端字段被 Pydantic 拒绝。
- 结果路径只能由服务端从项目 ID 和生成版本计算。
- API、快照和日志不得保存 API Key 或完整临时 URL 查询参数。

---

## File Map

- Replace: `backend/app/api/routes/generations.py` — 列表、创建、详情、重试、人工解析、下载。
- Modify: `backend/app/api/routes/projects.py` — 暴露人物档案人工确认状态。
- Modify: `backend/app/services/seedance.py` — 供应商异常分类和响应摘要。
- Modify: `backend/app/services/generation_jobs.py` — 安全提交、轮询、下载和状态机。
- Modify: `backend/app/worker.py` — 从素材 ID 恢复输入并续期临时 URL。
- Modify: `backend/tests/conftest.py` — 生成 API/Worker 共用的持久化 fixture。
- Create: `backend/tests/test_generations_api.py` — 生成 API 合约。
- Create: `backend/tests/test_generation_jobs.py` — Worker 状态与文件合约。
- Modify: `backend/tests/test_seedance.py` — 网关异常分类。
- Modify: `backend/tests/test_worker.py` — 领取、租约、模糊状态。

## Shared Test Fixtures

Before Task 1 tests, add these fixtures to `backend/tests/conftest.py`; later tasks may extend the returned rows but must keep these names:

```python
from datetime import UTC, datetime
from uuid import UUID

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def client():
    from app.main import create_app
    with TestClient(create_app()) as value:
        yield value


@pytest.fixture()
def generation_factory():
    from app.db.models import Generation, Project
    from app.db.session import SessionLocal

    def create(*, status="queued", version=1, result_path=None, external_task_id=None, project_id=None):
        with SessionLocal() as session:
            project = session.get(Project, project_id) if project_id else None
            if project is None:
                project = Project(name=f"generation-{version}", mode="preserve_product")
                session.add(project)
                session.flush()
            generation = Generation(
                project_id=project.id,
                version=version,
                prompt_version=1,
                provider="volcengine",
                ratio="adaptive",
                duration=-1,
                status=status,
                result_path=result_path,
                external_task_id=external_task_id,
                attempts=0,
                created_at=datetime.now(UTC),
            )
            session.add(generation)
            session.commit()
            return generation
    return create


@pytest.fixture()
def processing_generation(generation_factory):
    return generation_factory(status="processing", external_task_id="provider-task-1")


@pytest.fixture()
def queued_generation(generation_factory):
    return generation_factory(status="queued")


@pytest.fixture()
def failed_generation(generation_factory):
    return generation_factory(status="failed")


@pytest.fixture()
def uncertain_generation(generation_factory):
    return generation_factory(status="submission_uncertain")
```

In `backend/tests/test_generations_api.py`, add an autouse provider configuration and this helper:

```python
from types import SimpleNamespace


@pytest.fixture(autouse=True)
def configured_generation_provider(monkeypatch):
    monkeypatch.setenv("VOLCENGINE_API_KEY", "test-key")


def _ready_project(client, tmp_path, mode="preserve_product", replace_person=False):
    project_body = client.post(
        "/api/projects", json={"name": f"ready-{mode}", "mode": mode}
    ).json()
    project_id = UUID(project_body["id"])
    video_path = tmp_path / "reference.mp4"
    video_path.write_bytes(b"stored-video")
    with SessionLocal() as session:
        video = Asset(
            project_id=project_id,
            kind="reference_video",
            original_path=str(video_path),
            original_filename="reference.mp4",
            content_type="video/mp4",
            duration_sec=8,
        )
        revision = TimelineRevision(project_id=project_id, version=1, source="human")
        session.add_all([video, revision])
        session.flush()
        shot = Shot(timeline_revision_id=revision.id, position=0, start_sec=0, end_sec=8, analysis_status="succeeded")
        session.add(shot)
        session.flush()
        session.add(ShotEdit(project_id=project_id, shot_id=shot.id, action="展示产品", confirmed=True))
        prompt = PromptRevision(
            project_id=project_id,
            version=1,
            text="保持当前镜头和产品",
            replace_product=mode == "replace_product",
            replace_person=replace_person,
            source_timeline_revision_id=revision.id,
            status="completed",
        )
        session.add(prompt)
        if mode == "replace_product":
            product_path = tmp_path / "product.png"
            product_path.write_bytes(b"product")
            session.add(Asset(
                project_id=project_id,
                kind="product_reference_image",
                original_path=str(product_path),
                original_filename="product.png",
                content_type="image/png",
                profile_text="已确认目标产品",
                profile_json='{"summary_confirmed": true}',
                profile_user_edited=True,
                analysis_status="succeeded",
            ))
        if replace_person:
            person_path = tmp_path / "person.png"
            person_path.write_bytes(b"person")
            session.add(Asset(
                project_id=project_id,
                kind="person_reference_image",
                original_path=str(person_path),
                original_filename="person.png",
                content_type="image/png",
                profile_text="已确认人物",
                profile_user_edited=True,
                analysis_status="succeeded",
            ))
        session.commit()
        return SimpleNamespace(
            project_id=project_id,
            video_asset_id=video.id,
            prompt_version=prompt.version,
            generations_url=f"/api/projects/{project_id}/generations",
            payload={
                "provider": "volcengine",
                "prompt_version": prompt.version,
                "generate_audio": False,
                "include_person_reference": replace_person,
                "include_background_reference": False,
            },
        )
```

### Task 1: Define complete generation API responses

**Files:**
- Replace: `backend/app/api/routes/generations.py`
- Create: `backend/tests/test_generations_api.py`

**Interfaces:**
- Consumes: `Generation` ORM rows.
- Produces: `generation_response()` used by create, list, detail, retry and resolve routes.

- [ ] **Step 1: Write failing list/detail response tests**

```python
def test_generation_list_is_version_descending_and_builds_local_url(client, generation_factory, tmp_path) -> None:
    old = generation_factory(version=1, status="failed")
    local_file = tmp_path / "v2.mp4"
    local_file.write_bytes(b"video")
    latest = generation_factory(version=2, status="completed", result_path=str(local_file), project_id=old.project_id)
    response = client.get(f"/api/projects/{old.project_id}/generations")
    assert response.status_code == 200
    assert [item["version"] for item in response.json()] == [2, 1]
    assert response.json()[0]["local_video_url"].endswith(f"/{latest.id}/content")


def test_generation_detail_builds_local_url_instead_of_returning_raw_orm(client, generation_factory, tmp_path) -> None:
    local_file = tmp_path / "v1.mp4"
    local_file.write_bytes(b"video")
    generation = generation_factory(status="completed", result_path=str(local_file))
    body = client.get(f"/api/projects/{generation.project_id}/generations/{generation.id}").json()
    assert body["local_video_url"].endswith(f"/{generation.id}/content")
```

- [ ] **Step 2: Run the tests**

```powershell
Set-Location E:\工具-商用\backend
python -m pytest tests/test_generations_api.py -k "list or detail" -q
```

Expected: FAIL because list is missing and detail does not populate `local_video_url`.

- [ ] **Step 3: Define the response model and single constructor**

```python
class GenerationResponse(BaseModel):
    id: UUID
    version: int
    prompt_version: int
    provider: str
    status: str
    generate_audio: bool
    external_task_id: str | None
    attempts: int
    next_attempt_at: datetime | None
    result_url: str | None
    local_video_url: str | None
    error_message: str | None
    created_at: datetime
    completed_at: datetime | None


def generation_response(project_id: UUID, generation: Generation) -> GenerationResponse:
    return GenerationResponse(
        id=generation.id,
        version=generation.version,
        prompt_version=generation.prompt_version,
        provider=generation.provider,
        status=generation.status,
        generate_audio=generation.generate_audio,
        external_task_id=generation.external_task_id,
        attempts=generation.attempts,
        next_attempt_at=generation.next_attempt_at,
        result_url=generation.result_url,
        local_video_url=(
            f"/api/projects/{project_id}/generations/{generation.id}/content"
            if generation.result_path and Path(generation.result_path).is_file() else None
        ),
        error_message=generation.error_message,
        created_at=generation.created_at,
        completed_at=generation.completed_at,
    )
```

Add `GET ""` ordered by `Generation.version.desc()` and make `GET /{generation_id}` return `generation_response()`.

- [ ] **Step 4: Run response tests**

```powershell
python -m pytest tests/test_generations_api.py -k "list or detail" -q
```

Expected: PASS.

- [ ] **Step 5: Commit response consistency**

```powershell
git add backend/app/api/routes/generations.py backend/tests/conftest.py backend/tests/test_generations_api.py
git commit -m "feat: expose generation history consistently"
```

### Task 2: Queue immutable validated inputs

**Files:**
- Modify: `backend/app/api/routes/generations.py`
- Modify: `backend/app/api/routes/projects.py`
- Modify: `backend/tests/test_generations_api.py`

**Interfaces:**
- Consumes: `CreateGenerationRequest(provider, prompt_version, generate_audio, include_person_reference, include_background_reference)`.
- Produces: queued `Generation` with `reference_asset_ids`, `submission_fingerprint`, `ratio="adaptive"`, `duration=-1`.

- [ ] **Step 1: Write failing create-contract tests**

Cover these exact cases:

```python
def test_create_rejects_client_ratio_and_duration(client, tmp_path) -> None:
    ready_project = _ready_project(client, tmp_path)
    response = client.post(ready_project.generations_url, json={
        "provider": "volcengine", "prompt_version": 1,
        "ratio": "16:9", "duration": 8,
        "generate_audio": True,
        "include_person_reference": False,
        "include_background_reference": False,
    })
    assert response.status_code == 422


def test_create_only_queues_and_stores_asset_ids(client, tmp_path, monkeypatch) -> None:
    ready_project = _ready_project(client, tmp_path)
    monkeypatch.setattr(TempfilePublisher, "publish", lambda *_: (_ for _ in ()).throw(AssertionError("published in HTTP")))
    response = client.post(ready_project.generations_url, json=ready_project.payload)
    assert response.status_code == 202
    with SessionLocal() as session:
        generation = session.get(Generation, UUID(response.json()["id"]))
        assert generation.status == "queued"
        assert json.loads(generation.reference_asset_ids)[0] == str(ready_project.video_asset_id)
        assert generation.reference_image_urls is None
        assert generation.ratio == "adaptive"
        assert generation.duration == -1
```

Also test: old timeline prompt rejected; non-completed prompt rejected; unconfirmed shot rejected; missing provider key returns 409; reference >30 seconds rejected; replace mode automatically includes every confirmed `product_reference_image`; an unresolved product/action compatibility conflict rejects generation; preserve mode ignores `target_product_reference_image`; person flag mismatch with prompt snapshot rejected; unconfirmed person profile rejected; optional background missing rejected instead of silently ignored.

- [ ] **Step 2: Run create tests**

```powershell
python -m pytest tests/test_generations_api.py -k "create" -q
```

Expected: FAIL under the current synchronous publisher behavior.

- [ ] **Step 3: Use a strict request model**

```python
from pydantic import BaseModel, ConfigDict, Field


class CreateGenerationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    provider: str = Field(pattern="^(volcengine|comfly)$")
    prompt_version: int = Field(gt=0)
    generate_audio: bool = False
    include_person_reference: bool = False
    include_background_reference: bool = False
```

- [ ] **Step 4: Resolve and validate immutable inputs without publishing**

Build `asset_ids` in this order: current reference video; all confirmed target product assets for replace mode; confirmed person asset when the prompt snapshot has `replace_person=True`; selected background asset. Reject mismatches instead of omitting them. In replace mode call `check_product_compatibility()` against current shots again and reject non-empty conflicts, because a prompt may predate a later manual shot edit.

Expose `person_profile_confirmed: bool` from project details using `bool(person_asset and person_asset.profile_user_edited)`.

Compute the fingerprint from stable local identity, never public URLs:

```python
fingerprint_payload = {
    "project_id": str(project.id),
    "timeline_revision_id": str(current_revision.id),
    "prompt_revision_id": str(prompt.id),
    "provider": payload.provider,
    "generate_audio": payload.generate_audio,
    "asset_ids": [str(item.id) for item in assets],
}
fingerprint = hashlib.sha256(
    json.dumps(fingerprint_payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
).hexdigest()
```

Query active duplicate fingerprints and return 409. Lock the `Project` row with `select(Project).where(Project.id == project_id).with_for_update()` before calculating `max(version)+1`.

- [ ] **Step 5: Run create and strict-mode tests**

```powershell
python -m pytest tests/test_generations_api.py tests/test_dual_product_workflows.py -q
```

Expected: PASS.

- [ ] **Step 6: Commit immutable queueing**

```powershell
git add backend/app/api/routes/generations.py backend/app/api/routes/projects.py backend/tests/test_generations_api.py
git commit -m "feat: queue validated generation snapshots"
```

### Task 3: Add retry and uncertain-submission resolution APIs

**Files:**
- Modify: `backend/app/api/routes/generations.py`
- Modify: `backend/tests/test_generations_api.py`

**Interfaces:**
- Consumes: `POST /{generation_id}/retry` and `POST /{generation_id}/resolve`.
- Produces: new failed-task version or a resolved uncertain task.

- [ ] **Step 1: Write failing transition tests**

```python
def test_retry_creates_new_version_without_mutating_failed_row(client, failed_generation) -> None:
    response = client.post(f"/api/projects/{failed_generation.project_id}/generations/{failed_generation.id}/retry")
    assert response.status_code == 202
    assert response.json()["version"] == failed_generation.version + 1
    assert response.json()["status"] == "queued"
    with SessionLocal() as session:
        original = session.get(Generation, failed_generation.id)
        assert original.status == "failed"


def test_uncertain_task_requires_explicit_resolution(client, uncertain_generation) -> None:
    attach = client.post(f"/api/projects/{uncertain_generation.project_id}/generations/{uncertain_generation.id}/resolve", json={
        "action": "attach_task", "external_task_id": "provider-123"
    })
    assert attach.status_code == 200
    assert attach.json()["status"] == "processing"
```

Also assert retry rejects `queued`, `processing`, `retryable`, `submission_uncertain`, and `completed`; resolve rejects non-uncertain states; `confirm_not_created` sets `failed`; `attach_task` requires a nonblank ID of at most 255 characters.

- [ ] **Step 2: Run transition tests**

```powershell
python -m pytest tests/test_generations_api.py -k "retry or uncertain" -q
```

Expected: FAIL with missing routes.

- [ ] **Step 3: Implement explicit models and transitions**

```python
class ResolveGenerationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action: Literal["attach_task", "confirm_not_created"]
    external_task_id: str | None = Field(default=None, max_length=255)
```

Retry copies provider, prompt version, fixed ratio/duration, audio, `reference_asset_ids`, `request_snapshot`, and fingerprint into a new queued row with a new locked version. Resolve attaches a stripped task ID and sets `processing`, or sets `failed` with `error_message="用户确认供应商未创建任务"`. Clear lease fields and set `next_attempt_at=None` in both actions.

- [ ] **Step 4: Run transition tests**

```powershell
python -m pytest tests/test_generations_api.py -k "retry or uncertain" -q
```

Expected: PASS.

- [ ] **Step 5: Commit recovery APIs**

```powershell
git add backend/app/api/routes/generations.py backend/tests/test_generations_api.py
git commit -m "feat: resolve and retry generation tasks"
```

### Task 4: Make provider submission ambiguity explicit

**Files:**
- Modify: `backend/app/services/seedance.py`
- Modify: `backend/app/services/generation_jobs.py`
- Modify: `backend/tests/test_seedance.py`
- Create: `backend/tests/test_generation_jobs.py`

**Interfaces:**
- Consumes: `JsonTaskGateway.submit()` and `GenerationGateway`.
- Produces: `SubmissionUncertainError`, immediate task ID commit, sanitized response summary.

- [ ] **Step 1: Write gateway and job failing tests**

```python
def test_submit_timeout_is_classified_as_uncertain() -> None:
    class Http:
        def post(self, *_args, **_kwargs):
            raise requests.Timeout("read timed out")
    gateway = JsonTaskGateway("https://provider", "secret", http=Http())
    with pytest.raises(SubmissionUncertainError):
        gateway.submit({"model": "seedance"})


def test_uncertain_submission_is_never_automatically_retried(queued_generation) -> None:
    gateway = Mock()
    gateway.submit.side_effect = SubmissionUncertainError("供应商提交结果不明确")
    with SessionLocal() as session:
        generation = session.get(Generation, queued_generation.id)
        execute_generation_job(session, generation, gateway, {"model": "seedance"})
        assert generation.status == "submission_uncertain"
        assert generation.next_attempt_at is None
        assert generation.attempts == 1
```

Also test that a returned task ID is committed before the first `get_result()` call by observing it from a second session inside the fake gateway.

- [ ] **Step 2: Run the tests**

```powershell
python -m pytest tests/test_seedance.py tests/test_generation_jobs.py -k "submit or task_id" -q
```

Expected: FAIL.

- [ ] **Step 3: Add the explicit exception and sanitize summaries**

```python
class SubmissionUncertainError(RuntimeError):
    pass


def sanitize_provider_summary(value: object) -> str:
    text = json.dumps(value, ensure_ascii=False, default=str)
    text = re.sub(r"(?i)(authorization|api[_-]?key|token)(\"?\s*[:=]\s*\"?)[^\",\s]+", r"\1\2***", text)
    return text[:4000]
```

Wrap only `requests.Timeout` and `requests.ConnectionError` from `submit()` as `SubmissionUncertainError`; deterministic HTTP 4xx/5xx continues through normal retry/failure classification.

- [ ] **Step 4: Handle uncertain submission before the generic exception block**

```python
except SubmissionUncertainError as exc:
    generation.attempts += 1
    generation.status = "submission_uncertain"
    generation.error_message = str(exc)
    generation.next_attempt_at = None
    session.commit()
```

After `gateway.submit(payload)` returns, set `external_task_id`, `status="processing"`, sanitized `provider_response_summary`, then commit before polling.

- [ ] **Step 5: Run gateway/job tests**

```powershell
python -m pytest tests/test_seedance.py tests/test_generation_jobs.py -q
```

Expected: PASS.

- [ ] **Step 6: Commit ambiguity-safe submission**

```powershell
git add backend/app/services/seedance.py backend/app/services/generation_jobs.py backend/tests/test_seedance.py backend/tests/test_generation_jobs.py
git commit -m "fix: prevent duplicate ambiguous submissions"
```

### Task 5: Restore inputs in the Worker and renew temporary URLs

**Files:**
- Modify: `backend/app/worker.py`
- Modify: `backend/app/services/generation_jobs.py`
- Modify: `backend/tests/test_worker.py`
- Modify: `backend/tests/test_generation_jobs.py`

**Interfaces:**
- Consumes: ordered `Generation.reference_asset_ids` and local `Asset.original_path`.
- Produces: fresh provider payload saved as redacted `request_snapshot`.

- [ ] **Step 1: Write failing recovery tests**

Test that the Worker:

```python
assert json.loads(generation.reference_asset_ids) == [str(video.id), str(product.id), str(person.id)]
assert publisher.publish.call_count == 3
assert json.loads(generation.request_snapshot)["ratio"] == "adaptive"
assert "secret" not in generation.request_snapshot
```

Also test an expired URL is republished, an unexpired URL is reused, a missing local file fails clearly, and `_claim()` excludes `submission_uncertain`.

- [ ] **Step 2: Run Worker recovery tests**

```powershell
python -m pytest tests/test_worker.py tests/test_generation_jobs.py -k "asset or publish or claim" -q
```

Expected: FAIL because the Worker currently reads latest assets and stored public URLs.

- [ ] **Step 3: Resolve only persisted asset IDs**

Parse `reference_asset_ids`; convert each string to `UUID`; query each `Asset`; reject missing rows, project mismatches, non-files, and unexpected first kind. Publish only expired/missing URLs via one `_publish_if_expired(asset, publisher, now)` helper. Build the image URL list from all assets after the first video.

Save the exact Seedance request after replacing URL query strings with `scheme://host/path?[redacted]`:

```python
generation.request_snapshot = json.dumps(redact_request_urls(payload), ensure_ascii=False, sort_keys=True)
session.commit()
```

- [ ] **Step 4: Restrict generation claiming states**

Change `_claim()` to this signature:

```python
def _claim(
    session,
    model,
    kinds: list[str] | None,
    worker_id: str,
    now: datetime,
    *,
    statuses: tuple[str, ...],
):
    predicates = [
        model.status.in_(statuses),
        or_(model.next_attempt_at.is_(None), model.next_attempt_at <= now),
        or_(model.leased_at.is_(None), model.leased_at <= now - timedelta(minutes=10)),
    ]
    if kinds:
        predicates.append(model.kind.in_(kinds))
    records = session.scalars(
        select(model)
        .where(*predicates)
        .order_by(model.id)
        .with_for_update(skip_locked=True)
        .limit(5)
    ).all()
    for record in records:
        record.leased_at = now
        record.leased_by = worker_id
        if record.created_at is None:
            record.created_at = now
    session.commit()
    return records
```

Job calls pass `statuses=("queued", "uploaded", "processing", "retryable")`; generation calls pass `statuses=("queued", "processing", "retryable")`. `submission_uncertain`, `failed`, and `completed` are never claimed.

- [ ] **Step 5: Run Worker tests**

```powershell
python -m pytest tests/test_worker.py tests/test_generation_jobs.py -q
```

Expected: PASS.

- [ ] **Step 6: Commit deterministic Worker inputs**

```powershell
git add backend/app/worker.py backend/app/services/generation_jobs.py backend/tests/test_worker.py backend/tests/test_generation_jobs.py
git commit -m "feat: recover generation inputs from local assets"
```

### Task 6: Download, verify and expose permanent local results

**Files:**
- Modify: `backend/app/services/generation_jobs.py`
- Modify: `backend/app/api/routes/generations.py`
- Modify: `backend/tests/test_generation_jobs.py`
- Modify: `backend/tests/test_generations_api.py`

**Interfaces:**
- Consumes: completed provider result URL.
- Produces: `generated/v{version}.mp4`, `completed_at`, and safe `FileResponse`.

- [ ] **Step 1: Write failing file-state tests**

Add these local helpers at the top of `test_generation_jobs.py`:

```python
from types import SimpleNamespace
from unittest.mock import Mock


class StreamingVideoResponse:
    headers = {"content-type": "video/mp4", "content-length": "5"}
    def __enter__(self): return self
    def __exit__(self, *_args): return False
    def raise_for_status(self): return None
    def iter_content(self, _chunk_size): yield b"video"


def fake_streaming_video_response(*_args, **_kwargs):
    return StreamingVideoResponse()
```

```python
def test_completed_requires_verified_local_file(processing_generation, tmp_path, monkeypatch) -> None:
    settings = SimpleNamespace(media_root=tmp_path)
    gateway = Mock()
    gateway.get_result.return_value = GenerationResult(
        task_id="provider-task-1", status="completed", video_url="https://provider/result.mp4"
    )
    monkeypatch.setattr("app.services.generation_jobs.Settings", lambda: settings)
    monkeypatch.setattr("app.services.generation_jobs.probe_video", lambda path: VideoMetadata(5, 720, 1280, 25))
    monkeypatch.setattr("app.services.generation_jobs.requests.get", fake_streaming_video_response)
    with SessionLocal() as session:
        generation = session.get(Generation, processing_generation.id)
        execute_generation_job(session, generation, gateway, payload={})
        assert generation.status == "completed"
        assert Path(generation.result_path).name == "v1.mp4"
        assert generation.completed_at is not None
        assert not Path(f"{generation.result_path}.part").exists()
```

Also cover: content-type mismatch; declared and streamed size >1 GiB; FFprobe error; download interruption; completed provider response without URL; final file absent from completed API response; traversal impossible because request has no path parameter.

- [ ] **Step 2: Run result tests**

```powershell
python -m pytest tests/test_generation_jobs.py tests/test_generations_api.py -k "download or content or completed" -q
```

Expected: at least completed timestamp and missing-file response tests fail.

- [ ] **Step 3: Keep remote success retryable until the local file is valid**

Normalize provider `queued`, `pending`, and `running` to local `processing`. For remote completed results, download to `.part`, stream with the 1 GiB cap, call `probe_video(temporary)`, then `temporary.replace(destination)`. Only afterward set:

```python
generation.result_path = str(destination)
generation.completed_at = datetime.now(UTC)
generation.status = "completed"
generation.next_attempt_at = None
generation.error_message = None
```

Any download/probe failure follows normal retryable backoff and keeps `result_path=None` and `completed_at=None`.

- [ ] **Step 4: Harden the content route**

Require `generation.status == "completed"`, `result_path`, and `Path(result_path).is_file()`. Resolve the path and verify it is inside `(Settings().media_root / str(project_id) / "generated").resolve()` before returning `FileResponse(media_type="video/mp4")`.

- [ ] **Step 5: Run the full backend gate**

```powershell
python -m pytest -q
```

Expected: PASS with 0 failures and, on the real workstation, 0 skips.

- [ ] **Step 6: Commit permanent result handling**

```powershell
git add backend/app/services/generation_jobs.py backend/app/api/routes/generations.py backend/tests/test_generation_jobs.py backend/tests/test_generations_api.py
git commit -m "feat: verify and store generated videos locally"
```
