# Reference Video Segmentation and Seedance Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Upgrade AdFlow from whole-video descriptive prompts to editable per-segment reference-video instructions, automatically or manually split long videos, and submit one recoverable Seedance task per generation segment.

**Architecture:** Keep the current React → FastAPI → PostgreSQL → database Worker flow. Add an immutable generation-segment plan tied to a timeline revision, reuse the current FFmpeg scene boundaries and `clip_video()` path, attach prompt revisions and generations to one segment, and extend the existing Seedance request/lease/result machinery rather than creating a parallel pipeline.

**Tech Stack:** Python 3.11, FastAPI, SQLAlchemy 2, Alembic, PostgreSQL/SQLite tests, FFmpeg/FFprobe, React 19, TypeScript, Vite, Vitest.

## Global Constraints

- A generation segment contains one or more consecutive shots; it is not one task per shot.
- Effective segment limit is `30 - 1 = 29` seconds; recommended minimum is 8 seconds.
- Prefer shot boundaries; use an inside-shot boundary only when explicitly selected or no legal shot-boundary plan exists.
- Videos shorter than 8 seconds are valid as one segment without extra acceptance.
- Keep the current FFmpeg scene detection, boundary snapping, and `clip_video()` strategy.
- Segmented reference clips keep the current `-an -c:v libx264 -pix_fmt yuv420p` behavior.
- New prompts use `reference_video_edit` and fixed `保持 / 修改 / 删除 / 禁止` sections.
- Do not automatically concatenate generated results in this version.
- Do not add a queue, state library, UI library, or new third-party dependency.
- Existing projects and historical prompt/generation rows remain readable.
- Never execute a real Seedance generation without explicit user approval to spend quota.

## Execution Protocol for a Low-Context Implementer

For every task, follow this sequence exactly:

1. Read every file listed under that task before editing any of them.
2. Run the stated RED test and save the failure text in the task report; do not guess that it fails.
3. Make only the production changes named in that task.
4. Run the stated GREEN test. If another test fails, determine whether the new contract intentionally changed it before editing the test.
5. Run `git diff --check` and inspect `git diff --stat` plus the complete diff.
6. Commit only the task files. Never use `git add .` because `.claude/` and `.codex-review/` are unrelated untracked directories.
7. Stop after the commit and report: commit hash, exact test command/count, changed files, and unresolved concern.

Do not combine tasks. Do not modify an already-applied Alembic migration. Tasks 3, 4, and 5 deliberately use separate `0003`, `0004`, and `0005` revisions so each task remains independently deployable and reviewable.

---

## File Structure

**Create:**

- `backend/app/services/generation_segments.py` — pure segment planning, validation, shot intersection, and relative-time conversion.
- `backend/app/api/routes/generation_segments.py` — current-plan read, automatic-plan creation, and manual-plan creation.
- `backend/alembic/versions/0003_reference_video_segments.py` — additive segment-plan schema and persistent clip/publication metadata.
- `backend/alembic/versions/0004_segment_edit_prompts.py` — prompt mode and segment linkage.
- `backend/alembic/versions/0005_segment_generations.py` — generation-to-segment linkage.
- `backend/tests/test_generation_segments.py` — pure planner and validation tests.
- `backend/tests/test_generation_segments_api.py` — persistence, versioning, stale-plan, and manual-edit API tests.
- `frontend/src/generationSegments.ts` — pure UI validation and display helpers.
- `frontend/src/generationSegments.test.ts` — Vitest coverage for segment state.
- `frontend/src/components/GenerationSegmentsEditor.tsx` — automatic/manual segment editing inside the existing workflow.
- `frontend/src/components/GenerationStage.tsx` — per-segment Seedance submission, status, playback, and download.

**Modify:**

- `backend/app/core/config.py` — three segment duration settings.
- `backend/app/db/models.py` — `GenerationSegment`, prompt mode/segment FK, generation segment FK.
- `backend/app/main.py` — register the segment router.
- `backend/app/api/routes/projects.py` — segment-aware prompt requests/responses and prompt validation.
- `backend/app/api/routes/generations.py` — require a legal current segment and matching edit prompt.
- `backend/app/services/final_prompt.py` — fixed delta-instruction prompt generation and split-shot clipping.
- `backend/app/services/media.py` — reuse `clip_video()` for persistent segment output without changing its codec/audio behavior.
- `backend/app/worker.py` — publish the correct segment clip and build one request per segment.
- `backend/app/services/generation_jobs.py` — validate downloaded result video and required audio stream.
- `frontend/src/api.ts` — segment, prompt, generation, history, retry, and resolve contracts.
- `frontend/src/App.tsx` — restore segment plans and mount segment-aware prompt/generation UI.
- `frontend/src/workspaceDomain.ts` — add `generation` stage and unlocking rules.
- `frontend/src/components/WorkflowRail.tsx` — add generation/results stage.
- `frontend/src/components/PromptStage.tsx` — select segment and edit delta instructions.
- `frontend/src/App.css` — styles within the current visual system.
- Existing backend/frontend tests named in each task — update old whole-video assumptions only where the new contract deliberately replaces them.

---

### Task 1: Integrate the Existing Product Image Label Feature

