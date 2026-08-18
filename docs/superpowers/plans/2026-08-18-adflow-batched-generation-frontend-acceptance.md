# AdFlow Batched Generation Frontend and Acceptance Plan

> **For agentic workers:** REQUIRED SUBSKILL: Use superpowers:test-driven-development for every task, superpowers:systematic-debugging for any browser/runtime failure, and superpowers:verification-before-completion before claiming release readiness.

**Goal:** Extend the existing AdFlow page—not replace it—with full-prompt selling-point optimization, one-provider batch submission, refresh-safe batch progress, independent playback/download, responsive layout, and controlled real acceptance for both Seedance providers.

**Architecture:** `App.tsx` remains the workflow state owner. Pure frontend domain helpers normalize prompt and batch state. `PromptStage` continues to display one complete editable prompt and adds optimization/generation controls. A new `GenerationStage` groups backend tasks by batch and position, polls only active work, and delegates playback/download to application-owned endpoints. Runtime acceptance uses the existing local API/frontend/worker startup path and the new Alembic head.

**Tech Stack:** React, TypeScript, Vite, Vitest, Testing Library if already configured, CSS, FastAPI local runtime, PostgreSQL, FFmpeg/FFprobe, Volcengine Seedance, Comfly Seedance.

**Spec:** `docs/superpowers/specs/2026-08-18-full-prompt-batched-seedance-generation-design.md`

## Global constraints

- Complete Plans 1 and 2 first. Do not mock a frontend contract that the backend does not implement.
- Preserve the current navigation, workflow rail, card language, spacing system, and responsive approach. This is an extension of the existing page, not a redesign.
- The user sees and edits one full prompt. Never add a per-segment prompt editor or per-shot generation button.
- Generation segments remain visible as an execution plan, but selection of a segment must not change the official prompt text.
- One click creates one batch using one selected provider. The UI must show the number of tasks before confirmation.
- Never silently switch providers.
- Page refresh must restore the latest completed full prompt, latest batches, per-position retry state, and playable results.
- Poll active batches only. Stop polling terminal batches and when the component unmounts.
- Do not expose raw provider URLs, signed reference URLs, local paths, or API keys in the DOM.
- Do not run real external generation until the user explicitly approves the quota-spending acceptance step.

## File map

| File | Responsibility |
|---|---|
| `frontend/src/api.ts` | Full-prompt, batch, retry, content, and download API types/functions |
| `frontend/src/generationDomain.ts` | New pure batch grouping/status/polling/retry helpers |
| `frontend/src/generationDomain.test.ts` | Pure helper tests |
| `frontend/src/components/PromptStage.tsx` | Single full-prompt editor, selling-point optimization, provider/audio controls, batch start |
| `frontend/src/components/GenerationStage.tsx` | New batch progress/result cards, playback, download, per-task retry/resolve |
| `frontend/src/App.tsx` | Restore/order/polling/state orchestration and stage transitions |
| `frontend/src/workspaceDomain.ts` | Add generation/results workflow stage without removing existing stages |
| `frontend/src/components/WorkflowRail.tsx` | Label and order the new final stage |
| `frontend/src/App.css` | Existing-system responsive styles for controls and result cards |
| `frontend/src/promptWorkflow.test.ts` | Full-prompt restore and immutable version tests |
| `frontend/src/App.test.tsx` or existing component test location | Integration tests using the repository's current render harness |
| `docs/adflow-full-prompt-batched-generation-acceptance.md` | Sanitized release evidence and remaining operational limitations |

---

## Task 1: Add typed full-prompt and batch API/domain contracts

**Files:**

- Modify: `frontend/src/api.ts`
- Create: `frontend/src/generationDomain.ts`
- Create: `frontend/src/generationDomain.test.ts`
- Modify: `frontend/src/promptWorkflow.test.ts`

- [ ] **Step 1: Correct prompt revision types**

Extend `PromptRevisionSummary` to match the backend exactly:

```ts
export type PromptMode =
  | 'full_reference_video_edit'
  | 'reference_video_edit'
  | 'full_video_description'

export interface PromptRevisionSummary {
  id: string
  project_id: string
  version: number
  prompt_mode: PromptMode
  generation_segment_id: string | null
  text: string
  status: string
  error_message: string | null
  created_at: string
}
```

