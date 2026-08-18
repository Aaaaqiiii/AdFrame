# AdFlow Batched Seedance Backend Implementation Plan

> **For agentic workers:** REQUIRED SUBSKILL: Use superpowers:test-driven-development for every task, and superpowers:verification-before-completion before claiming any task complete.

**Goal:** Turn one approved full-video prompt and one immutable execution plan into an atomic batch of Seedance tasks—one task per generation segment—with deterministic relative-time prompts, recovery, retry, playback metadata, and download support.

**Architecture:** A batch is a UUID shared by ordinary `Generation` rows. The batch endpoint performs all validation and inserts all rows in one database transaction. The existing worker independently leases each row, materializes or reuses its physical reference clip, derives a provider prompt from the frozen full prompt without GPT, and uses the selected existing gateway. Existing polling/download resolution stays per generation; batch status is derived rather than stored.

**Tech Stack:** Python 3, FastAPI, SQLAlchemy, Alembic, PostgreSQL/SQLite tests, pytest, FFmpeg/FFprobe, existing Volcengine and Comfly Seedance gateways.

**Spec:** `docs/superpowers/specs/2026-08-18-full-prompt-batched-seedance-generation-design.md`

## Global constraints

- Complete the full-prompt backend plan first.
- Create migration `0006_generation_batches`; never edit `0001`–`0005`.
- One batch uses exactly one provider selected by the user. Never fail over automatically.
- One saved generation segment creates one `Generation`; never create one generation per shot.
- Validate the complete batch before inserting its first row. Any invalid segment causes the request to create zero rows.
- Require prompt mode `full_reference_video_edit`, current timeline identity, latest saved plan, confirmed shots, and confirmed product assets.
- The segment limit is 29 seconds. Do not restore the old whole-video `>30` rejection.
- Preserve `reference_asset_ids[0]` as the original video Asset ID. Never put a generation-segment ID in that list.
- Send all confirmed product reference images with every segment task.
- Keep existing `clip_video()` behavior (`-an`, `libx264`, `yuv420p`). Do not change audio or codec behavior in this plan.
- API responses must not expose `clip_path`, temporary signed publication URLs, API keys, or raw provider credentials.
- Real provider calls are prohibited in this plan; mocks/fakes only. Controlled real acceptance is in Plan 3.

## File map

| File | Responsibility |
|---|---|
| `backend/alembic/versions/0006_generation_batches.py` | Nullable batch identity/position/size columns and indexes |
| `backend/app/db/models.py` | New `Generation` batch fields |
| `backend/app/api/routes/generation_batches.py` | Atomic batch create/list/detail contracts and derived batch status |
| `backend/app/api/routes/generations.py` | Per-task response fields and batch-preserving retry behavior |
| `backend/app/main.py` | Register batch router |
| `backend/app/worker.py` | Load full prompt, derive relative prompt, materialize clip, and submit each task |
| `backend/app/services/generation_jobs.py` | Provider polling/download validation retained; only adjust snapshots if required |
| `backend/app/services/full_prompt.py` | Reuse parser/validator/deriver created in Plan 1 |
| `backend/tests/test_generation_batches_api.py` | New atomic batch, validation, grouping, and status tests |
| `backend/tests/test_generations_api.py` | Response/retry compatibility and per-task endpoint tests |
| `backend/tests/test_generation_jobs.py` | Worker payload, dual provider, recovery, and snapshot tests |
| `backend/tests/test_schema.py` | Alembic head assertion |

---

## Task 1: Persist batch identity without breaking historical generations

**Files:**

- Create: `backend/alembic/versions/0006_generation_batches.py`
- Modify: `backend/app/db/models.py`
- Modify: `backend/app/api/routes/generations.py`
- Modify: `backend/tests/test_schema.py`
- Modify: `backend/tests/test_generations_api.py`

- [ ] **Step 1: Write failing model/response tests**

Add assertions that a generation response contains nullable fields:

```python
assert payload["generation_batch_id"] is None
assert payload["batch_position"] is None
assert payload["batch_size"] is None
```

Add a schema-head assertion for `0006_generation_batches`. Run:

```powershell
python -m pytest tests/test_schema.py tests/test_generations_api.py -k "batch or alembic" -q
```

Expected: failures because the model and migration do not exist.

- [ ] **Step 2: Create migration `0006_generation_batches`**

Use:

```python
revision = "0006_generation_batches"
down_revision = "0005_segment_generations"
```

Add nullable columns to `generations`:

- `generation_batch_id`: UUID using the repository's existing portable UUID type;
- `batch_position`: integer;
- `batch_size`: integer.