**Files:**
- Existing commits: `c77d335`, `c8b90f9`
- Verify: `backend/tests/test_page_two_flow.py`
- Verify: `frontend/src/productProfile.test.ts`

**Interfaces:**
- Consumes: existing `Asset.profile_json` and product image list API.
- Produces: editable `view_label` and `note` for every product reference image; later prompt tasks consume these fields.

- [ ] **Step 1: Verify the target branch and commits**

Run:

```powershell
git status --short --branch
git show --stat --oneline c77d335
git show --stat --oneline c8b90f9
```

Expected: current branch contains no tracked changes; both commits exist and only implement product-image naming/editing.

- [ ] **Step 2: Integrate the existing commits without reimplementation**

Run:

```powershell
git cherry-pick c77d335 c8b90f9
```

Expected: both commits apply in order. Resolve only genuine overlap with the already committed design documents; do not rewrite the feature.

- [ ] **Step 3: Run the feature gates**

Run:

```powershell
Set-Location backend
python -m pytest tests/test_page_two_flow.py -q
Set-Location ..\frontend
npm.cmd test -- productProfile.test.ts
npm.cmd run lint
npm.cmd run build
```

Expected: all commands pass.

- [ ] **Step 4: Record the integration state**

Run:

```powershell
git status --short
git log -4 --oneline
```

Expected: no tracked changes; the two feature commits are now ancestors of the implementation branch. No additional commit is needed for a clean cherry-pick.

---

### Task 2: Add the Pure Segment Planner

**Files:**
- Create: `backend/app/services/generation_segments.py`
- Create: `backend/tests/test_generation_segments.py`
- Modify: `backend/app/core/config.py`

**Interfaces:**
- Produces: `SegmentDraft`, `plan_segments()`, `validate_segments()`, and `shot_slices_for_segment()`.
- Consumes: only numeric video duration and ordered `(shot_id, start_sec, end_sec)` values; no database session.

- [ ] **Step 1: Write planner RED tests**

Create tests covering these exact cases:

```python
def test_short_video_stays_one_segment():
    assert plan_segments(5.0, [("a", 0.0, 5.0)], 29.0, 8.0) == [
        SegmentDraft(0.0, 5.0, "video_edge", "video_edge", False)
    ]

def test_32_second_video_prefers_balanced_shot_boundary():
    shots = [("a", 0.0, 8.0), ("b", 8.0, 16.0), ("c", 16.0, 24.0), ("d", 24.0, 32.0)]
    assert [(s.start_sec, s.end_sec) for s in plan_segments(32.0, shots, 29.0, 8.0)] == [(0.0, 16.0), (16.0, 32.0)]

def test_75_second_video_uses_n_legal_segments():
    segments = plan_segments(75.0, [(str(i), i * 5.0, (i + 1) * 5.0) for i in range(15)], 29.0, 8.0)
    assert len(segments) == 3
    assert max(s.end_sec - s.start_sec for s in segments) <= 29.0

def test_single_long_shot_falls_back_to_inside_shot_boundaries():
    segments = plan_segments(61.0, [("a", 0.0, 61.0)], 29.0, 8.0)
    assert len(segments) == 3
    assert segments[0].end_boundary_type == "inside_shot"

def test_validation_rejects_gap_overlap_and_over_limit():
    with pytest.raises(SegmentValidationError, match="空缺或重叠"):
        validate_segments(32.0, [SegmentDraft(0, 15, "video_edge", "shot_boundary", False), SegmentDraft(16, 32, "shot_boundary", "video_edge", False)], 29.0, 8.0)
```

- [ ] **Step 2: Run RED**

Run:

```powershell
Set-Location backend
python -m pytest tests/test_generation_segments.py -q
```

Expected: collection fails because `generation_segments` does not exist.

- [ ] **Step 3: Implement the smallest pure planner**

Add:

```python
@dataclass(frozen=True)
class SegmentDraft:
    start_sec: float
    end_sec: float
    start_boundary_type: str
    end_boundary_type: str
    short_segment_accepted: bool = False

def plan_segments(duration_sec: float, shots: list[tuple[str, float, float]], max_sec: float, min_sec: float) -> list[SegmentDraft]: ...
def validate_segments(duration_sec: float, segments: list[SegmentDraft], max_sec: float, min_sec: float) -> None: ...
def shot_slices_for_segment(segment: SegmentDraft, shots: list[tuple[str, float, float]]) -> list[dict[str, float | str]]: ...
```

Implement boundary planning with this deterministic algorithm; do not invent a heuristic:

```text
candidate boundaries = sorted unique [0, every shot.end_sec, duration]
minimum segment count = ceil(duration / max_sec)

for count from minimum segment count through candidate_boundary_count - 1:
    use dynamic programming to find exactly count consecutive segments
    a transition previous_boundary -> boundary is legal when:
        duration <= max_sec
        duration >= min_sec, except the whole source video may be shorter than min_sec
    transition cost = (segment_duration - source_duration / count) ** 2
    retain the predecessor with the lowest cumulative cost
    if a complete path reaches source_duration:
        return that path, marking internal boundaries shot_boundary

if no all-shot-boundary path exists:
    use minimum segment count equal-width ranges
    mark non-edge boundaries inside_shot
```