Keep the repository's additional existing fields rather than deleting them. Change full-prompt history loading to pass `prompt_mode=full_reference_video_edit`.

- [ ] **Step 2: Add exact batch API types and functions**

Add:

```ts
export type BatchStatus =
  | 'queued'
  | 'processing'
  | 'complete'
  | 'partial'
  | 'failed'
  | 'uncertain'

export interface GenerationSummary {
  // retain all existing fields
  generation_batch_id: string | null
  batch_position: number | null
  batch_size: number | null
  generation_segment_id: string | null
}

export interface GenerationBatch {
  generation_batch_id: string
  project_id: string
  provider: 'volcengine' | 'comfly'
  prompt_version: number
  batch_size: number
  status: BatchStatus
  generations: GenerationSummary[]
}
```

Functions:

```ts
export function listGenerationBatches(projectId: string): Promise<GenerationBatch[]>
export function getGenerationBatch(projectId: string, batchId: string): Promise<GenerationBatch>
export function createGenerationBatch(
  projectId: string,
  input: {
    provider: 'volcengine' | 'comfly'
    prompt_version: number
    generate_audio: boolean
    include_person_reference: boolean
    include_background_reference: boolean
  },
): Promise<GenerationBatch>
export function retryGeneration(projectId: string, generationId: string): Promise<GenerationSummary>
export function generationContentUrl(projectId: string, generationId: string): string
export function generationDownloadUrl(projectId: string, generationId: string): string
export function optimizePromptSellingPoints(
  projectId: string,
  sourceVersion: number,
): Promise<PromptRevisionSummary>
```

Use the actual existing endpoint prefixes from `api.ts`; do not create a second base-URL mechanism.

- [ ] **Step 3: Write RED pure-domain tests**

Define and test:

```ts
export function latestGenerationPerPosition(batch: GenerationBatch): GenerationSummary[]
export function isBatchActive(status: BatchStatus): boolean
export function canRetryGeneration(generation: GenerationSummary): boolean
export function batchProgress(batch: GenerationBatch): { done: number; total: number }
export function chooseLatestFullPrompt(revisions: PromptRevisionSummary[]): PromptRevisionSummary | null
```

Required cases:

- retry v6 replaces failed v4 at position 2 while successful position 1 remains;
- ordered output is position 1..N even if API input is shuffled;
- missing or duplicate positions do not get silently counted as complete;
- active is true only for queued/processing;
- uncertain is not auto-retryable;
- progress counts `completed`, `failed`, and `submission_uncertain` as done but not queued/processing/retryable;
- prompt restore ignores queued/failed revisions and all legacy prompt modes, then chooses highest completed full-prompt version.

- [ ] **Step 4: Run RED, implement minimal helpers, and run GREEN**

```powershell
npm.cmd test -- generationDomain.test.ts promptWorkflow.test.ts
```

Expected RED: missing module/types. Implement pure helpers without React or network access, then rerun expecting green.

- [ ] **Step 5: Commit**

```powershell
npm.cmd run lint
git diff --check
git add -- src/api.ts src/generationDomain.ts src/generationDomain.test.ts src/promptWorkflow.test.ts
git commit -m "feat: add full prompt batch frontend contracts"
```

---

## Task 2: Adapt the existing prompt screen to one full prompt and one-click batch creation

**Files:**

- Modify: `frontend/src/components/PromptStage.tsx`
- Modify: `frontend/src/App.tsx`
- Modify: `frontend/src/App.css`
- Modify: the repository's existing PromptStage/App test file

- [ ] **Step 1: Write component behavior tests before changing JSX**

Using the current component test harness, assert:

- the editor displays one full prompt, not one panel per generation segment;
- absolute labels such as `00:00.00–00:12.50` remain visible and editable;
- the version selector lists completed full-prompt versions only;
- “根据产品卖点优化” calls the optimization handler with the selected version;
- while optimization is queued/running, the button is disabled and the last completed prompt remains visible;
- provider options are exactly “火山 Seedance 2.5” and “Comfly Seedance 2.5”;
- selecting a provider never changes the prompt text;
- before generation the screen states `将创建 N 个生成任务，每段最多 29 秒` using the persisted plan count;
- generation is disabled if the plan is dirty/unsaved, absent, stale, contains a validation issue, no completed full prompt is selected, or a submission is active;
- one click calls `onCreateBatch` exactly once with provider, prompt version, audio, and optional-reference flags.

