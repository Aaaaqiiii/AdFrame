# AdFlow Full-Prompt Backend Implementation Plan

> **For agentic workers:** REQUIRED SUBSKILL: Use superpowers:test-driven-development for every task, and superpowers:verification-before-completion before claiming any task complete.

**Goal:** Replace the new-workflow segment-scoped prompt contract with one immutable, full-video edit prompt that can be generated, manually saved, refined, and optimized around product selling points.

**Architecture:** Keep `PromptRevision` as the immutable version ledger. New prompt revisions use `prompt_mode="full_reference_video_edit"` and `generation_segment_id=NULL`. A focused pure service parses and validates absolute-time shot blocks and derives segment-relative provider prompts. Route and worker orchestration remain thin; deterministic global product rules are built by the server and preserved across every AI transformation.

**Tech Stack:** Python 3, FastAPI, SQLAlchemy, Pydantic, PostgreSQL/SQLite tests, pytest, existing GPT/Comfly prompt gateways.

**Spec:** `docs/superpowers/specs/2026-08-18-full-prompt-batched-seedance-generation-design.md`

## Global constraints

- Work in the existing repository and preserve user changes. Do not reset, clean, or rewrite unrelated files.
- Execute tasks in order. Each task begins with a failing test and ends with a focused green test plus a small commit.
- Do not modify migrations `0001` through `0005`. This plan requires no database migration.
- New official prompts always cover the complete current timeline and use absolute source-video times.
- The four required columns for every expected shot block are exactly `保持：`, `修改：`, `删除：`, and `禁止：`.
- The server, not GPT, owns the deterministic prefix: mode lock, target-product profile, product-image purposes, people/background references, and audio instructions.
- Preserve historical prompt rows. Do not rewrite old `full_video_description` or `reference_video_edit` revisions.
- Do not call Seedance in this plan.

## File map

| File | Responsibility |
|---|---|
| `backend/app/services/full_prompt.py` | New pure parser, validator, absolute-label builder, deterministic-prefix preservation, and segment derivation |
| `backend/app/services/final_prompt.py` | AI generation/refinement/optimization orchestration using the pure full-prompt service |
| `backend/app/api/routes/projects.py` | Full-prompt create/save/refine/optimize API contracts and revision persistence |
| `backend/app/worker.py` | Dispatch the new selling-point optimization job kind |
| `backend/tests/test_full_prompt.py` | Pure parser, validation, prefix, boundary-crossing, and relative-time derivation tests |
| `backend/tests/test_projects_api.py` | Create/save/history/refine/optimize API tests |
| `backend/tests/test_simplified_prompt_workflow.py` | Worker execution, source snapshot, and immutable version behavior |
| `backend/tests/test_dual_product_workflows.py` | Preserve-product and replace-product deterministic rule regression coverage |

---

## Task 1: Introduce the pure full-prompt document contract

**Files:**

- Create: `backend/app/services/full_prompt.py`
- Create: `backend/tests/test_full_prompt.py`

- [ ] **Step 1: Write failing tests for the document parser and validator**

Add test-local `prompt_text()` and `shot_ranges()` builders, then cover these exact cases:

```python
def test_full_prompt_accepts_every_absolute_shot_block_once():
    document = validate_full_prompt(
        prompt_text(),
        [(0.0, 4.0), (4.0, 9.5)],
        required_prefixes=("产品参考图用途", "目标产品档案"),
    )
    assert [(b.source_start_sec, b.source_end_sec) for b in document.blocks] == [
        (0.0, 4.0),
        (4.0, 9.5),
    ]


@pytest.mark.parametrize(
    "mutator, expected",
    [
        (lambda text: text.replace("00:04.00–00:09.50", "00:04.00–00:09.40"), "时间块"),
        (lambda text: text.replace("删除：无", ""), "删除："),
        (lambda text: text + "\n\n00:00.00–00:04.00\n保持：重复\n修改：无\n删除：无\n禁止：无", "重复"),
        (lambda text: text.replace("产品参考图用途", "参考资料"), "产品参考图用途"),
    ],
)
def test_full_prompt_rejects_missing_changed_or_duplicate_structure(mutator, expected):
    with pytest.raises(FullPromptValidationError, match=expected):
        validate_full_prompt(
            mutator(prompt_text()),
            [(0.0, 4.0), (4.0, 9.5)],
            required_prefixes=("产品参考图用途",),
        )
```

