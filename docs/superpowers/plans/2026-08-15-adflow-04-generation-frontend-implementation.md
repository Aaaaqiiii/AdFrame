# AdFlow Generation Frontend Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在当前页面结构内增加独立第六步，完成生成提交、任务恢复、结果播放下载和历史操作。

**Architecture:** 生成状态使用 `App.tsx` 的局部状态和单独轮询 effect，不引入新状态库。`GenerationStage` 是纯受控组件；状态文案、提交阻止、排序和活跃判断放进可单测的 `generationDomain.ts`。

**Tech Stack:** React 19、TypeScript 6、Vite 8、Vitest、现有 CSS。

## Global Constraints

- 保留 `AppHeader`、页面一/页面二入口、左侧 `WorkflowRail`、右侧 `stage-host` 和全局状态条。
- 不改前五步总体布局，不把工作流改成顶部步骤条。
- 使用现有橙色强调色、字体、边框、圆角、间距和按钮类。
- 第六步采用主内容区加 320px 右栏；920px 以下改为单列。
- 第五步移除保留产品模式中的目标产品上传和“替换产品”开关。
- 不增加 Redux、Zustand、React Query、路由库、组件库或 React Testing Library。
- 生成轮询只更新提示词/生成数据，不调用重置编辑状态的 `restoreProject()`。

---

## File Map

- Create: `frontend/src/generationDomain.ts` — 生成纯函数和类型窄化。
- Create: `frontend/src/generationDomain.test.ts` — 状态、排序、阻止原因、轮询测试。
- Modify: `frontend/src/api.ts` — 提示词历史和生成 API 类型/函数。
- Modify: `frontend/src/workspaceDomain.ts` — 第六步工作流阶段。
- Modify: `frontend/src/workspaceDomain.test.ts` — 第六步类型/解锁规则。
- Modify: `frontend/src/components/WorkflowRail.tsx` — 追加第六步。
- Modify: `frontend/src/components/PromptStage.tsx` — 删除旧产品替换入口。
- Create: `frontend/src/components/GenerationStage.tsx` — 第六步受控视图。
- Modify: `frontend/src/App.tsx` — 数据加载、动作和独立轮询。
- Delete: `frontend/src/workflow.ts` — 删除旧的“保存提示词/手动发布/立即提交”准备器。
- Delete: `frontend/src/workflow.test.ts` — 由生成域测试取代。
- Modify: `frontend/src/App.css` — 仅增加生成页面样式和响应式规则。

### Task 1: Build and test the generation domain

**Files:**
- Create: `frontend/src/generationDomain.ts`
- Create: `frontend/src/generationDomain.test.ts`

**Interfaces:**
- Consumes: API `GenerationSummary`, provider readiness and selected prompt.
- Produces: `GenerationStatus`, `isActiveGeneration`, `generationStatusLabel`, `generationStatusTone`, `sortGenerations`, `canSubmitGeneration`.

- [ ] **Step 1: Write the complete failing pure-function tests**

```ts
import { describe, expect, it } from 'vitest'
import {
  canSubmitGeneration,
  generationStatusLabel,
  generationStatusTone,
  isActiveGeneration,
  sortGenerations,
} from './generationDomain'

describe('generation domain', () => {
  it('polls only automatic non-terminal states', () => {
    expect(['queued', 'processing', 'retryable'].map(isActiveGeneration)).toEqual([true, true, true])
    expect(['submission_uncertain', 'failed', 'completed'].map(isActiveGeneration)).toEqual([false, false, false])
  })

  it('presents every status in Chinese', () => {
    expect(generationStatusLabel('queued')).toBe('本地排队中')
    expect(generationStatusLabel('processing')).toBe('Seedance 生成中')
    expect(generationStatusLabel('retryable')).toBe('等待自动重试')
    expect(generationStatusLabel('submission_uncertain')).toBe('提交结果待确认')
    expect(generationStatusLabel('failed')).toBe('生成失败')
    expect(generationStatusLabel('completed')).toBe('已完成')
    expect(generationStatusTone('completed')).toBe('success')
    expect(generationStatusTone('submission_uncertain')).toBe('danger')
    expect(generationStatusTone('processing')).toBe('working')
  })

  it('sorts without mutating API data', () => {
    const source = [{ version: 1 }, { version: 3 }, { version: 2 }]
    expect(sortGenerations(source as never).map(item => item.version)).toEqual([3, 2, 1])
    expect(source.map(item => item.version)).toEqual([1, 3, 2])
  })

  it('returns the first actionable submit blocker', () => {
    expect(canSubmitGeneration({ promptVersion: 0, providerReady: true, submitting: false })).toBe('请先在第五步保存一份当前时间轴的完整提示词。')
    expect(canSubmitGeneration({ promptVersion: 4, providerReady: false, submitting: false })).toBe('所选生成通道尚未配置。')
    expect(canSubmitGeneration({ promptVersion: 4, providerReady: true, submitting: true })).toBe('正在创建生成任务。')
    expect(canSubmitGeneration({ promptVersion: 4, providerReady: true, submitting: false })).toBe('')
  })
})
```