Use a small epsilon of `0.001` seconds for equality/coverage comparisons. Call `validate_segments()` on the final result before returning. `shot_slices_for_segment` returns only positive intersections and includes `shot_id`, `source_start_sec`, `source_end_sec`, `relative_start_sec = source_start_sec - segment.start_sec`, and `relative_end_sec = source_end_sec - segment.start_sec`.

Add settings:

```python
provider_max_reference_seconds: float = 30.0
segment_safety_margin_seconds: float = 1.0
recommended_min_segment_seconds: float = 8.0

@property
def effective_segment_limit_seconds(self) -> float:
    return self.provider_max_reference_seconds - self.segment_safety_margin_seconds
```

- [ ] **Step 4: Run GREEN**

Run:

```powershell
python -m pytest tests/test_generation_segments.py -q
```

Expected: all planner tests pass.

- [ ] **Step 5: Commit**

```powershell
git add backend/app/core/config.py backend/app/services/generation_segments.py backend/tests/test_generation_segments.py
git commit -m "feat: plan generation segments at shot boundaries"
```

---

### Task 3: Persist Immutable Segment Plans and Expose Their API

**Files:**
- Create: `backend/alembic/versions/0003_reference_video_segments.py`
- Modify: `backend/app/db/models.py`
- Create: `backend/app/api/routes/generation_segments.py`
- Modify: `backend/app/main.py`
- Create: `backend/tests/test_generation_segments_api.py`
- Modify: `backend/tests/test_migrations.py`

**Interfaces:**
- Produces: `GET /api/projects/{id}/generation-segments`, `POST .../generation-segments/auto`, `PUT .../generation-segments`.
- Produces model: `GenerationSegment(id, project_id, plan_version, position, source_start_sec, source_end_sec, start_boundary_type, end_boundary_type, source_timeline_revision_id, short_segment_accepted, clip_path, public_url, public_url_expires_at, created_at)`.

- [ ] **Step 1: Write API and migration RED tests**

Tests must assert:

```python
auto = client.post(f"/api/projects/{project_id}/generation-segments/auto")
assert auto.status_code == 201
assert [(x["source_start_sec"], x["source_end_sec"]) for x in auto.json()["segments"]] == [(0.0, 16.0), (16.0, 32.0)]

manual = client.put(f"/api/projects/{project_id}/generation-segments", json={
    "segments": [
        {"source_start_sec": 0, "source_end_sec": 12, "start_boundary_type": "video_edge", "end_boundary_type": "inside_shot", "short_segment_accepted": False},
        {"source_start_sec": 12, "source_end_sec": 32, "start_boundary_type": "inside_shot", "end_boundary_type": "video_edge", "short_segment_accepted": False},
    ]
})
assert manual.status_code == 201
assert manual.json()["plan_version"] == 2
assert client.get(f"/api/projects/{project_id}/generation-segments").json()["plan_version"] == 2
```

Also assert: missing timeline is 422; unconfirmed shots are 422; stale timeline plans remain queryable only by history code and are not returned as current; short split-created segments require `short_segment_accepted=true`; gaps/overlaps/over-limit are 422.

- [ ] **Step 2: Run RED**

Run:

```powershell
Set-Location backend
python -m pytest tests/test_generation_segments_api.py tests/test_migrations.py -q
```

Expected: failures for missing table, model, and routes.

- [ ] **Step 3: Add the model and additive migration**

Create `0003_reference_video_segments` with:

```python
op.create_table(
    "generation_segments",
    sa.Column("id", sa.Uuid(), primary_key=True),
    sa.Column("project_id", sa.Uuid(), sa.ForeignKey("projects.id"), nullable=False),
    sa.Column("plan_version", sa.Integer(), nullable=False),
    sa.Column("position", sa.Integer(), nullable=False),
    sa.Column("source_start_sec", sa.Float(), nullable=False),
    sa.Column("source_end_sec", sa.Float(), nullable=False),
    sa.Column("start_boundary_type", sa.String(20), nullable=False),
    sa.Column("end_boundary_type", sa.String(20), nullable=False),
    sa.Column("source_timeline_revision_id", sa.Uuid(), sa.ForeignKey("timeline_revisions.id"), nullable=False),
    sa.Column("short_segment_accepted", sa.Boolean(), nullable=False, server_default=sa.false()),
    sa.Column("clip_path", sa.String(500), nullable=True),
    sa.Column("public_url", sa.String(1000), nullable=True),
    sa.Column("public_url_expires_at", sa.String(40), nullable=True),
    sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    sa.UniqueConstraint("project_id", "plan_version", "position"),
)
op.create_index("ix_generation_segments_project_plan", "generation_segments", ["project_id", "plan_version"])
```

Do not modify or backfill historical rows in this step.

`clip_path`, `public_url`, and `public_url_expires_at` are mutable transport metadata; the segment bounds, position, plan version, boundary types, and timeline revision are immutable. A new manual plan creates new rows with empty transport metadata.

- [ ] **Step 4: Implement the three endpoints**

Use Pydantic models with `extra="forbid"`. Lock the project row before computing the next `plan_version`. Both creation endpoints validate current timeline, all-shot confirmation, full coverage, and duration. Return:

```json
{
  "plan_version": 2,
  "timeline_revision_id": "uuid",
  "max_segment_seconds": 29,
  "recommended_min_seconds": 8,
  "segments": []
}
```

Register the router in `main.py`.

When the project has a current timeline but no saved segment plan, `GET` returns HTTP 200 with `plan_version: 0`, the current `timeline_revision_id`, configured limits, and `segments: []`. Do not use 404 for this normal empty state.

Use these exact status codes/messages so the frontend and tests do not guess:

- `404 "项目不存在"`
- `422 "请先确认时间轴"`
- `422 "时间轴存在未确认镜头，请先确认全部镜头"`
- `422 "生成片段不能有空缺或重叠"`
- `422 "生成片段超过 29 秒安全上限"` (format the configured value, do not literally hardcode 29)
- `422 "短片段需要明确确认"`

- [ ] **Step 5: Run GREEN and migration checks**

Run:

```powershell
python -m pytest tests/test_generation_segments.py tests/test_generation_segments_api.py tests/test_migrations.py -q
python -m alembic -c alembic.ini upgrade head
python -m alembic -c alembic.ini current
```

Expected: tests pass and current revision is `0003_reference_video_segments`.

- [ ] **Step 6: Commit**

```powershell
git add backend/alembic/versions/0003_reference_video_segments.py backend/app/db/models.py backend/app/api/routes/generation_segments.py backend/app/main.py backend/tests/test_generation_segments_api.py backend/tests/test_migrations.py
git commit -m "feat: persist immutable generation segment plans"
```

---

### Task 4: Generate Segment-Scoped Delta Prompts

**Files:**
- Modify: `backend/app/db/models.py`
- Create: `backend/alembic/versions/0004_segment_edit_prompts.py`
- Modify: `backend/app/api/routes/projects.py`
- Modify: `backend/app/services/final_prompt.py`
- Modify: `backend/tests/test_projects_api.py`
- Modify: `backend/tests/test_simplified_prompt_workflow.py`
- Modify: `backend/tests/test_dual_product_workflows.py`

**Interfaces:**
- `PromptRevision.prompt_mode: str`, default legacy rows to `full_video_description`.
- `PromptRevision.generation_segment_id: UUID | None`.
- `POST /api/projects/{id}/prompts` accepts required `generation_segment_id` for `use_ai=true` in the new workflow.
- Prompt list accepts optional `generation_segment_id` and returns `prompt_mode` plus `generation_segment_id`.

Use these exact request/response additions:

```python
class CreatePromptRequest(BaseModel):
    # keep all existing fields
    generation_segment_id: UUID | None = None

class PromptRevisionSummary(BaseModel):
    # keep all existing fields
    prompt_mode: str
    generation_segment_id: UUID | None
```

The existing `Job.provider_input_id` continues storing `str(prompt_revision.id)`. Do not add a segment column to `Job`: the Worker loads `PromptRevision` by that ID, then follows `PromptRevision.generation_segment_id`.

- [ ] **Step 1: Write prompt contract RED tests**

Assert that a queued segment prompt stores `prompt_mode="reference_video_edit"` and its segment ID, then Worker output includes exactly one block per intersecting shot with all four labels. Add a long-shot case where a 20–35 second shot is split at 29 seconds: the first prompt gets the 20–29 intersection and the second gets 0–6 relative seconds.

Also assert:

```python
assert "保持：" in revision.text
assert "修改：" in revision.text
assert "删除：" in revision.text
assert "禁止：" in revision.text
assert "完整描述原视频" not in revision.text
```

Reject segment/timeline mismatch, stale plan, unconfirmed shots, and attempts to use a legacy prompt for a new segment generation.

- [ ] **Step 2: Run RED**

Run:

```powershell
Set-Location backend
python -m pytest tests/test_projects_api.py tests/test_simplified_prompt_workflow.py tests/test_dual_product_workflows.py -k "segment or reference_video_edit or delta_prompt or split_shot_prompt" -q
```

Expected: failures because prompt mode and segment linkage do not exist.

- [ ] **Step 3: Add prompt schema columns**

Create a new migration with `down_revision = "0003_reference_video_segments"`:

```python
op.add_column("prompt_revisions", sa.Column("prompt_mode", sa.String(30), nullable=False, server_default="full_video_description"))
op.add_column("prompt_revisions", sa.Column("generation_segment_id", sa.Uuid(), sa.ForeignKey("generation_segments.id"), nullable=True))
op.create_index("ix_prompt_revisions_segment", "prompt_revisions", ["generation_segment_id"])
```

Its downgrade drops the index, foreign-key column, then `prompt_mode`. Add matching SQLAlchemy fields. Keep old rows valid and readable.

- [ ] **Step 4: Implement fixed prompt construction**

Change `generate_final_prompt()` to receive the selected segment and shot intersections. The system message must require this exact structure:

```text
全局规则：原参考视频决定人物、动作、场景、构图、运镜、节奏和镜头顺序。只执行明确修改。

00:00.00–00:03.20
保持：...
修改：...
删除：...
禁止：...
```

Server-side validation must parse every expected relative time label and all four headings. If one block is missing, perform the existing targeted repair once; otherwise fail the revision instead of marking incomplete text `completed`.

Keep prompt validation deterministic:

```python
required_labels = [_time_label(slice["relative_start_sec"], slice["relative_end_sec"]) for slice in shot_slices]
for label in required_labels:
    block = parsed_blocks.get(label)
    if block is None or any(heading not in block for heading in ("保持：", "修改：", "删除：", "禁止：")):
        missing.append(label)
```

Do not validate with a single global substring search; each time block needs all four headings.

For `replace_product`, deterministically append the confirmed product profile and named image purposes to global modification constraints. For preserve mode, deterministically forbid product replacement.

- [ ] **Step 5: Keep snapshot-safe refinement**

Require a selected completed `reference_video_edit` prompt from the same segment. Preserve the existing frozen `revision.text` source behavior. Reject refinement that removes a required time block or heading.

- [ ] **Step 6: Run GREEN and regression modules**

Run:

```powershell
python -m pytest tests/test_projects_api.py tests/test_simplified_prompt_workflow.py tests/test_dual_product_workflows.py tests/test_generation_segments_api.py -q
```

Expected: all pass.

- [ ] **Step 7: Commit**

```powershell
git add backend/app/db/models.py backend/alembic/versions/0004_segment_edit_prompts.py backend/app/api/routes/projects.py backend/app/services/final_prompt.py backend/tests/test_projects_api.py backend/tests/test_simplified_prompt_workflow.py backend/tests/test_dual_product_workflows.py
git commit -m "feat: generate segment-scoped video edit prompts"
```

---

### Task 5: Prepare Persistent Segment Clips and Submit One Task Per Segment

**Files:**
- Modify: `backend/app/db/models.py`
- Create: `backend/alembic/versions/0005_segment_generations.py`
- Modify: `backend/app/api/routes/generations.py`
- Modify: `backend/app/services/media.py`
- Modify: `backend/app/worker.py`
- Modify: `backend/app/services/generation_jobs.py`
- Modify: `backend/tests/test_media.py`
- Modify: `backend/tests/test_projects_api.py`
- Modify: `backend/tests/test_generation_jobs.py`

**Interfaces:**
- `Generation.generation_segment_id: UUID | None`.
- `CreateGenerationRequest` adds required `generation_segment_id`.
- `ensure_segment_clip(source, destination, start_sec, end_sec, max_sec) -> Path` reuses `clip_video()` and verifies output.
- Existing `build_seedance_request()` remains the provider payload builder.

Keep `reference_asset_ids` backward compatible: element 0 remains the original reference-video `Asset.id`; elements 1..N remain selected image `Asset.id` values. The Worker resolves those IDs exactly as today, but replaces the published video URL with the segment clip URL when the selected segment does not cover the entire original video. Do not put a `GenerationSegment.id` into `reference_asset_ids`.

- [ ] **Step 1: Write generation RED tests**

Cover:

```python
response = client.post(f"/api/projects/{project_id}/generations", json={
    "provider": "comfly",
    "prompt_version": prompt_version,
    "generation_segment_id": str(segment_id),
    "generate_audio": False,
    "include_person_reference": False,
    "include_background_reference": False,
})
assert response.status_code == 202
assert response.json()["generation_segment_id"] == str(segment_id)
```

Assert: a 32-second original video is accepted when the chosen segment is legal; prompt/segment mismatch is 422; stale segment is 422; an actual segment over effective limit is 422; repeated active input is 409; two segments create exactly two generations, never one per shot.

Worker tests must assert that the published video asset is the persistent segment clip, request text is the matching prompt, reference images preserve their IDs/order, and request snapshot contains no signed query strings.

- [ ] **Step 2: Run RED**

Run:

```powershell
Set-Location backend
python -m pytest tests/test_media.py tests/test_projects_api.py tests/test_generation_jobs.py -k "segment or long_reference or one_task_per_segment" -q
```

Expected: old whole-video rejection and missing segment linkage fail.

- [ ] **Step 3: Add generation linkage**

Create a new migration with `down_revision = "0004_segment_edit_prompts"`:

```python
op.add_column("generations", sa.Column("generation_segment_id", sa.Uuid(), sa.ForeignKey("generation_segments.id"), nullable=True))
op.create_index("ix_generations_segment", "generations", ["generation_segment_id"])
```

Its downgrade drops the index and column. Add the ORM and response field. Historical generations remain nullable.

- [ ] **Step 4: Reuse current clipping behavior for persistent files**

Add a thin wrapper, not a second encoder:

```python
def ensure_segment_clip(source: Path, destination: Path, start_sec: float, end_sec: float, max_sec: float) -> Path:
    if destination.is_file():
        metadata = probe_video(destination)
    else:
        metadata = probe_video(clip_video(source, destination, start_sec, end_sec))
    if metadata.duration_sec > max_sec:
        destination.unlink(missing_ok=True)
        raise ValueError("生成片段超过供应商安全时长")
    return destination
```

Store clips under `<project media>/generation-segments/plan-<version>/segment-<position>.mp4` and persist that absolute path in `GenerationSegment.clip_path`. Do not alter `clip_video()` audio or codec flags.

- [ ] **Step 5: Replace whole-video validation with segment validation**

In `create_generation()`, remove `video.duration_sec > 30`. Validate current segment, matching current timeline, matching `reference_video_edit` prompt, and FFprobe-safe effective length. Include `generation_segment_id`, segment bounds, and plan version in the submission fingerprint.