Also test timestamp formatting beyond 59 seconds, CRLF input, harmless blank lines, out-of-order blocks, unknown extra blocks, empty column bodies, and a prefix containing ordinary numbers that must not be interpreted as timestamps.

- [ ] **Step 2: Run the RED test**

Run from `backend`:

```powershell
python -m pytest tests/test_full_prompt.py -q
```

Expected: collection fails because `app.services.full_prompt` does not exist.

- [ ] **Step 3: Implement exact public types and functions**

Create these public definitions; keep parsing helpers private:

```python
@dataclass(frozen=True)
class PromptBlock:
    source_start_sec: float
    source_end_sec: float
    keep: str
    modify: str
    delete: str
    forbid: str


@dataclass(frozen=True)
class FullPromptDocument:
    global_prefix: str
    blocks: tuple[PromptBlock, ...]


class FullPromptValidationError(ValueError):
    pass


def format_time_label(start_sec: float, end_sec: float) -> str: ...
def expected_full_prompt_labels(shot_ranges: Sequence[tuple[float, float]]) -> list[str]: ...
def parse_full_prompt(text: str) -> FullPromptDocument: ...
def validate_full_prompt(
    text: str,
    shot_ranges: Sequence[tuple[float, float]],
    *,
    required_prefixes: Sequence[str] = (),
) -> FullPromptDocument: ...
def render_full_prompt(document: FullPromptDocument) -> str: ...
```

Parsing rules:

- Normalize `\r\n` to `\n` and trim trailing whitespace only.
- Recognize a time header only when an entire line matches `MM:SS.xx–MM:SS.xx`; accept more than two minute digits.
- Everything before the first time header is `global_prefix`.
- Each time block must contain each required column exactly once and in the required order.
- A column body must contain at least one non-whitespace character; `无` is valid.
- Compare parsed labels against expected labels in order and one-to-one. Do not use fuzzy matching.
- Use integer centiseconds internally while comparing labels so floating-point representation cannot change equality.

- [ ] **Step 4: Add deterministic segment derivation tests**

Add a three-shot example with a segment starting and ending inside shots:

```python
def test_segment_derivation_clips_crossing_blocks_and_preserves_bodies():
    document = validate_full_prompt(
        three_shot_prompt(),
        [(0.0, 10.0), (10.0, 25.0), (25.0, 40.0)],
    )
    derived = derive_segment_prompt(
        document,
        segment_start_sec=18.0,
        segment_end_sec=32.0,
        batch_position=2,
        batch_size=3,
    )
    assert "片段 2/3" in derived
    assert "00:00.00–00:07.00" in derived
    assert "00:07.00–00:14.00" in derived
    assert "00:10.00–00:25.00" not in derived
    assert "镜头二保持正文" in derived
    assert "镜头三保持正文" in derived
```

Add tests for an 8-second full-coverage segment, exact-edge coverage, no overlap, empty intersections, a shot that appears in two neighboring segments with identical four-column bodies, and two repeated derivations producing byte-identical text.

- [ ] **Step 5: Implement deterministic derivation**

Add this public function:

```python
def derive_segment_prompt(
    document: FullPromptDocument,
    *,
    segment_start_sec: float,
    segment_end_sec: float,
    batch_position: int,
    batch_size: int,
) -> str: ...
```

For every block whose source interval intersects the segment, calculate:

```text
relative_start = max(block.start, segment.start) - segment.start
relative_end   = min(block.end, segment.end) - segment.start
```

Render the original deterministic prefix, a server-owned line identifying the batch position and original source interval, and the clipped relative blocks. Copy each four-column body byte-for-byte after newline normalization. Raise `FullPromptValidationError` if the segment is empty, outside the prompt, or produces no blocks.