- [ ] **Step 2: Run the tests and verify missing module failure**

```powershell
Set-Location E:\工具-商用\frontend
npm.cmd test -- generationDomain.test.ts
```

Expected: FAIL because `generationDomain.ts` does not exist.

- [ ] **Step 3: Implement the pure domain**

```ts
import type { GenerationSummary } from './api'

export type GenerationStatus = 'queued' | 'processing' | 'retryable' | 'submission_uncertain' | 'failed' | 'completed'
export type GenerationSubmitInput = { promptVersion: number; providerReady: boolean; submitting: boolean }

export function isActiveGeneration(status: GenerationStatus) {
  return status === 'queued' || status === 'processing' || status === 'retryable'
}

export function generationStatusLabel(status: GenerationStatus) {
  return {
    queued: '本地排队中', processing: 'Seedance 生成中', retryable: '等待自动重试',
    submission_uncertain: '提交结果待确认', failed: '生成失败', completed: '已完成',
  }[status]
}

export function generationStatusTone(status: GenerationStatus) {
  if (status === 'completed') return 'success'
  if (status === 'failed' || status === 'submission_uncertain') return 'danger'
  return 'working'
}

export function sortGenerations(items: GenerationSummary[]) {
  return [...items].sort((left, right) => right.version - left.version)
}

export function canSubmitGeneration(input: GenerationSubmitInput) {
  if (!input.promptVersion) return '请先在第五步保存一份当前时间轴的完整提示词。'
  if (!input.providerReady) return '所选生成通道尚未配置。'
  if (input.submitting) return '正在创建生成任务。'
  return ''
}
```

- [ ] **Step 4: Run the domain test**

```powershell
npm.cmd test -- generationDomain.test.ts
```

Expected: PASS.

- [ ] **Step 5: Commit generation domain logic**

```powershell
git add frontend/src/generationDomain.ts frontend/src/generationDomain.test.ts
git commit -m "feat: define generation UI states"
```

### Task 2: Add typed prompt and generation API clients

**Files:**
- Modify: `frontend/src/api.ts`
- Modify: `frontend/src/generationDomain.test.ts`

**Interfaces:**
- Consumes: backend plan 2/3 HTTP contracts.
- Produces: `PromptRevisionSummary`, complete `GenerationSummary`, and six API functions.

- [ ] **Step 1: Replace the incomplete API types**

```ts
export type PromptRevisionSummary = {
  id: string
  version: number
  text: string
  status: 'completed'
  source_timeline_revision_id: string | null
  replace_product: boolean
  replace_person: boolean
  created_at: string
}

export type GenerationSummary = {
  id: string
  version: number
  prompt_version: number
  provider: 'volcengine' | 'comfly'
  status: GenerationStatus
  generate_audio: boolean
  external_task_id?: string | null
  attempts: number
  next_attempt_at?: string | null
  result_url?: string | null
  local_video_url?: string | null
  error_message?: string | null
  created_at: string
  completed_at?: string | null
}
```

Import `GenerationStatus` with `import type` from `generationDomain.ts`. Type-only circular references disappear at runtime.

- [ ] **Step 2: Add exact API functions**

```ts
export function listPromptRevisions(projectId: string) {
  return request<PromptRevisionSummary[]>(`/api/projects/${projectId}/prompts?current_timeline_only=true&status=completed`)
}
export function listGenerations(projectId: string) {
  return request<GenerationSummary[]>(`/api/projects/${projectId}/generations`)
}
export function createGeneration(projectId: string, payload: {
  provider: 'volcengine' | 'comfly'
  prompt_version: number
  generate_audio: boolean
  include_person_reference: boolean
  include_background_reference: boolean
}) {
  return request<GenerationSummary>(`/api/projects/${projectId}/generations`, { method: 'POST', ...json(payload) })
}
export function retryGeneration(projectId: string, generationId: string) {
  return request<GenerationSummary>(`/api/projects/${projectId}/generations/${generationId}/retry`, { method: 'POST' })
}
export function resolveGeneration(projectId: string, generationId: string, payload: { action: 'attach_task' | 'confirm_not_created'; external_task_id?: string }) {
  return request<GenerationSummary>(`/api/projects/${projectId}/generations/${generationId}/resolve`, { method: 'POST', ...json(payload) })
}
export function generationVideoUrl(path?: string | null) { return path ? mediaUrl(path) : '' }
```