Validation order must be stable:

1. project exists;
2. segment exists and belongs to project;
3. segment timeline equals current timeline and segment plan version is the latest current plan;
4. prompt exists, is `completed`, is `reference_video_edit`, belongs to the same segment, and uses current timeline;
5. all current shots are confirmed;
6. provider key and mode-specific references are valid;
7. no matching active fingerprint exists.

Return `422 "编辑指令与生成片段不匹配"` for prompt/segment mismatch and `422 "生成分段已过期，请重新确认分段"` for stale plan/timeline.

- [ ] **Step 6: Publish and submit the segment clip in Worker**

Before building the request, create/reuse the persistent clip. Do not wrap it in an `Asset`. Add a segment-specific helper beside `_publish_if_expired`:

```python
def _publish_segment_if_expired(session, segment: GenerationSegment) -> str:
    expires_at = datetime.fromisoformat(segment.public_url_expires_at) if segment.public_url_expires_at else None
    if segment.public_url and (expires_at is None or expires_at > datetime.now(UTC)):
        return segment.public_url
    published = TempfilePublisher().publish(Path(segment.clip_path), "video/mp4")
    segment.public_url = published.url
    segment.public_url_expires_at = published.expires_at.isoformat()
    session.commit()
    return published.url
```

For a one-segment source at `0..video.duration_sec`, continue publishing the original `Asset`; for a physically clipped segment publish `GenerationSegment.clip_path`. Continue sending:

```python
build_seedance_request(model, prompt.text, segment_public_url, "adaptive", -1, generation.generate_audio, image_urls)
```

Do not create child jobs per shot. Save the generation segment and prompt linkage in the redacted request snapshot.

Worker flow must be exactly:

```text
load Generation
load PromptRevision by project_id + prompt_version
load GenerationSegment by generation_segment_id
resolve original video + image Assets from reference_asset_ids
if segment covers complete source: publish original video Asset
else: ensure persistent clip, persist clip_path, publish segment
publish image Assets in stored order
build and persist redacted request snapshot
call existing execute_generation_job
release existing lease
```

If prompt or segment linkage is missing, mark the task `failed`; if clipping, publication, provider transport, polling, or result download raises, use the existing retry policy.

- [ ] **Step 7: Validate result media**

Extend probing only as far as required: valid video stream, positive duration/width/height/fps; when `generate_audio` is true, inspect FFprobe stream types and require one audio stream. Invalid downloads stay `retryable` under the existing policy.

Extend `VideoMetadata` with `has_audio: bool`. In `probe_video()`, request all stream `codec_type` values, select the first video stream as today, and set `has_audio = any(stream["codec_type"] == "audio" for stream in payload["streams"])`. Update direct `VideoMetadata(...)` constructions in tests. Do not add codec-specific quality rules.

- [ ] **Step 8: Run GREEN and Worker regression**

Run:

```powershell
python -m pytest tests/test_media.py tests/test_projects_api.py tests/test_generation_jobs.py tests/test_generation_segments_api.py -q
```

Expected: all pass.

- [ ] **Step 9: Commit**

```powershell
git add backend/app/db/models.py backend/alembic/versions/0005_segment_generations.py backend/app/api/routes/generations.py backend/app/services/media.py backend/app/worker.py backend/app/services/generation_jobs.py backend/tests/test_media.py backend/tests/test_projects_api.py backend/tests/test_generation_jobs.py
git commit -m "feat: submit one Seedance task per video segment"
```

---

### Task 6: Add Frontend Segment Planning and Editing

**Files:**
- Create: `frontend/src/generationSegments.ts`
- Create: `frontend/src/generationSegments.test.ts`
- Create: `frontend/src/components/GenerationSegmentsEditor.tsx`
- Modify: `frontend/src/api.ts`
- Modify: `frontend/src/App.tsx`
- Modify: `frontend/src/App.css`

**Interfaces:**
- Produces TypeScript types `GenerationSegment`, `GenerationSegmentPlan`, `GenerationSegmentInput`.
- Produces pure helpers `segmentDuration`, `segmentIssue`, `canSaveSegmentPlan`, `nearestShotBoundary`.
- Component callbacks: `onAutoPlan()`, `onSave(inputs)`, `onRestoreAuto()`.

- [ ] **Step 1: Write frontend RED tests**

```typescript
it('rejects gaps and segments over the server limit', () => {
  expect(canSaveSegmentPlan([{ source_start_sec: 0, source_end_sec: 30 }], 30, 29, 8)).toBe(false)
})

it('accepts an original five second video as one segment', () => {
  expect(segmentIssue({ source_start_sec: 0, source_end_sec: 5, short_segment_accepted: false }, 5, 29, 8)).toBeNull()
})

it('snaps to the nearest shot boundary', () => {
  expect(nearestShotBoundary(15.7, [0, 8, 16, 24, 32])).toBe(16)
})
```

- [ ] **Step 2: Run RED**

Run:

```powershell
Set-Location frontend
npm.cmd test -- generationSegments.test.ts
```

Expected: module not found.

- [ ] **Step 3: Add API types and pure helpers**

Add:

```typescript
export type GenerationSegment = {
  id: string; position: number; source_start_sec: number; source_end_sec: number
  start_boundary_type: 'video_edge' | 'shot_boundary' | 'inside_shot'
  end_boundary_type: 'video_edge' | 'shot_boundary' | 'inside_shot'
  short_segment_accepted: boolean
}
export type GenerationSegmentPlan = {
  plan_version: number; timeline_revision_id: string
  max_segment_seconds: number; recommended_min_seconds: number
  segments: GenerationSegment[]
}
```

Add GET, automatic POST, and manual PUT functions. Keep all validation pure in `generationSegments.ts`.

- [ ] **Step 4: Build the editor inside the existing layout**

Render one compact row/card per segment with start, end, duration, shot range, boundary badge, and validation message. Provide “自动规划”, “增加切点”, “删除切点”, “恢复自动方案”, and “保存分段”. Default adjustments snap to shot boundaries; an explicit “允许镜头内部切分” action unlocks exact time input.

Do not introduce drag-and-drop. Numeric inputs and existing buttons are sufficient and testable.

- [ ] **Step 5: Restore and invalidate correctly in App**

On project restore, GET the current plan after timeline data. Clear the displayed current plan when timeline revision changes until a new plan is loaded/created. Do not delete historical backend records.

Use this restore order and do not issue requests in a race:

```text
GET project details
set project + timeline
GET current generation-segment plan
if a plan exists: select its first segment
GET completed prompts filtered by selected segment
GET generation history
start generation polling only when an active generation exists
```

A missing current plan is the Task 3 HTTP 200 response with `plan_version: 0` and `segments: []`; render the automatic-planning empty state instead of a page-level error.

- [ ] **Step 6: Run GREEN and frontend gates**

Run:

```powershell
npm.cmd test -- generationSegments.test.ts timelineEditing.test.ts workspaceDomain.test.ts
npm.cmd run lint
npm.cmd run build
```

Expected: all pass.

- [ ] **Step 7: Commit**

```powershell
git add frontend/src/generationSegments.ts frontend/src/generationSegments.test.ts frontend/src/components/GenerationSegmentsEditor.tsx frontend/src/api.ts frontend/src/App.tsx frontend/src/App.css
git commit -m "feat: edit generation segments in the current workflow"
```

---

### Task 7: Make the Prompt UI Segment-Aware

**Files:**
- Modify: `frontend/src/components/PromptStage.tsx`
- Modify: `frontend/src/App.tsx`
- Modify: `frontend/src/api.ts`
- Modify: `frontend/src/promptWorkflow.test.ts`
- Modify: `frontend/src/App.css`

**Interfaces:**
- Consumes: current `GenerationSegmentPlan` and segment-filtered prompt revisions.
- Produces: selected segment ID and selected prompt version; AI generate/save/refine always carry the selected segment.

- [ ] **Step 1: Write prompt workflow RED tests**

Add pure selection tests asserting that changing from segment A to B cannot retain A's prompt version, completed versions are filtered by `generation_segment_id`, and a stale `full_video_description` revision is never selected for generation.

- [ ] **Step 2: Run RED**

Run:

```powershell
Set-Location frontend
npm.cmd test -- promptWorkflow.test.ts
```

Expected: failures for missing segment-aware selection.

- [ ] **Step 3: Update API and UI copy**

Change the fifth-step heading to “生成并校对编辑指令”. Add a segment selector showing `片段 1 · 00:00–00:16`. Show global rules and the selected segment's exact submitted video/reference images. Prompt creation and manual save include `generation_segment_id`; prompt listing filters by it. Refinement continues sending only `source_version`, and the server derives the immutable segment ID from that selected source revision so the client cannot move a refinement across segments.

The editable prompt remains plain text; do not build a custom rich-text editor. The fixed four-section structure is server-validated.

- [ ] **Step 4: Prevent cross-segment state leaks**

When segment changes: load that segment's completed revisions, select its latest version, or clear the editor if none. Active prompt jobs are matched through the prompt revision/segment linkage, not merely by project.

- [ ] **Step 5: Run GREEN and gates**

Run:

```powershell
npm.cmd test -- promptWorkflow.test.ts generationSegments.test.ts
npm.cmd run lint
npm.cmd run build
```

Expected: all pass.

- [ ] **Step 6: Commit**

```powershell
git add frontend/src/components/PromptStage.tsx frontend/src/App.tsx frontend/src/api.ts frontend/src/promptWorkflow.test.ts frontend/src/App.css
git commit -m "feat: edit prompts per generation segment"
```

---

### Task 8: Connect Generation, Recovery, Playback, and Download UI

**Files:**
- Create: `frontend/src/components/GenerationStage.tsx`
- Create: `frontend/src/generationDomain.ts`
- Create: `frontend/src/generationDomain.test.ts`
- Modify: `frontend/src/api.ts`
- Modify: `frontend/src/App.tsx`
- Modify: `frontend/src/workspaceDomain.ts`
- Modify: `frontend/src/components/WorkflowRail.tsx`
- Modify: `frontend/src/App.css`

**Interfaces:**
- Consumes: segments, segment-filtered completed edit prompts, generation list/detail/retry/resolve/content APIs.
- Produces: sixth workflow stage with one status/result card per segment.