- [ ] **Step 6: Run focused tests and commit**

```powershell
python -m pytest tests/test_full_prompt.py -q
git diff --check
git add -- app/services/full_prompt.py tests/test_full_prompt.py
git commit -m "feat: define full video edit prompt contract"
```

Expected: all `test_full_prompt.py` tests pass.

---

## Task 2: Generate and manually save one full prompt

**Files:**

- Modify: `backend/app/services/final_prompt.py`
- Modify: `backend/app/api/routes/projects.py`
- Modify: `backend/tests/test_projects_api.py`
- Modify: `backend/tests/test_simplified_prompt_workflow.py`
- Modify: `backend/tests/test_dual_product_workflows.py`

- [ ] **Step 1: Write failing API contract tests**

Add tests that build a project with a current timeline, confirmed shot edits, and confirmed product references. Assert:

```python
response = client.post(
    f"/api/projects/{project_id}/prompts",
    json={
        "product_profile": "核心卖点：轻薄、不黏腻",
        "visual_direction": "只修改产品，其他画面保持",
        "audio_mode": "generate",
        "audio_style": "轻快",
        "replace_product": True,
        "replace_person": False,
        "use_ai": True,
    },
)
assert response.status_code == 202
revision = prompt_revision_from_db(project_id, version=1)
assert revision.prompt_mode == "full_reference_video_edit"
assert revision.generation_segment_id is None
assert revision.text == ""
```

Add a manual-save case (`use_ai=False`) containing every absolute shot block and deterministic prefix; expect `201/completed`. Removing one entire block, one required column, the target-product profile, or one confirmed product-image purpose must return `422` and create no revision.

Add a compatibility assertion that historical `reference_video_edit` revisions remain readable and unchanged.

- [ ] **Step 2: Run RED tests**

```powershell
python -m pytest tests/test_projects_api.py tests/test_simplified_prompt_workflow.py tests/test_dual_product_workflows.py -k "full_reference_video_edit or full_prompt" -q
```

Expected: failures show the route still creates `full_video_description` or validates a segment-only prompt.

- [ ] **Step 3: Add server-owned full-prompt helpers**

In `final_prompt.py`, add these focused functions:

```python
def build_full_prompt_prefix(
    *,
    project_mode: str,
    product_profile: str,
    product_image_purposes: Sequence[str],
    people_reference: str | None,
    background_reference: str | None,
    audio_mode: str,
    audio_style: str,
) -> str: ...


async def generate_full_edit_prompt(
    *,
    settings: Settings,
    shot_slices: Sequence[dict[str, Any]],
    deterministic_prefix: str,
    visual_direction: str,
) -> str: ...
```

The AI system instruction must explicitly require:

- exactly one absolute-time block for every supplied shot and in supplied order;
- only the four required columns;
- `保持` describes what is held fixed, while `修改` and `删除` contain only deltas;
- no visible text, logo, label, watermark, subtitle, or invented copy unless the confirmed facts explicitly require it;
- no modification of the server-owned prefix;
- preserve-product mode may not replace, remove, or redesign the original product;
- replace-product mode must use the confirmed target-product profile and image-purpose names.

After the model returns, discard any model-authored global prefix, prepend `deterministic_prefix`, validate with `validate_full_prompt`, and permit exactly one targeted repair call for missing structural blocks. If the repaired result is still invalid, fail the job; never mark an invalid revision completed.

- [ ] **Step 4: Change prompt creation semantics**

In `projects.py`:

- A new prompt request without `generation_segment_id` creates `prompt_mode="full_reference_video_edit"`.
- Reject a non-null `generation_segment_id` for the new UI/API contract with `422` and a migration message; retain old rows but stop creating new `reference_video_edit` rows.
- Use every confirmed shot edit from the current timeline, not a segment intersection.
- For manual saves, compute expected absolute labels from the database shots and validate the submitted text using the same pure validator.
- Reuse the existing product-replacement detector only on `修改` and `删除` bodies. Do not scan the deterministic `禁止` rule itself.
- For replace-product manual saves, require the confirmed target profile, `产品参考图用途`, and every confirmed image display/view label in the deterministic prefix.
- Store the full submitted/generated text in `PromptRevision.text`; do not create child segment revisions.