Delete `publishReferenceVideo`, the old `createGeneration` signature containing ratio/duration, and redundant narrow `getGeneration` if no caller remains.

- [ ] **Step 3: Run TypeScript build to expose contract mismatches**

```powershell
npm.cmd run build
```

Expected before App migration: TypeScript errors at obsolete generation call sites are acceptable and identify Task 5 work; no errors should originate in `api.ts` or `generationDomain.ts`.

- [ ] **Step 4: Commit API types together with Task 3 after build is green**

Do not commit a known red build. Keep these edits uncommitted until `GenerationStage` compiles in Task 3.

### Task 3: Append the sixth workflow step and controlled view

**Files:**
- Modify: `frontend/src/workspaceDomain.ts`
- Modify: `frontend/src/workspaceDomain.test.ts`
- Modify: `frontend/src/components/WorkflowRail.tsx`
- Create: `frontend/src/components/GenerationStage.tsx`
- Modify: `frontend/src/api.ts`

**Interfaces:**
- Consumes: prompt history, generation history, preflight, local callbacks.
- Produces: `WorkflowStage='generation'` and controlled `GenerationStage`.

- [ ] **Step 1: Extend the workflow test first**

```ts
it('includes generation as the sixth independent stage', () => {
  const stage: WorkflowStage = 'generation'
  expect(stage).toBe('generation')
})
```

- [ ] **Step 2: Run the workflow test**

```powershell
npm.cmd test -- workspaceDomain.test.ts
```

Expected: TypeScript/Vitest FAIL because the union excludes `generation`.

- [ ] **Step 3: Extend the union and rail without changing existing items**

```ts
export type WorkflowStage = 'materials' | 'analysis' | 'timeline' | 'shots' | 'prompt' | 'generation'
```

Append to `items`:

```ts
{ id: 'generation', number: '06', label: '生成与结果', note: '提交、恢复、播放与下载' }
```

- [ ] **Step 4: Create an explicit controlled component contract**

```ts
type Props = {
  prompts: PromptRevisionSummary[]
  generations: GenerationSummary[]
  selectedPromptVersion: number
  provider: 'volcengine' | 'comfly'
  generateAudio: boolean
  includeBackground: boolean
  preflight: Record<string, { ready: boolean; model: string; endpoint: string }> | null
  submitting: boolean
  pollError: string
  uploadItems: string[]
  onPromptVersion: (version: number) => void
  onProvider: (provider: 'volcengine' | 'comfly') => void
  onGenerateAudio: (value: boolean) => void
  onIncludeBackground: (value: boolean) => void
  onSubmit: () => void
  onRetry: (generationId: string) => void
  onResolve: (generationId: string, action: 'attach_task' | 'confirm_not_created', taskId?: string) => void
  onOpenPrompt: () => void
  onOpenSettings: () => void
}
```

The component renders: heading; missing-prompt/config banners; prompt selector and expandable text; provider cards; audio/background choices; non-toggle person/product references derived from the selected prompt/project; temporary upload list; one primary submit button; 320px active-task/player aside; version-descending history rows.

For `submission_uncertain`, render an input for supplier task ID plus “附加任务 ID” and a confirmation-protected “确认供应商未创建” action. For `completed`, use:

```tsx
<video controls preload="metadata" src={generationVideoUrl(current.local_video_url)} />
<a className="button secondary" href={generationVideoUrl(current.local_video_url)} download>下载 MP4</a>
```

- [ ] **Step 5: Build the new component before App integration**

```powershell
npm.cmd run build
```

Expected: component and API types compile; obsolete App imports may still fail until Task 5, but no error may point inside `GenerationStage.tsx`.

- [ ] **Step 6: Commit API and view skeleton only when the build is green after temporary direct fixture removal**