Add non-unique lookup indexes on `(project_id, generation_batch_id)` and `(generation_batch_id, batch_position)`. Do not add a uniqueness constraint: a retry deliberately reuses the original batch position with a newer project-wide `Generation.version`. Enforce creation-time positions `1..batch_size` in application code.

The downgrade drops indexes before columns. Do not backfill historical rows.

- [ ] **Step 3: Update ORM and response schema**

Add nullable fields to `Generation`. Extend `GenerationResponse` and its serializer with all three fields. Old rows must serialize successfully with `null` values.

- [ ] **Step 4: Upgrade and verify migration**

```powershell
alembic upgrade head
alembic current
python -m pytest tests/test_schema.py tests/test_generations_api.py -k "batch or alembic" -q
git diff --check
git add -- alembic/versions/0006_generation_batches.py app/db/models.py app/api/routes/generations.py tests/test_schema.py tests/test_generations_api.py
git commit -m "feat: persist generation batch identity"
```

Expected Alembic head: `0006_generation_batches`.

---

## Task 2: Atomically create one task per current generation segment

**Files:**

- Create: `backend/app/api/routes/generation_batches.py`
- Create: `backend/tests/test_generation_batches_api.py`
- Modify: `backend/app/main.py`
- Modify: `backend/app/api/routes/generations.py`
- Modify: `backend/tests/test_generations_api.py`

- [ ] **Step 1: Define and test the public API contract**

Use these request/response shapes:

```python
class CreateGenerationBatchRequest(BaseModel):
    provider: Literal["volcengine", "comfly"]
    prompt_version: int = Field(ge=1)
    generate_audio: bool = False
    include_person_reference: bool = False
    include_background_reference: bool = False


class GenerationBatchResponse(BaseModel):
    generation_batch_id: UUID
    project_id: UUID
    provider: Literal["volcengine", "comfly"]
    prompt_version: int
    batch_size: int
    status: Literal["queued", "processing", "complete", "partial", "failed", "uncertain"]
    generations: list[GenerationResponse]
```

Endpoint:

```text
POST /api/projects/{project_id}/generation-batches -> 201
```

Write a happy-path test with a 50-second current timeline and two saved segments. Assert one request creates exactly two rows with:

- one shared non-null batch UUID;
- positions `[1, 2]`, size `2`;
- the selected provider on both rows;
- consecutive project generation versions;
- segment IDs matching plan order;
- prompt version matching the selected completed full prompt;
- `reference_asset_ids[0]` equal to the original video asset;
- all confirmed product image asset IDs included;
- zero provider gateway calls during HTTP creation.

- [ ] **Step 2: Write all validation and atomicity tests before implementation**

Each case must assert both the exact HTTP code/message fragment and that zero new `Generation` rows exist:

1. project missing (`404`);
2. no current timeline (`422`);
3. missing/empty plan (`422`);
4. stale plan belonging to a previous timeline (`422`);
5. plan gaps/overlaps or position gaps (`422`);
6. any segment longer than 29 seconds or any short segment not explicitly accepted (`422`);
7. any required shot edit unconfirmed (`422`);
8. prompt missing/not completed (`422`);
9. prompt mode is historical `reference_video_edit` (`422`);
10. full prompt belongs to a stale timeline (`422`);
11. full prompt structural validation fails (`422`);
12. replace-product confirmations incomplete (`422`);
13. requested people/background reference flags conflict with the frozen full-prompt settings (`422`);
14. chosen provider API key unavailable (`409`);
15. an active identical batch or active legacy task on any selected current-plan segment already exists (`409`).

The active duplicate test must not block a new batch after every prior row is terminal. The persisted terminal statuses are exactly `completed`, `failed`, and `submission_uncertain`; active statuses are exactly `queued`, `processing`, and `retryable`.

- [ ] **Step 3: Run RED tests**

```powershell
python -m pytest tests/test_generation_batches_api.py -q
```

Expected: collection or route failures because the endpoint is absent.

- [ ] **Step 4: Implement preflight as a read-only function**

Inside the new route module, define a frozen internal value object and helper:

```python
@dataclass(frozen=True)
class BatchInputs:
    project: Project
    timeline_revision: TimelineRevision
    prompt_revision: PromptRevision
    segments: tuple[GenerationSegment, ...]
    original_video_asset: Asset
    reference_assets: tuple[Asset, ...]


def _require_batch_inputs(
    session: Session,
    *,
    project_id: UUID,
    prompt_version: int,
    provider: str,
    settings: Settings,
    include_person_reference: bool,
    include_background_reference: bool,
) -> BatchInputs: ...
```