- [ ] **Step 2: Run RED test**

```powershell
npm.cmd test -- PromptStage
```

Use the exact test filename if Vitest does not filter by component name. Expected: missing controls/props.

- [ ] **Step 3: Extend props without rebuilding the component**

Retain existing layout and add these concepts to the existing props interface:

```ts
type PromptStageProps = {
  // retain current props
  generationPlan: GenerationPlan | null
  generationPlanDirty: boolean
  selectedProvider: 'volcengine' | 'comfly'
  generateAudio: boolean
  includePersonReference: boolean
  includeBackgroundReference: boolean
  optimizeBusy: boolean
  generationBusy: boolean
  onOptimizeSellingPoints: (sourceVersion: number) => Promise<void>
  onProviderChange: (provider: 'volcengine' | 'comfly') => void
  onGenerateAudioChange: (enabled: boolean) => void
  onCreateBatch: () => Promise<void>
}
```

Place new controls after the existing full-prompt version/editor/save/refine controls. Do not move the product-image or timeline sections. Do not add per-segment prompt cards.

- [ ] **Step 4: Wire immutable optimization in `App.tsx`**

On optimization:

1. call `optimizePromptSellingPoints(projectId, selectedPromptVersion)`;
2. keep the selected completed source visible while the new revision is queued;
3. reuse the existing project/job polling path;
4. when the revision completes, reload only full-prompt history and select the new completed version;
5. on failure, show the backend error and preserve the old selection/text.

Manual save and ordinary refine must continue creating new versions rather than overwriting text.

- [ ] **Step 5: Wire batch creation**

On one click:

1. confirm `generationPlan.plan_version > 0`, draft is clean, and `segments.length > 0`;
2. call `createGenerationBatch` once;
3. append/replace the returned batch state;
4. navigate the workflow stage to generation results;
5. do not loop over segments in the browser and do not call the legacy single-generation endpoint.

Display backend validation messages verbatim enough for the user to act, while avoiding raw stack traces.

- [ ] **Step 6: Run tests, lint, build, and commit**

```powershell
npm.cmd test
npm.cmd run lint
npm.cmd run build
git diff --check
git add -- src/components/PromptStage.tsx src/App.tsx src/App.css
git commit -m "feat: start batched generation from the full prompt"
```

---

## Task 3: Add refresh-safe batch progress, playback, download, and per-segment recovery

**Files:**

- Create: `frontend/src/components/GenerationStage.tsx`
- Modify: `frontend/src/App.tsx`
- Modify: `frontend/src/workspaceDomain.ts`
- Modify: `frontend/src/components/WorkflowRail.tsx`
- Modify: `frontend/src/App.css`
- Modify/Create: component integration tests in the repository's existing test location

- [ ] **Step 1: Write RED result-stage tests**

Cover these rendered states:

- queued and processing cards show position, source interval, provider, and non-terminal status;
- successful cards use `<video controls>` with `generationContentUrl(projectId, id)` and a separate download link using `generationDownloadUrl(projectId, id)`; the content helper omits `download`, while the download helper appends `?download=true`;
- a failed card shows its safe error and a retry button affecting only that position;
- an uncertain card shows the existing manual resolve controls, not automatic retry;
- partial batch visibly distinguishes successful and failed positions;
- complete batch shows all separate results and no “拼接” action;
- provider is shown at batch level and cannot be switched for existing tasks;
- no rendered field contains `clip_path`, a filesystem drive prefix, or raw signed URL query parameters.

- [ ] **Step 2: Implement `GenerationStage` inside the current design system**

Required props:

```ts
type GenerationStageProps = {
  batch: GenerationBatch | null
  retryingGenerationId: string | null
  onRetry: (generationId: string) => Promise<void>
  onResolve: (generationId: string, resolution: 'submitted' | 'not_submitted') => Promise<void>
  onRefresh: () => Promise<void>
  onBackToPrompt: () => void
}
```

Render one batch summary card and one result card per latest batch position. Use the established card/button/badge classes where possible. New CSS must be narrowly scoped under `.generation-stage`.