```powershell
git add frontend/src/api.ts frontend/src/workspaceDomain.ts frontend/src/workspaceDomain.test.ts frontend/src/components/WorkflowRail.tsx frontend/src/components/GenerationStage.tsx
git commit -m "feat: add generation workflow view"
```

### Task 4: Remove obsolete product replacement controls from step five

**Files:**
- Modify: `frontend/src/components/PromptStage.tsx`
- Modify: `frontend/src/App.tsx`
- Modify: `frontend/src/workflow.ts`
- Modify: `frontend/src/workflow.test.ts`

**Interfaces:**
- Consumes: immutable `MODE` and person replacement state.
- Produces: fifth step that cannot contradict project mode.

- [ ] **Step 1: Add a pure mode assertion**

Add to `workspaceDomain.test.ts`:

```ts
it('derives product replacement only from project mode', () => {
  expect(productReplacementForMode('preserve_product')).toBe(false)
  expect(productReplacementForMode('replace_product')).toBe(true)
})
```

- [ ] **Step 2: Implement the mode helper and run it**

```ts
export function productReplacementForMode(mode: ProjectMode) {
  return mode === 'replace_product'
}
```

```powershell
npm.cmd test -- workspaceDomain.test.ts
```

Expected: PASS.

- [ ] **Step 3: Simplify `PromptStage` props and markup**

Delete `replaceProduct`, `onReplaceProduct`, `targetProduct`, all target-product callbacks, the preserve-mode target product `AssetCard`, and the “替换产品” checkbox. Keep the replace-mode locked banner and person reference/checkbox.

In `App.tsx`, delete `replaceProduct` state and `uploadTargetProducts()`. Submit prompts with:

```ts
replace_product: productReplacementForMode(MODE),
```

Use `materials.product` as the optional original-product profile in preserve mode and as the required target profile in replace mode. Never read `materials.target_product` for a new prompt.

- [ ] **Step 4: Delete obsolete pre-generation workflow helper**

`runGenerationPreparation()` represents the removed UI flow that manually publishes the video. Delete `frontend/src/workflow.ts` and `frontend/src/workflow.test.ts`; no caller should remain.

- [ ] **Step 5: Run frontend tests/build**

```powershell
npm.cmd test
npm.cmd run build
```

Expected: PASS.

- [ ] **Step 6: Commit strict step-five UI**

```powershell
git add frontend/src/components/PromptStage.tsx frontend/src/App.tsx frontend/src/workspaceDomain.ts frontend/src/workspaceDomain.test.ts frontend/src/workflow.ts frontend/src/workflow.test.ts
git commit -m "fix: remove product replacement from preserve mode"
```

### Task 5: Connect App state, actions and independent polling

**Files:**
- Modify: `frontend/src/App.tsx`
- Modify: `frontend/src/components/GenerationStage.tsx`
- Modify: `frontend/src/generationDomain.test.ts`

**Interfaces:**
- Consumes: Task 2 API functions and Task 3 controlled view.
- Produces: project-scoped generation recovery without resetting current editing state.

- [ ] **Step 1: Add local state with exact defaults**

```ts
const [promptRevisions, setPromptRevisions] = useState<PromptRevisionSummary[]>([])
const [generations, setGenerations] = useState<GenerationSummary[]>([])
const [selectedPromptVersion, setSelectedPromptVersion] = useState(0)
const [generationProvider, setGenerationProvider] = useState<'volcengine' | 'comfly'>('volcengine')
const [generateAudio, setGenerateAudio] = useState(false)
const [includeBackground, setIncludeBackground] = useState(false)
const [generationSubmitting, setGenerationSubmitting] = useState(false)
const [generationPollError, setGenerationPollError] = useState('')
```

- [ ] **Step 2: Add a non-destructive workspace refresh**

```ts
const refreshGenerationWorkspace = useCallback(async (id: string) => {
  const [nextPrompts, nextGenerations] = await Promise.all([
    listPromptRevisions(id),
    listGenerations(id),
  ])
  setPromptRevisions(nextPrompts)
  setGenerations(sortGenerations(nextGenerations))
  setSelectedPromptVersion(current =>
    nextPrompts.some(item => item.version === current) ? current : nextPrompts[0]?.version || 0
  )
  return nextGenerations
}, [])
```

Call it after project restoration, prompt task completion, prompt manual save, prompt refinement completion, create, retry, and resolve. On a new project reset only generation state plus existing project state.

- [ ] **Step 3: Add an isolated generation polling effect**