Validation order must be stable and match the specification: locked project → current timeline → all current shots confirmed → completed full prompt/mode/current timeline → full-prompt structure → current/latest immutable plan → plan coverage/duration/short-segment acceptance → product references → optional people/background selections → provider configuration → active duplicate. Use the pure full-prompt validator from Plan 1. Do not publish media or mutate rows during preflight.

- [ ] **Step 5: Insert the complete batch in one transaction**

After preflight:

- generate one `uuid4()` batch ID;
- compute `batch_size=len(segments)`;
- select the `Project` row with `with_for_update()` before reading `max(Generation.version)`, matching the existing create/retry serialization pattern, then allocate the consecutive range `max+1 .. max+batch_size`;
- create rows ordered by `GenerationSegment.position`;
- set per-row `duration=-1` and the existing adaptive ratio value expected by the gateway;
- set a deterministic submission fingerprint that includes project, current timeline revision, plan version, segment ID/bounds, full prompt revision/version, provider, generate-audio flag, and sorted reference asset IDs;
- flush all rows, serialize, and commit once;
- if any insert fails, roll back the entire request.

Do not store derived provider prompt text in `PromptRevision` and do not create child prompt revisions.

Keep the old single-generation endpoint only for historical rows and internal compatibility tests. Mark it deprecated in its FastAPI route metadata. It must continue requiring a historical `reference_video_edit` prompt bound to the same segment, and it must return `409` if that segment already has an active batch generation. Conversely, batch preflight returns `409` if any current-plan segment already has an active legacy generation. The new frontend never calls the old endpoint.

- [ ] **Step 6: Register route, run focused tests, and commit**

```powershell
python -m pytest tests/test_generation_batches_api.py tests/test_generations_api.py tests/test_projects_api.py -q
git diff --check
git add -- app/api/routes/generation_batches.py app/api/routes/generations.py app/main.py tests/test_generation_batches_api.py tests/test_generations_api.py
git commit -m "feat: create atomic Seedance generation batches"
```

---

## Task 3: Submit deterministically derived prompts through both existing providers

**Files:**

- Modify: `backend/app/worker.py`
- Modify: `backend/app/services/generation_jobs.py`
- Modify: `backend/tests/test_generation_jobs.py`
- Modify: `backend/tests/test_media.py`

- [ ] **Step 1: Write worker payload tests for a boundary-crossing batch**

Create a 40-second source with two segments `[0, 18]` and `[18, 40]`, where one shot spans `[10, 25]`. Queue a batch and run the generation worker with mocked media publication and gateway. Assert:

- exactly two provider submissions occur;
- task 1 prompt contains the crossing body under `00:10.00–00:18.00`;
- task 2 prompt contains the identical body under `00:00.00–00:07.00`;
- task 2 never contains an absolute `00:18.00` offset;
- both requests contain their own video segment URL first;
- both contain every confirmed product reference image URL;
- neither request contains a segment prompt revision;
- no GPT/chat helper is called while deriving either payload.

- [ ] **Step 2: Add provider-selection tests**

Parameterize `provider` over `volcengine` and `comfly`. Assert the worker selects only the corresponding configured gateway and model:

- Volcengine: `doubao-seedance-2-5-260628`;
- Comfly: `doubao-seedance-2.5`.

A gateway exception must follow the existing retryable/failure classification. It must never call the other provider.

- [ ] **Step 3: Add clip recovery and snapshot tests**

Retain and extend existing media regression coverage:

- a missing cached segment clip is rebuilt;
- a corrupt cached clip is deleted, rebuilt, and probed;
- a valid cached clip is reused;
- an unavailable FFmpeg/FFprobe executable raises the existing environment error and does not delete a potentially valid file;
- full-video coverage reuses/publishes the original source rather than recoding it;
- a generated subclip is validated to be at most 29 seconds before provider submission.

The redacted request snapshot must have this stable envelope:

```json
{
  "generation_batch_id": "uuid",
  "batch_position": 1,
  "batch_size": 2,
  "generation_segment_id": "uuid",
  "plan_version": 3,
  "source_start_sec": 0.0,
  "source_end_sec": 18.0,
  "full_prompt_revision_id": "uuid",
  "prompt_version": 4,
  "provider_request": {
    "text": "derived relative-time prompt",
    "ratio": "adaptive"
  }
}
```

Signed URLs may be sent to the provider but must be redacted or omitted from the persisted snapshot according to the existing redaction helper.

- [ ] **Step 4: Run RED tests**

```powershell
python -m pytest tests/test_generation_jobs.py tests/test_media.py -k "batch or derived or crossing or segment_clip" -q
```

Expected: the worker currently sends the full absolute prompt unchanged and lacks batch snapshot fields.

- [ ] **Step 5: Implement the worker branch**