- [ ] **Step 3: Add workflow stage and restore ordering**

Append a final `generation` stage after `prompt`; do not renumber or remove semantic stages unpredictably. Restore in this order after project load:

1. project/assets;
2. current timeline and shot edits;
3. immutable generation plan;
4. completed full-prompt history and selected version;
5. generation batches and latest selected batch.

If one restore call fails, retain already restored earlier state and show a scoped error. A missing batch is a valid empty state.

- [ ] **Step 4: Implement bounded polling**

When the selected batch status is queued or processing, poll its detail endpoint using the app's existing interval convention. Requirements:

- one timer per selected batch;
- clear timer on terminal status, project/batch change, or unmount;
- prevent overlapping requests;
- back off or show a recoverable error after repeated network failures instead of starting multiple timers;
- update only the returned batch and keep selected prompt/segment drafts intact.

Add fake-timer tests proving terminal stop, unmount cleanup, and no duplicate polling after rerender.

- [ ] **Step 5: Implement per-position retry/resolve**

Retry calls the existing per-generation endpoint once, then refreshes the batch. It must not resubmit successful siblings. Manual resolution follows the existing backend contract and also refreshes the batch. Disable only the affected card while its action is pending.

- [ ] **Step 6: Run frontend gate and commit**

```powershell
npm.cmd test
npm.cmd run lint
npm.cmd run build
git diff --check
git add -- src/components/GenerationStage.tsx src/App.tsx src/workspaceDomain.ts src/components/WorkflowRail.tsx src/App.css
git commit -m "feat: recover and play batched generation results"
```

---

## Task 4: Verify responsive layout and local runtime without external spend

**Files:**

- Modify only if a verified defect exists: `frontend/src/App.css`, relevant frontend component, startup scripts
- Create: `docs/adflow-full-prompt-batched-generation-acceptance.md`

- [ ] **Step 1: Run all automated release gates from a clean process environment**

Backend:

```powershell
Set-Location 'E:\工具-商用\backend'
alembic upgrade head
alembic current
python -m pytest -q
```

Frontend:

```powershell
Set-Location 'E:\工具-商用\frontend'
npm.cmd test
npm.cmd run lint
npm.cmd run build
```

Expected: Alembic head `0006_generation_batches`, zero test failures, zero media skips caused by missing FFmpeg/FFprobe, clean lint, successful build.

- [ ] **Step 2: Restart API, worker, and frontend through the repository startup path**

Stop only processes belonging to this repository after resolving their exact command lines. Restart through the maintained scripts so API and worker inherit the same FFmpeg/FFprobe and database environment. Do not use the destructive password synchronization script; current authentication must be verified first.

Verify:

```text
GET /api/health -> 200 {"status":"ok"}
generation-segments route -> not 404
generation-batches route -> authenticated/expected application response, not 404
worker process -> running with the same backend environment
frontend -> loads the latest compiled/dev source
```

Record executable paths and version output for FFmpeg and FFprobe without copying secrets.

- [ ] **Step 3: Perform browser workflow checks at three widths**

Use the in-app browser or installed Chrome at:

- 1440×900;
- 920×900;
- 390×844.

At each width verify:

- no document-level horizontal overflow;
- workflow rail/navigation remains usable;
- three named product-image cards retain readable labels;
- generation-plan cards and short-segment controls are not clipped;
- full prompt editor remains one editor and does not become segment tabs;
- version selector, optimize button, provider selector, and batch button wrap without overlap;
- result video cards, retry buttons, and download links remain reachable;
- long status/error text wraps and does not widen the page.

Save screenshots under a temporary evidence directory or a gitignored report asset directory. Do not commit screenshots containing user media unless the user explicitly asks.

- [ ] **Step 4: Run a no-spend local/API smoke**

Using a fixture or existing local project without sending to a provider, verify:

- a >30-second source can have N segments each ≤29 seconds;
- the full prompt contains all absolute shot blocks;
- manual save/refine/selling optimization creates immutable versions;
- the batch preflight rejects an invalid prompt or stale plan atomically;
- mocked/local test submission creates N rows, not per-shot rows;
- refresh restores the latest completed full prompt and batch state.

- [ ] **Step 5: Write the acceptance report and commit verified fixes/docs**

The report must contain:

- commit IDs and migration head;
- exact automated test totals;
- runtime executable/version evidence;
- responsive widths checked;
- sanitized smoke project ID if safe;
- unresolved limitations: no automatic concatenation, split source clips omit original audio, semantic truth still requires user review;
- a clear statement that no real provider quota was spent in Task 4.

```powershell
git diff --check
git add -- docs/adflow-full-prompt-batched-generation-acceptance.md
git add -- frontend/src/App.css frontend/src/components/PromptStage.tsx frontend/src/components/GenerationStage.tsx
git commit -m "test: verify batched generation release candidate"
```

Only stage source files that were actually changed to fix verified defects.

---

## Task 5: Controlled real acceptance with both Seedance providers

**Files:**

- Modify: `docs/adflow-full-prompt-batched-generation-acceptance.md`
- Production code only if a real, reproduced defect is first covered by a failing automated test

- [ ] **Step 1: Stop and obtain explicit quota authorization**

Before calling either provider, present the user with:

- the number of planned segments/tasks;
- chosen test duration and expected two-provider task count;
- confirmation that Volcengine and Comfly each consume paid/limited quota;
- confirmation that product/reference media will be transmitted to those providers.

Do not proceed from old general approval. Obtain approval immediately before this step.

- [ ] **Step 2: Choose the smallest representative real project**

Use a confirmed project that exercises the actual contract while minimizing spend:

- source long enough to prove two execution segments if quota permits;
- at least two named product reference views;
- confirmed shot facts;
- a completed full prompt with deterministic product rules;
- no sensitive content in logs/reports.

If the user approves only one segment per provider, use a ≤29-second project for provider transport acceptance and retain automated evidence for multi-segment batching. State that limitation explicitly.

- [ ] **Step 3: Submit one complete batch through Volcengine**

Select `volcengine`, submit once, and verify:

- one external task per plan segment;
- no Comfly call;
- every provider request uses the correct physical segment and relative prompt;
- all confirmed product images are referenced;
- polling survives at least one page refresh;
- every successful result downloads locally and plays through the application endpoint;
- any uncertain state can be resolved without duplicating sibling tasks.

- [ ] **Step 4: Submit one complete batch through Comfly**

Repeat using `comfly`. Verify the same conditions and explicitly prove there was no Volcengine fallback.

- [ ] **Step 5: Verify restart recovery**

During a safe non-terminal polling window, restart only the worker through the maintained startup path. Verify lease recovery, continued polling, no duplicate external submission, and unchanged batch position identity. Do not interrupt a provider submission at an unsafe point merely to manufacture this test; existing automated crash-window tests remain authoritative where timing cannot be controlled safely.

- [ ] **Step 6: Record sanitized evidence and rerun regression gates**

Add to the acceptance report:

- batch IDs and generation IDs;
- provider names/models;
- segment count and durations;
- terminal states and local result presence;
- refresh/restart recovery outcome;
- exact amount of quota only if the provider reports it; otherwise say unknown rather than estimating;
- no keys, signed URLs, raw provider response bodies, or user media contents.

Then rerun:

```powershell
Set-Location 'E:\工具-商用\backend'
python -m pytest -q
Set-Location 'E:\工具-商用\frontend'
npm.cmd test
npm.cmd run lint
npm.cmd run build
git diff --check
```

- [ ] **Step 7: Commit report or defect fix and perform final review**

If no code defect was found:

```powershell
git add -- docs/adflow-full-prompt-batched-generation-acceptance.md
git commit -m "test: accept both Seedance batch providers"
```

If a defect was found, first add a failing automated regression, implement the smallest fix, rerun focused and full gates, and commit the fix separately from the evidence report.

## Final completion checklist

- One full absolute-time prompt is visible, editable, savable, refinable, and selling-point optimizable.
- One batch request creates exactly N segment tasks atomically.
- Both providers work without automatic fallback.
- Every task receives the correct clip, relative prompt, and all confirmed product images.
- Refresh and worker restart recover progress without duplicate submission.
- Successful results play and download independently.
- Responsive checks pass at 1440, 920, and 390 widths.
- Backend full suite, frontend tests, lint, and build all pass after real acceptance.
- Acceptance report contains sanitized, reproducible evidence and the known no-concatenation/original-audio limitations.