- [ ] **Step 1: Write generation-domain RED tests**

Cover active/terminal status classification, submit blockers, grouping by segment, latest generation selection, and poll continuation while any segment is active.

```typescript
expect(canSubmitSegment({ segment, prompt, providerReady: true })).toBeNull()
expect(groupGenerationsBySegment(generations).get(segment.id)?.[0].version).toBe(3)
expect(shouldPollGenerations([{ status: 'completed' }, { status: 'processing' }])).toBe(true)
```

- [ ] **Step 2: Run RED**

Run:

```powershell
Set-Location frontend
npm.cmd test -- generationDomain.test.ts
```

Expected: module not found.

- [ ] **Step 3: Complete generation API contracts**

Add `listGenerations`, `retryGeneration`, and `resolveGeneration`; extend summaries with `generation_segment_id`, prompt version, attempts, task ID, timestamps, and `local_video_url`. Use `mediaUrl(local_video_url)` for playback/download.

- [ ] **Step 4: Build the sixth stage**

For every segment show: original segment preview when available, selected edit prompt, provider, generate-audio option, submitted reference-image list, generation button, current task state, generated player, retry/resolve actions, and download link. A segment can complete even if a sibling fails.

Use the existing visual language and responsive breakpoints. Do not redesign the header, rail, or earlier steps.

- [ ] **Step 5: Add recovery polling**

On project restore list all generations. Poll only while at least one generation is `queued`, `processing`, or `retryable`; stop for `completed`, `failed`, and `submission_uncertain`. Polling updates generation state only and must not reset timeline, prompt edits, or selected segment.

- [ ] **Step 6: Run GREEN and frontend gates**

Run:

```powershell
npm.cmd test
npm.cmd run lint
npm.cmd run build
```

Expected: all frontend tests pass, lint is clean, build succeeds.

- [ ] **Step 7: Commit**

```powershell
git add frontend/src/components/GenerationStage.tsx frontend/src/generationDomain.ts frontend/src/generationDomain.test.ts frontend/src/api.ts frontend/src/App.tsx frontend/src/workspaceDomain.ts frontend/src/components/WorkflowRail.tsx frontend/src/App.css
git commit -m "feat: complete segmented generation user flow"
```

---

### Task 9: Run Release Gates and Controlled Local Acceptance

**Files:**
- Modify only if a gate exposes a product defect: relevant production/test file.
- Create: `docs/adflow-segmented-generation-acceptance.md`

**Interfaces:**
- Consumes: all previous tasks.
- Produces: reproducible release evidence without secrets, signed URLs, provider payloads, or private media contents.

- [ ] **Step 1: Run full backend tests**

```powershell
Set-Location backend
python -m pytest -q
```

Expected: zero failures/errors. Environment skips are not accepted on the release machine because FFmpeg/FFprobe are installed.

- [ ] **Step 2: Run full frontend gates**

```powershell
Set-Location ..\frontend
npm.cmd test
npm.cmd run lint
npm.cmd run build
```

Expected: all pass.

- [ ] **Step 3: Verify PostgreSQL migration on a backup copy**

Use the existing backup and migration verification scripts. Verify Alembic head is `0005_segment_generations`, all three new migrations are present, existing project/material/timeline/prompt/generation counts are unchanged, and API health succeeds. Never run password synchronization or overwrite `.env`.

- [ ] **Step 4: Run prompt-only segmented acceptance without Seedance spend**

Use one real video over 29 seconds. Verify automatic N segments, manual segment count, shot-boundary movement, one explicit inside-shot cut, delta prompts for every segment, refresh recovery, and zero generation tasks before clicking generate.

- [ ] **Step 5: Request explicit quota approval**

Stop and ask the user before the first real Seedance submission. State provider, number of segment tasks, and that quota will be consumed.

- [ ] **Step 6: Run one controlled Seedance closed loop after approval**

Submit every segment of the chosen project. Verify one task per segment, correct source clip and prompt version, reference-image purpose/order, restart recovery, local playback, and download. Do not claim visual continuity or product fidelity without manually comparing the actual videos.

- [ ] **Step 7: Write acceptance evidence**

Record exact test counts, migration revision, media metadata, project ID, segment ranges, prompt versions, generation IDs/statuses, refresh/restart evidence, and known limitations. Exclude keys, full vendor responses, signed URLs, and private frame contents.

- [ ] **Step 8: Commit the acceptance record**

```powershell
git add docs/adflow-segmented-generation-acceptance.md
git commit -m "docs: record segmented generation acceptance"
```

---

## Final Self-Review Checklist

- Every design requirement maps to a task: segment algorithm (2), persistence/API (3), delta prompts (4), physical clips/Seedance (5), editing UI (6–7), recovery/results (8), real acceptance (9).
- One generation segment creates one generation task; no task introduces per-shot Seedance submissions.
- Current FFmpeg behavior is reused unchanged; no audio-preservation or frame-calibration work was silently added.
- Old rows remain nullable/legacy-compatible; new submissions require current segment-linked edit prompts.
- Product image labels are integrated once, before dependent work.
- Alembic revisions are immutable and ordered `0003` segments → `0004` prompts → `0005` generations.
- No task adds automatic concatenation, a new queue, a new UI framework, or a new dependency.