Before provider submission, when `generation.generation_batch_id` is non-null:

1. Load the exact `GenerationSegment` and exact `PromptRevision` identified by the row's frozen IDs/version.
2. Require `prompt_mode="full_reference_video_edit"`, null prompt segment identity, and matching project/timeline.
3. Parse and revalidate the frozen full prompt.
4. Derive the relative prompt with `derive_segment_prompt()` and the row's batch position/size.
5. Use the existing `ensure_segment_clip()` path and publication renewal logic.
6. Resolve all frozen reference assets. The physical segment video is request item zero; all confirmed product images follow.
7. Call the existing `build_seedance_request()` and selected gateway with the derived text.
8. Persist the redacted snapshot before/with external task identity using the existing crash-recovery semantics.

Keep the existing legacy branch for historical non-batch rows. Do not look up “latest” prompt/plan data during worker execution; frozen identities are authoritative.

- [ ] **Step 6: Run focused tests and commit**

```powershell
python -m pytest tests/test_generation_jobs.py tests/test_media.py tests/test_full_prompt.py -q
git diff --check
git add -- app/worker.py app/services/generation_jobs.py tests/test_generation_jobs.py tests/test_media.py
git commit -m "feat: submit derived segment prompts to Seedance"
```

---

## Task 4: Expose batch recovery/status and preserve identity on retry

**Files:**

- Modify: `backend/app/api/routes/generation_batches.py`
- Modify: `backend/app/api/routes/generations.py`
- Modify: `backend/tests/test_generation_batches_api.py`
- Modify: `backend/tests/test_generations_api.py`

- [ ] **Step 1: Implement list and detail endpoints with tests**

Add:

```text
GET /api/projects/{project_id}/generation-batches
GET /api/projects/{project_id}/generation-batches/{batch_id}
```

List newest batches first and generations in ascending `batch_position`, choosing the newest `Generation.version` for a position when that segment was retried. Old non-batch rows are excluded. Detail returns `404` if the batch does not belong to the project.

Derive status from current rows using this priority:

1. `uncertain` if any position is `submission_uncertain`;
2. `queued` if every position is `queued`;
3. `processing` if any position is `queued`, `processing`, or `retryable` after the all-queued case;
4. `complete` if every position is `completed`;
5. `partial` if at least one position is `completed` and at least one is `failed`;
6. `failed` if every position is `failed`.

Map the repository's actual persisted generation statuses into these response statuses in one pure helper and unit-test every combination.

- [ ] **Step 2: Make retry preserve the batch slot**

For a failed/needs-resolution batch generation, the existing retry endpoint must create a new `Generation` version with the same:

- `generation_batch_id`;
- `batch_position` and `batch_size`;
- segment ID;
- prompt version;
- provider and generation settings;
- frozen reference asset IDs.

Before retry, rerun the current segment/full-prompt validation already required by the API. A stale timeline, stale plan, invalid full prompt, missing product asset, or over-limit segment returns `422` and creates no row. A retry never recreates successful sibling positions.

- [ ] **Step 3: Verify result content/download remains per task**

Use existing generation endpoints and tests to prove:

- `completed` rows return playable content through the application-owned content endpoint;
- extend `GET /api/projects/{project_id}/generations/{generation_id}/content` with `download: bool = Query(False)`: return inline video without a filename disposition when false, and an attachment with a safe filename containing batch position when true;
- expired provider URLs are not required after local result persistence;
- failed and uncertain rows do not claim playable content;
- resolving an uncertain submission does not alter sibling tasks.

- [ ] **Step 4: Run complete backend gates**

```powershell
python -m pytest tests/test_generation_batches_api.py tests/test_generations_api.py tests/test_generation_jobs.py tests/test_media.py tests/test_projects_api.py tests/test_schema.py -q
python -m pytest -q
alembic current
git diff --check
```

Expected: zero failures and Alembic head `0006_generation_batches`. Media tests must execute with FFmpeg/FFprobe available; do not count skips as equivalent evidence.

- [ ] **Step 5: Commit and self-review**

```powershell
git add -- app/api/routes/generation_batches.py app/api/routes/generations.py tests/test_generation_batches_api.py tests/test_generations_api.py
git commit -m "feat: recover and retry batched generation results"
git status --short
```

Review the complete diff against the specification. Search for any batch path that sends `prompt.text` directly without `derive_segment_prompt`, any automatic provider fallback, and any API response field containing `clip_path` or publication URL. Fix and retest before handoff.

## Plan 2 handoff

Record migration head, commit IDs, focused totals, and full-backend total. Then proceed to `2026-08-18-adflow-batched-generation-frontend-acceptance.md`. Do not spend external provider quota during this handoff.