- [ ] **Step 5: Update the final-prompt worker execution**

In `execute_final_prompt_job`:

- Branch on `revision.prompt_mode`.
- For `full_reference_video_edit`, load the exact source timeline revision frozen on the prompt revision, require all intersecting edits confirmed, build the prefix from confirmed database records, generate the complete prompt, validate, then mark completed.
- Retain the legacy branch for already queued historical rows.
- Preserve the source snapshot rule: the job may not drift to a later timeline, product profile, or prompt revision.

Add a test that queues v1, changes the current timeline afterward, runs the worker, and proves v1 still used its frozen timeline. Add a test that the completed full prompt contains all shot labels and all three product-image purposes.

- [ ] **Step 6: Run focused tests and commit**

```powershell
python -m pytest tests/test_projects_api.py tests/test_simplified_prompt_workflow.py tests/test_dual_product_workflows.py tests/test_full_prompt.py -q
git diff --check
git add -- app/api/routes/projects.py app/services/final_prompt.py tests/test_projects_api.py tests/test_simplified_prompt_workflow.py tests/test_dual_product_workflows.py
git commit -m "feat: generate one immutable full video prompt"
```

---

## Task 3: Preserve full-prompt identity through refinement and selling-point optimization

**Files:**

- Modify: `backend/app/api/routes/projects.py`
- Modify: `backend/app/services/final_prompt.py`
- Modify: `backend/app/worker.py`
- Modify: `backend/tests/test_projects_api.py`
- Modify: `backend/tests/test_simplified_prompt_workflow.py`

- [ ] **Step 1: Write failing refinement tests**

Cover both malicious and malformed model output:

```python
def test_full_prompt_refinement_restores_prefix_and_keeps_absolute_blocks(...):
    # Source v1 has deterministic prefix and all current absolute blocks.
    # Mock GPT output omits the prefix but returns edited blocks.
    # Completed v2 restores the exact v1 prefix and remains full_reference_video_edit.
    ...
    assert v2.prompt_mode == "full_reference_video_edit"
    assert v2.generation_segment_id is None
    assert v2.text.startswith(source_prefix)
    assert expected_full_prompt_labels(shot_ranges) == labels_from(v2.text)
```

Also assert that a refinement missing a block or introducing product replacement in preserve mode fails and does not become completed. Prove that selecting source version 1 remains frozen even if version 2 is created before the worker runs.

- [ ] **Step 2: Add and test the selling-point endpoint contract**

Add request schema and endpoint:

```python
class OptimizeSellingPointsRequest(BaseModel):
    source_version: int = Field(ge=1)


@router.post(
    "/{project_id}/prompts/optimize-selling-points",
    response_model=PromptRevisionSummary,
    status_code=status.HTTP_202_ACCEPTED,
)
def optimize_prompt_selling_points(...): ...
```

Test requirements:

- Source must be a completed `full_reference_video_edit` revision on the current timeline.
- Product profile and all required target-product confirmations must still be valid.
- The route creates a queued immutable next version and a `Job(kind="prompt_selling_point_optimization")`.
- The queued revision copies `prompt_mode`, null segment identity, source timeline, audio configuration, replacement flags, and a frozen copy of the source prompt in `text`.
- Missing source, non-completed source, stale timeline, or empty selling points returns `422` without creating a revision/job.

- [ ] **Step 3: Implement one validation pipeline for both transformations**

Create a private helper in `final_prompt.py`:

```python
async def transform_full_prompt(
    *,
    settings: Settings,
    source_text: str,
    instruction: str,
    expected_shot_ranges: Sequence[tuple[float, float]],
    required_prefixes: Sequence[str],
    project_mode: str,
) -> str: ...
```

It must:

1. Parse and validate the source.
2. Freeze the exact deterministic prefix from the source.
3. Send only the time-block body plus the instruction to GPT.
4. Remove any returned model prefix and restore the frozen prefix.
5. Validate every expected absolute block and four columns.
6. Run preserve-product replacement detection only over actionable `修改/删除` bodies.
7. Return a valid complete prompt or raise `ValueError`.