```ts
useEffect(() => {
  if (!projectId || !generations.some(item => isActiveGeneration(item.status))) return
  let consecutiveFailures = 0
  const timer = window.setInterval(() => {
    void listGenerations(projectId).then(items => {
      consecutiveFailures = 0
      setGenerationPollError('')
      setGenerations(sortGenerations(items))
    }).catch(error => {
      consecutiveFailures += 1
      if (consecutiveFailures >= 2) setGenerationPollError(errorMessage(error))
    })
  }, 3000)
  return () => window.clearInterval(timer)
}, [projectId, generations.map(item => `${item.id}:${item.status}`).join('|')])
```

The effect must not call `restoreProject()` and must stop when no `queued`, `processing`, or `retryable` task remains.

- [ ] **Step 4: Implement submit/retry/resolve actions**

Submit derives `include_person_reference` from the selected prompt's `replace_person`; it never reads a free checkbox:

```ts
await createGeneration(projectId, {
  provider: generationProvider,
  prompt_version: selectedPromptVersion,
  generate_audio: generateAudio,
  include_person_reference: Boolean(selectedPrompt?.replace_person),
  include_background_reference: includeBackground,
})
```

All actions set the global notice on success/failure, refresh only generation data, and use `finally` to clear submitting state.

- [ ] **Step 5: Unlock and mount the sixth step**

Append `generation` to `unlocked` only when `promptRevisions.length > 0`. Mount `GenerationStage` after `PromptStage`, pass the existing Settings dialog opener, and make the post-save prompt call offer `setStage('generation')` without automatically navigating during editing.

- [ ] **Step 6: Run the complete frontend logic gate**

```powershell
npm.cmd test
npm.cmd run lint
npm.cmd run build
```

Expected: PASS.

- [ ] **Step 7: Commit App integration**

```powershell
git add frontend/src/App.tsx frontend/src/components/GenerationStage.tsx frontend/src/generationDomain.test.ts
git commit -m "feat: restore and control generation tasks"
```

### Task 6: Add generation layout without redesigning the page

**Files:**
- Modify: `frontend/src/App.css`
- Modify: `frontend/src/components/GenerationStage.tsx`
- Test: browser at `/preserve-product` and `/replace-product`

**Interfaces:**
- Consumes: existing CSS variables and `.button`, `.status-badge`, `.stage-heading` classes.
- Produces: responsive `.generation-*` classes scoped to the sixth step.

- [ ] **Step 1: Add only scoped layout rules**

```css
.generation-layout { display: grid; grid-template-columns: minmax(0, 1fr) 320px; border-top: 1px solid var(--line); }
.generation-main { min-width: 0; padding: 26px 30px 30px; }
.generation-aside { min-width: 0; padding: 22px; border-left: 1px solid var(--line); background: var(--surface-soft); }
.generation-section { padding: 20px 0; border-bottom: 1px solid var(--line); }
.generation-options { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 10px; }
.generation-upload-list, .generation-history { display: grid; gap: 9px; }
.generation-player { width: 100%; aspect-ratio: 9 / 16; max-height: 520px; border-radius: 10px; background: #0e100d; }
.generation-history-row { display: grid; grid-template-columns: 64px 90px 90px minmax(120px, 1fr) auto; gap: 12px; align-items: center; padding: 12px 0; border-bottom: 1px solid var(--line); }
```

Under the existing `@media (max-width: 920px)` add single-column layout and remove the left border. Under `680px`, make options and history rows single-column.

- [ ] **Step 2: Run static frontend gates**

```powershell
npm.cmd test
npm.cmd run lint
npm.cmd run build
```

Expected: PASS.

- [ ] **Step 3: Inspect the actual app in the browser at desktop width**

Verify at 1440×900:

- top header and page mode tabs are unchanged;
- left rail remains 224px and shows 01—06;
- first five steps retain their existing layout;
- sixth step uses one white `stage-content` card, main column and 320px aside;
- no horizontal scroll;
- empty, processing, uncertain, failed, completed and missing-local-file states are visually distinguishable.

- [ ] **Step 4: Inspect responsive widths**

Verify 920×900 and 390×844: rail collapse behavior remains current; generation columns stack; buttons remain tappable; prompt text and task IDs wrap instead of overflowing.

- [ ] **Step 5: Commit scoped layout changes**

```powershell
git add frontend/src/App.css frontend/src/components/GenerationStage.tsx
git commit -m "style: fit generation results into current workspace"
```