The selling-point instruction must allow GPT to distribute confirmed selling points across the most suitable existing shots, translate abstract confirmed claims into producible material/light/action-result/product-state descriptions, remove mechanical repetition, and improve continuity. It may edit only the appropriate `修改` bodies. It must never invent claims, certifications, ingredients, numbers, packaging copy, scenes, people, or actions; never add visible words/logos/overlays; never force every selling point into every shot; and never change labels, block count, order, or the deterministic prefix.

- [ ] **Step 4: Dispatch the new job kind**

In `worker.py`, route `prompt_selling_point_optimization` to a new `execute_selling_point_optimization_job` function. Follow the same lease, error, and terminal-state behavior as `prompt_refinement`. Do not create a special worker or queue.

- [ ] **Step 5: Run focused tests and commit**

```powershell
python -m pytest tests/test_projects_api.py tests/test_simplified_prompt_workflow.py -k "refinement or selling_point or full_prompt" -q
git diff --check
git add -- app/api/routes/projects.py app/services/final_prompt.py app/worker.py tests/test_projects_api.py tests/test_simplified_prompt_workflow.py
git commit -m "feat: optimize full prompts around confirmed selling points"
```

---

## Task 4: Stabilize history/recovery contracts and run the backend gate

**Files:**

- Modify: `backend/app/api/routes/projects.py`
- Modify: `backend/tests/test_projects_api.py`
- Modify: `backend/tests/test_simplified_prompt_workflow.py`
- Modify: `backend/tests/test_dual_product_workflows.py`

- [ ] **Step 1: Add an explicit prompt-mode history filter**

Extend the existing history endpoint with:

```python
prompt_mode: Literal[
    "full_reference_video_edit",
    "reference_video_edit",
    "full_video_description",
] | None = Query(default=None)
```

Apply it in SQL, together with the existing current-timeline and completed-status rules. The frontend can then restore only full prompts without deleting legacy history.

- [ ] **Step 2: Add recovery tests**

Prove all of the following:

- history returns completed full prompts newest-first;
- a queued/failed latest version does not hide the latest completed version;
- `prompt_mode=full_reference_video_edit` excludes historical segment prompts;
- a refresh after manual v3 restores v3 rather than v1;
- an optimization queued from v1 remains bound to its v1 text even if v2 is saved later;
- no prompt endpoint creates a `Generation` row or calls either Seedance gateway.

- [ ] **Step 3: Run focused and complete backend verification**

```powershell
python -m pytest tests/test_full_prompt.py tests/test_projects_api.py tests/test_simplified_prompt_workflow.py tests/test_dual_product_workflows.py -q
python -m pytest -q
git diff --check
```

Expected: zero failures. If media tests are skipped because the current process cannot resolve FFmpeg/FFprobe, fix the process environment and rerun; do not report an environment skip as equivalent to execution.

- [ ] **Step 4: Review against the specification**

Read the specification sections for prompt semantics and verify, line by line:

- exactly one official full prompt;
- absolute source-time labels;
- immutable revisions;
- deterministic prefix preservation;
- no segment prompt revisions;
- no Seedance call;
- legacy rows readable.

Search for stale new-workflow creation of `reference_video_edit` and prove it is absent outside compatibility fixtures:

```powershell
Get-ChildItem app,tests -Recurse -File | Select-String -Pattern 'reference_video_edit'
```

- [ ] **Step 5: Commit history/recovery changes**

```powershell
git add -- app/api/routes/projects.py tests/test_projects_api.py tests/test_simplified_prompt_workflow.py tests/test_dual_product_workflows.py
git commit -m "test: lock full prompt recovery contracts"
git status --short
```

Expected: only user-owned unrelated untracked files may remain.

## Plan 1 handoff

Record the commit IDs and exact test totals. Then continue directly with `2026-08-18-adflow-batched-seedance-backend-implementation.md`; do not begin frontend work before the batch API and worker contracts are green.
