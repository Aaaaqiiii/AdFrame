import { useCallback, useEffect, useRef, useState } from 'react'
import './App.css'
import {
  analyzeReferenceImage,
  cancelPromptJob,
  createProject,
  createPrompt,
  deleteReferenceImage,
  deleteProject,
  getPreflight,
  getLatestShotAISummary,
  getProject,
  getReferenceProfile,
  listAnalysisJobs,
  listPromptRevisions,
  listProjects,
  mergeTimelineShots,
  mediaUrl,
  referenceImageAssetUrl,
  referenceImageUrl,
  refinePrompt,
  renameProject,
  requestShotVisionAnalysis,
  restoreAiTimeline,
  retryShotAnalysis,
  saveComflyApiKey,
  saveVolcengineApiKey,
  saveShotEdit,
  saveTimeline,
  testServiceConnection,
  startAnalysis,
  updateReferenceProfile,
  updateProductReferenceImage,
  uploadReferenceImage,
  uploadReferenceVideo,
} from './api'
import { ApiError } from './api'
import type { AnalysisJob, AssetKind, ConnectionCheck, GenerationBatch, GenerationSegmentInput, GenerationSegmentPlan, Project, ProjectDetails, PromptRevisionSummary, SettingsSaveResult, Timeline, TimelineShot } from './api'
import { autoPlanGenerationSegments, createGenerationBatch, getGenerationBatch, getGenerationSegments, listGenerationBatches, optimizePromptSellingPoints, resolveGeneration, retryGeneration, saveGenerationSegments } from './api'
import { AppHeader } from './components/AppHeader'
import { AnalysisStage } from './components/AnalysisStage'
import { MaterialsStage } from './components/MaterialsStage'
import type { MaterialState } from './components/MaterialsStage'
import type { ProductImageUpload } from './components/ProductReferenceCard'
import { ProjectDrawer, SettingsDialog } from './components/Overlays'
import { PromptStage } from './components/PromptStage'
import { ShotWorkspace } from './components/ShotWorkspace'
import { draftFromShot } from './shotDraft'
import type { EditDraft } from './shotDraft'
import { GenerationSegmentsEditor } from './components/GenerationSegmentsEditor'
import { GenerationStage } from './components/GenerationStage'
import { TimelineEditor } from './components/TimelineEditor'
import { WorkflowRail } from './components/WorkflowRail'
import { restoreProjectId, restoreWorkflowStage, saveProjectId, saveWorkflowStage } from './projectSession'
import { shotIndexAtTime } from './timelineScrubbing'
import { modeFromPath, modePath, preserveShotSelection, shouldRefreshProjectDuringPolling } from './workspaceDomain'
import { choosePromptRevision, latestPromptJob, promptJobKind, promptRetryNotice, promptTaskFromJobs } from './promptWorkflow'
import { chooseRestoredGenerationBatch } from './generationDomain'
import { localId } from './runtime'
import type { WorkflowStage } from './workspaceDomain'

const MODE = modeFromPath(window.location.pathname)
const DEFAULT_PROMPT_DIRECTION = '保持原视频的镜头时长、动作节奏、构图与运镜；未明确修改的事实保持不变。'
const EMPTY_MATERIAL = (): MaterialState => ({ filename: '', previewUrl: '', images: [], status: 'pending', profile: '', error: '' })
const EMPTY_EDIT: EditDraft = {
  people: '', action: '', product: '', productInteraction: '', background: '', camera: '',
  lighting: '', visualStyle: '', visibleText: '', uncertainties: '', keep: '', confirmed: false,
}
const terminal = (status?: string | null) => status === 'completed' || status === 'succeeded' || status === 'failed' || status === 'cancelled'
const success = (status?: string | null) => status === 'completed' || status === 'succeeded'
const audioMode = (value?: string): 'none' | 'auto' | 'custom' => value === 'auto' ? 'auto' : value === 'custom' || value === 'add_style' ? 'custom' : 'none'

function errorMessage(error: unknown) {
  if (error instanceof Error) return error.message
  return '请求失败，请稍后重试'
}

function timelineRanges(shots: TimelineShot[]) {
  return shots.map(({ start_sec, end_sec }) => ({ start_sec, end_sec }))
}

function App() {
  const restoredStage = useRef(restoreWorkflowStage(localStorage, MODE))
  const [stage, setStage] = useState<WorkflowStage>(() => restoredStage.current || 'materials')
  const [projectId, setProjectId] = useState(() => restoreProjectId(localStorage, MODE) || '')
  const [projectName, setProjectName] = useState('')
  const [projects, setProjects] = useState<Project[]>([])
  const [projectDrawer, setProjectDrawer] = useState(false)
  const [deletingProjectId, setDeletingProjectId] = useState<string | null>(null)
  const [deletingAssetKind, setDeletingAssetKind] = useState<AssetKind | null>(null)
  const [settingsOpen, setSettingsOpen] = useState(false)
  const [notice, setNotice] = useState('上传参考视频后，系统会先理解完整内容，再让你校正镜头。')
  const [video, setVideo] = useState({ filename: '', previewUrl: '', uploading: false })
  const [materials, setMaterials] = useState<Record<AssetKind, MaterialState>>({ product: EMPTY_MATERIAL(), target_product: EMPTY_MATERIAL(), person: EMPTY_MATERIAL(), background: EMPTY_MATERIAL() })
  const [productIdentity, setProductIdentity] = useState({ name: '', category: '', packageForm: '', sellingPoints: '', confirmed: false })
  const [timeline, setTimeline] = useState<Timeline | null>(null)
  const [aiRevisionId, setAiRevisionId] = useState('')
  const [timelineDirty, setTimelineDirty] = useState(false)
  const [timelineConfirmed, setTimelineConfirmed] = useState(false)
  const [timelineSaving, setTimelineSaving] = useState(false)
  const [generationPlan, setGenerationPlan] = useState<GenerationSegmentPlan | null>(null)
  const [segmentBusy, setSegmentBusy] = useState(false)
  const [selectedSegmentId, setSelectedSegmentId] = useState<string | null>(null)
  const [selectedProvider, setSelectedProvider] = useState<'volcengine' | 'comfly'>('volcengine')
  const [generationRatio, setGenerationRatio] = useState<'adaptive' | '16:9' | '4:3' | '1:1' | '3:4' | '9:16' | '21:9'>('adaptive')
  const [optimizeBusy, setOptimizeBusy] = useState(false)
  const [generationBusy, setGenerationBusy] = useState(false)
  const [selectedBatch, setSelectedBatch] = useState<GenerationBatch | null>(null)
  const [retryingGenerationId, setRetryingGenerationId] = useState<string | null>(null)
  const [selectedIds, setSelectedIds] = useState<string[]>([])
  const [selectedBoundary, setSelectedBoundary] = useState<number | null>(null)
  const [playhead, setPlayhead] = useState(0)
  const [fps, setFps] = useState(25)
  const [videoRatio, setVideoRatio] = useState('16 / 9')
  const [jobs, setJobs] = useState<AnalysisJob[]>([])
  const [globalStatus, setGlobalStatus] = useState('pending')
  const [selectedShotId, setSelectedShotId] = useState('')
  const [shotSaving, setShotSaving] = useState(false)
  const [shotEdit, setShotEdit] = useState<EditDraft>({ ...EMPTY_EDIT })
  const [promptText, setPromptText] = useState('')
  const [promptDirty, setPromptDirty] = useState(false)
  const [promptDirection, setPromptDirection] = useState(DEFAULT_PROMPT_DIRECTION)
  const [promptRefinement, setPromptRefinement] = useState('')
  const [replacePerson, setReplacePerson] = useState(false)
  const [promptTask, setPromptTask] = useState<'generate' | 'refine' | 'optimize' | null>(null)
  const [cancellingPromptTask, setCancellingPromptTask] = useState(false)
  const [promptVersion, setPromptVersion] = useState(0)
  const [promptMode, setPromptMode] = useState<'full_reference_video_edit' | 'standalone_video_recreation'>('full_reference_video_edit')
  const [recreationAudioMode, setRecreationAudioMode] = useState<'none' | 'auto' | 'custom'>('none')
  const [recreationAudioRequirement, setRecreationAudioRequirement] = useState('')
  const [promptVersions, setPromptVersions] = useState<PromptRevisionSummary[]>([])
  const [preflight, setPreflight] = useState<Record<string, { ready: boolean; model: string; endpoint: string }> | null>(null)
  const [connectionChecks, setConnectionChecks] = useState<Record<string, ConnectionCheck>>({})
  const [volcengineKey, setVolcengineKey] = useState('')
  const [comflyKey, setComflyKey] = useState('')
  const videoRef = useRef<HTMLVideoElement>(null)

  const shots = timeline?.shots || []
  const selectedShot = shots.find((shot) => shot.id === selectedShotId)
  const allShotJobsDone = shots.length > 0 && shots.every((shot) => success(shot.analysis_status) || success(jobs.find((job) => job.shot_id === shot.id)?.status))

  const setTimelineState = useCallback((value: Timeline, confirmed = false) => {
    setTimeline(value)
    setSelectedShotId(value.shots[0]?.id || '')
    setSelectedIds(value.shots[0] ? [value.shots[0].id] : [])
    setSelectedBoundary(null)
    setTimelineDirty(false)
    setTimelineConfirmed(confirmed)
    // 时间轴变化后旧分段方案失效，直到加载新方案。
    setGenerationPlan(null)
  }, [])

  const restorePromptVersions = useCallback(async (id: string, preferredVersion?: number, preferLatest = false, currentTimelineRevisionId?: string) => {
    const versions = await listPromptRevisions(id)
    setPromptVersions(versions)
    const currentVersions = currentTimelineRevisionId
      ? versions.filter((item) => item.source_timeline_revision_id === currentTimelineRevisionId)
      : versions
    const selected = choosePromptRevision(currentVersions, preferLatest ? undefined : preferredVersion)
    if (selected?.prompt_mode === 'full_reference_video_edit' || selected?.prompt_mode === 'standalone_video_recreation') setPromptMode(selected.prompt_mode)
    if (selected) {
      setReplacePerson(selected.replace_person)
      setRecreationAudioMode(audioMode(selected.audio_mode))
      setRecreationAudioRequirement(selected.audio_style || '')
    }
    setPromptVersion(selected?.version ?? 0)
    setPromptText(selected?.text ?? '')
    setPromptDirty(false)
    return selected
  }, [])

  const restoreProject = useCallback(async (id: string) => {
    const project = await getProject(id)
    if ((project.mode || MODE) !== MODE) throw new Error('该项目属于替换产品流程，请从页面一新建项目。')
    setProjectId(project.id)
    saveProjectId(localStorage, project.id, MODE)
    setProjectName(project.name)
    setVideo({ filename: project.reference_video_name || '', previewUrl: project.reference_video_url ? mediaUrl(project.reference_video_url) : '', uploading: false })
    setMaterials((current) => ({
      product: { ...current.product, filename: project.product_reference_image_name || '', previewUrl: project.product_reference_image_name ? referenceImageUrl(project.id, 'product') : '', images: (project.product_reference_images || []).map((item) => ({ id: item.id, filename: item.filename, previewUrl: referenceImageAssetUrl(item.image_url), viewLabel: item.view_label || 'other', displayName: item.display_name || '', note: item.note || '', status: item.status })), status: project.product_analysis_status || 'pending', profile: project.product_profile || '', error: project.product_analysis_error || '' },
      target_product: { ...current.target_product, filename: project.target_product_reference_image_name || '', previewUrl: project.target_product_reference_image_name ? referenceImageUrl(project.id, 'target_product') : '', images: (project.target_product_reference_images || []).map((item) => ({ id: item.id, filename: item.filename, previewUrl: referenceImageAssetUrl(item.image_url), viewLabel: item.view_label || 'other', displayName: item.display_name || '', note: item.note || '', status: item.status })), status: project.target_product_analysis_status || 'pending', profile: project.target_product_profile || '', error: project.target_product_analysis_error || '' },
      person: { ...current.person, filename: project.person_reference_image_name || '', previewUrl: project.person_reference_image_name ? referenceImageUrl(project.id, 'person') : '', images: project.person_reference_image_name ? [{ filename: project.person_reference_image_name, previewUrl: referenceImageUrl(project.id, 'person'), viewLabel: 'other', displayName: '', note: '' }] : [], status: project.person_analysis_status || 'pending', profile: project.person_profile || '', error: project.person_analysis_error || '' },
      background: { ...current.background, filename: project.background_reference_image_name || '', previewUrl: project.background_reference_image_name ? referenceImageUrl(project.id, 'background') : '', images: project.background_reference_image_name ? [{ filename: project.background_reference_image_name, previewUrl: referenceImageUrl(project.id, 'background'), viewLabel: 'other', displayName: '', note: '' }] : [] },
    }))
    setProductIdentity({ name: project.product_name || '', category: project.product_category || '', packageForm: project.product_package_form || '', sellingPoints: project.product_selling_points || '', confirmed: Boolean(project.product_profile_confirmed) })
    if (project.prompt_visual_direction) setPromptDirection(project.prompt_visual_direction)
    setReplacePerson(Boolean(project.prompt_replace_person))
    if (project.timeline) {
      setTimeline(project.timeline)
      setSelectedShotId((current) => preserveShotSelection(current, project.timeline!.shots))
      setSelectedIds((current) => {
        const preserved = current.filter((id) => project.timeline!.shots.some((shot) => shot.id === id))
        return preserved.length ? preserved : project.timeline!.shots[0] ? [project.timeline!.shots[0].id] : []
      })
      if (project.timeline.source === 'vision_hybrid' || project.timeline.source === 'ai') setAiRevisionId(project.timeline.revision_id)
      setTimelineConfirmed(project.timeline.source === 'human' || project.timeline.source === 'ai_restored')
      // restore 顺序：timeline 设置后立即 GET 当前分段方案，有方案时选择第一段。
      try {
        const plan = await getGenerationSegments(project.id)
        setGenerationPlan(plan)
        setSelectedSegmentId(plan.segments[0]?.id ?? null)
      } catch { setGenerationPlan(null); setSelectedSegmentId(null) }
    } else {
      setTimeline(null)
      setTimelineConfirmed(false)
      setGenerationPlan(null)
      setSelectedSegmentId(null)
    }
    const restoredPrompt = project.timeline
      ? await restorePromptVersions(project.id, undefined, true, project.timeline.revision_id)
      : undefined
    if (!project.timeline) {
      setPromptVersions([])
      setPromptVersion(0)
      setPromptText('')
    }
    // restore 顺序最后：批次列表，选最新批次。
    let restoredBatch: GenerationBatch | null = null
    if (restoredPrompt) {
      try {
        const batches = await listGenerationBatches(project.id)
        restoredBatch = chooseRestoredGenerationBatch(batches, restoredPrompt.version)
      } catch { /* 无批次或批次接口错误时保留空状态 */ }
    }
    setSelectedBatch(restoredBatch)
    if (!restoredStage.current) {
      const restoredShots = project.timeline?.shots || []
      const resume: WorkflowStage = restoredBatch
        ? 'generation'
        : restoredPrompt || (restoredShots.length > 0 && restoredShots.every((shot) => shot.edit?.confirmed))
          ? 'prompt'
          : restoredShots.some((shot) => success(shot.analysis_status))
            ? 'shots'
            : project.timeline
              ? 'timeline'
              : 'materials'
      restoredStage.current = resume
      setStage(resume)
    }
    return project
  }, [restorePromptVersions])

  useEffect(() => {
    if (!window.location.pathname.startsWith(modePath(MODE))) window.history.replaceState(null, '', modePath(MODE))
    if (!projectId) return
    restoreProject(projectId).catch((error) => {
      saveProjectId(localStorage, '', MODE)
      setProjectId('')
      setNotice(`无法恢复项目：${errorMessage(error)}`)
    })
  }, [projectId, restoreProject])

  useEffect(() => {
    if (restoredStage.current) saveWorkflowStage(localStorage, stage, MODE)
  }, [stage])

  const refreshJobs = useCallback(async () => {
    if (!projectId) return []
    const next = await listAnalysisJobs(projectId)
    setJobs(next)
    const recoveredTask = promptTaskFromJobs(next)
    if (recoveredTask) setPromptTask(recoveredTask)
    const global = next.find((job) => job.kind === 'vision_analysis')
    if (global) {
      setGlobalStatus(global.status)
    }
    return next
  }, [projectId])

  useEffect(() => {
    if (!projectId) return
    void refreshJobs().catch(() => undefined)
    const timer = window.setInterval(async () => {
      try {
        const current = await refreshJobs()
        const global = current.find((job) => job.kind === 'vision_analysis')
        const effectivePromptTask = promptTask ?? promptTaskFromJobs(current)
        const expectedPromptJobKind = effectivePromptTask ? promptJobKind(effectivePromptTask) : undefined
        const promptJob = expectedPromptJobKind ? current.find((job) => job.kind === expectedPromptJobKind) : undefined
        if (shouldRefreshProjectDuringPolling({
          globalStatus: global?.status,
          timelineSource: timeline?.source,
          timelineDirty,
          hasActiveShotJobs: current.some((job) => job.kind === 'vision_shot_analysis' && !terminal(job.status)),
          stage,
        })) {
          const project = await restoreProject(projectId)
          if (success(global?.status) && project.timeline?.source === 'vision_hybrid') setAiRevisionId(project.timeline.revision_id)
        }
        if (effectivePromptTask && promptJob && terminal(promptJob.status)) {
          await restoreProject(projectId)
          setPromptTask(null)
          if (success(promptJob.status) && promptJob.kind === 'prompt_refinement') setPromptRefinement('')
          setNotice(success(promptJob.status) ? 'GPT 提示词新版本已生成并恢复到编辑框。' : promptJob.status === 'cancelled' ? 'GPT 提示词任务已取消。' : `GPT 提示词任务失败：${promptJob.error_message || '未知错误'}`)
        } else if (effectivePromptTask && promptJob && (promptJob.attempts || 0) > 0) {
          setNotice(promptRetryNotice(promptJob))
        }
        for (const kind of ['product', 'target_product', 'person', 'background'] as AssetKind[]) {
          if (materials[kind].filename && !terminal(materials[kind].status)) {
            const profile = await getReferenceProfile(projectId, kind)
            setMaterials((old) => ({ ...old, [kind]: { ...old[kind], status: profile.status, profile: profile.profile || '', error: profile.error || '' } }))
            if (kind === 'product') setProductIdentity((current) => ({
              name: String(profile.structure?.product_name || current.name),
              category: String(profile.structure?.product_category || current.category),
              packageForm: String(profile.structure?.package_form || current.packageForm),
              sellingPoints: String(profile.structure?.selling_points || current.sellingPoints),
              confirmed: Boolean(profile.structure?.summary_confirmed),
            }))
            if (kind === 'product' && terminal(profile.status)) {
              const project = await getProject(projectId)
              setMaterials((old) => ({ ...old, product: {
                ...old.product,
                images: (project.product_reference_images || []).map((item) => ({ id: item.id, filename: item.filename, previewUrl: referenceImageAssetUrl(item.image_url), viewLabel: item.view_label || 'other', displayName: item.display_name || '', note: item.note || '', status: item.status })),
              } }))
            }
          }
        }
      } catch { /* A short service interruption is surfaced by the next explicit action. */ }
    }, 3000)
    return () => window.clearInterval(timer)
  }, [materials, projectId, promptTask, refreshJobs, restoreProject, stage, timeline?.source, timelineDirty])

  useEffect(() => { setShotEdit(draftFromShot(selectedShot)) }, [selectedShot])

  useEffect(() => {
    if (success(globalStatus) && timeline?.source === 'vision_hybrid') setAiRevisionId(timeline.revision_id)
  }, [globalStatus, timeline])

  // 批次轮询：仅批次为 queued/processing 时轮询；依赖稳定标识（batch_id + status），
  // 避免每次 setSelectedBatch 重建 timer；terminal/unmount/项目变更停止。
  const pollingBatchId = selectedBatch?.generation_batch_id ?? null
  const pollingBatchStatus = selectedBatch?.status ?? null
  useEffect(() => {
    if (!projectId || !pollingBatchId) return
    if (pollingBatchStatus !== 'queued' && pollingBatchStatus !== 'processing') return
    let cancelled = false
    let inFlight = false
    const timer = window.setInterval(async () => {
      if (inFlight) return
      inFlight = true
      try {
        const batch = await getGenerationBatch(projectId, pollingBatchId)
        if (!cancelled) setSelectedBatch(batch)
      } catch { /* 网络错误由下一次轮询或手动刷新覆盖 */ }
      finally { inFlight = false }
    }, 3000)
    return () => { cancelled = true; window.clearInterval(timer) }
  }, [projectId, pollingBatchId, pollingBatchStatus])

  async function ensureProject(name = '未命名参考广告') {
    if (projectId) return projectId
    const created = await createProject(name, MODE)
    setProjectId(created.id)
    setProjectName(created.name)
    saveProjectId(localStorage, created.id, MODE)
    return created.id
  }

  function resetWorkflowState() {
    setVideo({ filename: '', previewUrl: '', uploading: false })
    setMaterials({ product: EMPTY_MATERIAL(), target_product: EMPTY_MATERIAL(), person: EMPTY_MATERIAL(), background: EMPTY_MATERIAL() })
    setProductIdentity({ name: '', category: '', packageForm: '', sellingPoints: '', confirmed: false })
    setTimeline(null)
    setAiRevisionId('')
    setTimelineDirty(false)
    setTimelineConfirmed(false)
    setGenerationPlan(null)
    setSelectedSegmentId(null)
    setSelectedBatch(null)
    setSelectedIds([])
    setSelectedBoundary(null)
    setPlayhead(0)
    setJobs([])
    setGlobalStatus('pending')
    setSelectedShotId('')
    setShotEdit({ ...EMPTY_EDIT })
    setPromptText('')
    setPromptDirty(false)
    setPromptDirection(DEFAULT_PROMPT_DIRECTION)
    setPromptRefinement('')
    setReplacePerson(false)
    setPromptTask(null)
    setPromptVersion(0)
    setPromptMode('full_reference_video_edit')
    setRecreationAudioMode('none')
    setRecreationAudioRequirement('')
    setPromptVersions([])
    setSelectedProvider('volcengine')
    setGenerationRatio('adaptive')
    setStage('materials')
  }

  async function newProject() {
    const created = await createProject(`参考广告 ${new Date().toLocaleString('zh-CN', { hour12: false })}`, MODE)
    saveProjectId(localStorage, created.id, MODE)
    setProjectId(created.id)
    setProjectName(created.name)
    resetWorkflowState()
    setNotice('新项目已创建，请上传参考视频。')
  }

  async function removeProject(id: string, name: string) {
    if (!window.confirm(`确定永久删除项目“${name}”吗？项目记录和本地素材都会删除，无法恢复。`)) return
    setDeletingProjectId(id)
    try {
      const result = await deleteProject(id)
      setProjects((current) => current.filter((project) => project.id !== id))
      if (id === projectId) {
        saveProjectId(localStorage, '', MODE)
        setProjectId('')
        setProjectName('')
        restoredStage.current = 'materials'
        resetWorkflowState()
        setProjectDrawer(false)
      }
      setNotice(result.media_deleted ? '项目及其本地素材已删除。' : '项目已删除，但隔离区中的部分素材仍需手动清理。')
    } catch (error) {
      setNotice(`删除项目失败：${errorMessage(error)}`)
    } finally {
      setDeletingProjectId(null)
    }
  }

  async function renameSavedProject(id: string, name: string) {
    try {
      const updated = await renameProject(id, name)
      setProjects((current) => current.map((project) => project.id === id ? { ...project, name: updated.name } : project))
      if (id === projectId) setProjectName(updated.name)
      setNotice(`项目已重命名为“${updated.name}”。`)
    } catch (error) {
      setNotice(`项目重命名失败：${errorMessage(error)}`)
      throw error
    }
  }

  async function uploadVideo(file: File) {
    setVideo((old) => ({ ...old, uploading: true }))
    try {
      // 页面二允许先上传目标产品，因此上传视频时必须沿用当前项目，不能丢掉已上传产品。
      const created = MODE === 'replace_product' && projectId ? null : await createProject(file.name.replace(/\.[^.]+$/, '') || '参考广告', MODE)
      const id = created?.id || projectId
      if (!id) throw new Error('无法创建项目')
      if (created) {
        setProjectId(id)
        setProjectName(created.name)
        saveProjectId(localStorage, id, MODE)
      }
      await uploadReferenceVideo(id, file)
      setVideo({ filename: file.name, previewUrl: URL.createObjectURL(file), uploading: false })
      setTimeline(null)
      setAiRevisionId('')
      setJobs([])
      setGlobalStatus('pending')
      setTimelineConfirmed(false)
      setGenerationPlan(null)
      setSelectedSegmentId(null)
      setPromptText('')
      setPromptDirty(false)
      setPromptRefinement('')
      setPromptVersion(0)
      setPromptVersions([])
      setPromptTask(null)
      setSelectedBatch(null)
      setNotice('参考视频已保存。下一步将先提取候选切点，再理解完整视频内容。')
    } catch (error) {
      setVideo((old) => ({ ...old, uploading: false }))
      setNotice(`视频上传失败：${errorMessage(error)}`)
    }
  }

  async function uploadImage(kind: AssetKind, file: File, append = false) {
    try {
      const id = await ensureProject()
      const result = await uploadReferenceImage(id, kind, file, kind === 'person')
      const previewUrl = URL.createObjectURL(file)
      const image = { id: result.asset_id, filename: file.name, previewUrl, viewLabel: 'other', displayName: '', note: '', status: result.analysis_status || result.status || 'queued' }
      setMaterials((old) => ({ ...old, [kind]: { filename: file.name, previewUrl, images: append ? [...old[kind].images, image] : [image], status: result.analysis_status || result.status || 'queued', profile: '', error: '' } }))
      if (kind === 'person' || kind === 'background') setPromptDirty(true)
      setNotice(`${kind === 'product' ? '原产品' : kind === 'target_product' ? '目标产品' : kind === 'person' ? '人物' : '背景'}图片已上传，AI 正在生成可编辑文字档案。`)
      return true
    } catch (error) { setNotice(`图片上传失败：${errorMessage(error)}`); return false }
  }

  async function uploadProducts(items: ProductImageUpload[], productName: string, productCategory: string, packageForm: string, sellingPoints: string) {
    if (!productName) { setNotice('请先填写产品名称。'); return false }
    if (!productCategory) { setNotice('请先填写产品类别。'); return false }
    if (!packageForm) { setNotice('请先选择主包装形态。'); return false }
    let id = ''
    try { id = await ensureProject() }
    catch (error) { setNotice(`无法创建产品项目：${errorMessage(error)}`); return false }
    let uploaded = 0
    for (const item of items) {
      try {
        const result = await uploadReferenceImage(id, 'product', item.file, false, { view_label: item.viewLabel, display_name: item.displayName.trim(), note: item.note, product_name: productName, product_category: productCategory, package_form: packageForm, selling_points: sellingPoints })
        const previewUrl = URL.createObjectURL(item.file)
        setMaterials((old) => ({ ...old, product: { ...old.product, filename: item.file.name, previewUrl, images: [...old.product.images, { id: result.asset_id, filename: item.file.name, previewUrl, viewLabel: item.viewLabel, displayName: item.displayName.trim(), note: item.note, status: result.analysis_status || result.status || 'queued' }], status: 'queued', profile: '', error: '' } }))
        uploaded += 1
      } catch (error) { setNotice(`产品图“${item.file.name}”上传失败：${errorMessage(error)}`) }
    }
    if (uploaded) {
      setProductIdentity({ name: productName, category: productCategory, packageForm, sellingPoints, confirmed: false })
      setPromptDirty(true)
      try {
        const profile = await getReferenceProfile(id, 'product')
        setMaterials((old) => ({ ...old, product: { ...old.product, status: profile.status, profile: profile.profile || '', error: profile.error || '' } }))
      } catch {
        // Uploads already succeeded; keep polling their queued state if this
        // one aggregate refresh is interrupted.
      }
      setNotice(`已上传 ${uploaded} 张目标产品图，AI 正在根据角度和备注生成产品事实。`)
    }
    return uploaded === items.length
  }

  async function renameProductImage(imageId: string, viewLabel: string, displayName: string) {
    if (!projectId) return false
    try {
      const saved = await updateProductReferenceImage(projectId, 'product', imageId, { view_label: viewLabel, display_name: displayName })
      setMaterials((old) => ({ ...old, product: { ...old.product, images: old.product.images.map((image) => image.id === imageId ? { ...image, viewLabel: saved.view_label, displayName: saved.display_name } : image) } }))
      setNotice(`图片名称已保存为“${saved.display_name}”。`)
      return true
    } catch (error) { setNotice(`保存图片名称失败：${errorMessage(error)}`); return false }
  }

  async function retryProfile(kind: AssetKind) {
    if (!projectId) return
    try {
      const result = await analyzeReferenceImage(projectId, kind)
      setMaterials((old) => ({ ...old, [kind]: { ...old[kind], status: result.status, error: '', images: old[kind].images.map((image) => success(image.status) ? image : { ...image, status: result.status }) } }))
      setNotice('已重新提交图片理解任务。')
    } catch (error) { setNotice(`重试失败：${errorMessage(error)}`) }
  }

  async function saveProfile(kind: AssetKind) {
    if (!materials[kind].profile.trim()) return
    try {
      const id = projectId || await ensureProject()
      const result = await updateReferenceProfile(id, kind, materials[kind].profile.trim())
      setMaterials((old) => ({ ...old, [kind]: { ...old[kind], status: result.status, profile: result.profile || '', error: '' } }))
      if (kind === 'person' || kind === 'background') setPromptDirty(true)
      setNotice('人工校对后的文字档案已保存。')
    } catch (error) { setNotice(`保存档案失败：${errorMessage(error)}`) }
  }

  async function saveProductProfile(confirmed: boolean) {
    if (!materials.product.profile.trim() || !productIdentity.name.trim() || !productIdentity.category.trim() || !productIdentity.packageForm) return
    try {
      const id = projectId || await ensureProject()
      const result = await updateReferenceProfile(id, 'product', materials.product.profile.trim(), {
        product_name: productIdentity.name.trim(),
        product_category: productIdentity.category.trim(),
        package_form: productIdentity.packageForm,
        selling_points: productIdentity.sellingPoints.trim(),
        summary_confirmed: confirmed,
      })
      setMaterials((old) => ({ ...old, product: { ...old.product, status: result.status, profile: result.profile || '', error: result.error || '' } }))
      setProductIdentity((current) => ({ ...current, confirmed }))
      setPromptDirty(true)
      setNotice(confirmed ? '目标产品档案已确认，后续提示词将只使用这份产品事实。' : '产品事实的人工修改已保存。')
    } catch (error) { setNotice(`保存产品档案失败：${errorMessage(error)}`) }
  }

  async function startGlobalFlow() {
    if (!projectId || !video.filename) return
    try {
      setNotice('正在用 FFmpeg 提取精确候选切点和证据帧…')
      const candidates = await startAnalysis(projectId)
      setTimelineState({ ...candidates, source: 'ffmpeg_candidates' })
      // 用户先确认分镜边界；确认前不启动Qwen理解。
      setGlobalStatus('completed')
      setStage('timeline')
      setNotice('本地候选分镜已建立，请人工确认或调整边界。确认后才会开始逐镜理解。')
    } catch (error) {
      setGlobalStatus('failed')
      setNotice(`无法生成候选分镜：${errorMessage(error)}`)
    }
  }

  function selectTimelineShot(id: string, multi: boolean) {
    setSelectedIds((current) => multi ? (current.includes(id) ? current.filter((item) => item !== id) : [...current, id].slice(-2)) : [id])
  }

  async function removeReferenceImage(kind: 'person' | 'background') {
    const label = kind === 'person' ? '人物' : '背景'
    if (!window.confirm(`删除${label}参考图后，关联的 AI 文字档案也会清空。是否继续？`)) return
    setDeletingAssetKind(kind)
    try {
      if (projectId) await deleteReferenceImage(projectId, kind)
      const previewUrl = materials[kind].previewUrl
      if (previewUrl.startsWith('blob:')) URL.revokeObjectURL(previewUrl)
      setMaterials((old) => ({ ...old, [kind]: EMPTY_MATERIAL() }))
      if (kind === 'person') setReplacePerson(false)
      setPromptDirty(true)
      setNotice(`${label}参考图和文字档案已删除。请重新保存或生成提示词。`)
    } catch (error) {
      setNotice(`删除${label}参考图失败：${errorMessage(error)}`)
    } finally {
      setDeletingAssetKind(null)
    }
  }

  function selectFactShot(id: string) {
    setSelectedShotId(id)
    const shot = shots.find((item) => item.id === id)
    if (!shot) return
    setPlayhead(shot.start_sec)
    if (videoRef.current) videoRef.current.currentTime = shot.start_sec
  }

  function moveTimelineBoundary(index: number, requested: number) {
    if (!timeline) return
    const left = timeline.shots[index]
    const right = timeline.shots[index + 1]
    if (!left || !right) return
    const boundary = Math.max(left.start_sec + 1 / fps, Math.min(right.end_sec - 1 / fps, requested))
    setTimeline({ ...timeline, shots: timeline.shots.map((shot, position) => position === index ? { ...shot, end_sec: boundary } : position === index + 1 ? { ...shot, start_sec: boundary } : shot) })
    setTimelineDirty(true)
  }

  function seekTimeline(value: number) {
    setPlayhead(value)
    const index = shotIndexAtTime(shots, value)
    // 拖动播放指针时同步高亮当前镜头，避免拆分位置和选中镜头看起来不一致。
    if (index >= 0) setSelectedIds([shots[index].id])
    if (videoRef.current && Math.abs(videoRef.current.currentTime - value) > .04) videoRef.current.currentTime = value
  }

  function splitAtPlayhead(displayedTime?: number) {
    if (!timeline) return
    // 按钮显示值、播放器时间和实际拆分值使用同一时刻；播放器时间可避免拖动结束瞬间的React批量更新延迟。
    const playerTime = videoRef.current?.currentTime
    const splitTime = Number.isFinite(displayedTime) ? Number(displayedTime) : Number.isFinite(playerTime) ? Number(playerTime) : playhead
    const shot = timeline.shots.find((item) => item.start_sec < splitTime && splitTime < item.end_sec)
    if (!shot) { setNotice('请先把播放指针移到某个镜头内部，再执行拆分。'); return }
    const at = Number(splitTime.toFixed(3))
    const index = timeline.shots.indexOf(shot)
    const left = { ...shot, id: localId(), end_sec: at, analysis_status: 'pending' as const }
    const right = { ...shot, id: localId(), start_sec: at, analysis_status: 'pending' as const }
    const next = [...timeline.shots]
    next.splice(index, 1, left, right)
    setTimeline({ ...timeline, shots: next })
    setSelectedIds([left.id])
    setTimelineDirty(true)
    setTimelineConfirmed(false)
    setPlayhead(at)
    setNotice(`已在 ${at.toFixed(2)} 秒拆分镜头 ${index + 1}。保存前不会提交逐镜任务。`)
  }

  function mergeSelected() {
    if (!timeline || selectedIds.length !== 2) return
    const indexes = selectedIds.map((id) => timeline.shots.findIndex((shot) => shot.id === id)).sort((a, b) => a - b)
    if (indexes[1] - indexes[0] !== 1) { setNotice('只能合并两个相邻镜头。'); return }
    const first = timeline.shots[indexes[0]]
    const last = timeline.shots[indexes[1]]
    const merged = { ...first, id: localId(), end_sec: last.end_sec, analysis_status: 'pending' as const }
    const next = [...timeline.shots]
    next.splice(indexes[0], 2, merged)
    setTimeline({ ...timeline, shots: next })
    setSelectedIds([merged.id])
    setTimelineDirty(true)
    setTimelineConfirmed(false)
    setNotice(`已合并镜头 ${indexes[0] + 1} 和镜头 ${indexes[1] + 1}。保存前不会提交逐镜任务。`)
  }

  async function restoreAi() {
    if (!projectId || !aiRevisionId) { setNotice('当前还没有可恢复的 AI 最终时间轴。'); return }
    try {
      await restoreAiTimeline(projectId, aiRevisionId)
      const project = await getProject(projectId)
      if (project.timeline) setTimelineState(project.timeline)
      setTimelineDirty(true)
      setTimelineConfirmed(false)
      setNotice('已恢复 AI 初始划分，请人工确认后保存。')
    } catch (error) { setNotice(`恢复失败：${errorMessage(error)}`) }
  }

  async function saveHumanTimeline() {
    if (!projectId || !timeline) return
    setTimelineSaving(true)
    try {
      const result = await saveTimeline(projectId, timelineRanges(timeline.shots))
      await restoreProject(projectId)
      setTimelineConfirmed(true)
      setTimelineDirty(false)
      setNotice(`人工时间轴已保存；${result.affected_shot_ids?.length || 0} 个受影响镜头需要重新理解。`)
    } catch (error) { setNotice(`保存时间轴失败：${errorMessage(error)}`) }
    finally { setTimelineSaving(false) }
  }

  async function autoPlanSegments() {
    if (!projectId) return
    setSegmentBusy(true)
    try {
      const plan = await autoPlanGenerationSegments(projectId)
      setGenerationPlan(plan)
      setSelectedSegmentId(plan.segments[0]?.id ?? null)
      setNotice(plan.segments.length ? `已自动规划 ${plan.segments.length} 个生成片段。` : '自动规划未产生片段。')
    } catch (error) { setNotice(`自动规划失败：${errorMessage(error)}`) }
    finally { setSegmentBusy(false) }
  }

  async function saveSegmentPlan(inputs: GenerationSegmentInput[]) {
    if (!projectId) return
    setSegmentBusy(true)
    try {
      const plan = await saveGenerationSegments(projectId, inputs)
      setGenerationPlan(plan)
      setSelectedSegmentId(plan.segments[0]?.id ?? null)
      setNotice(`分段方案已保存为 v${plan.plan_version}。`)
    } catch (error) { setNotice(`保存分段失败：${errorMessage(error)}`) }
    finally { setSegmentBusy(false) }
  }

  async function optimizeSellingPoints() {
    if (!projectId || !promptVersion) return
    setOptimizeBusy(true)
    setPromptTask('optimize')
    try {
      await optimizePromptSellingPoints(projectId, promptVersion)
      setNotice('卖点优化任务已进入后台，完成后会生成新的提示词版本。')
      await refreshJobs()
      await restorePromptVersions(projectId, promptVersion)
    } catch (error) { setPromptTask(null); setNotice(`卖点优化失败：${errorMessage(error)}`) }
    finally { setOptimizeBusy(false) }
  }

  async function createBatch() {
    if (!projectId || !generationPlan || !promptVersion) return
    setGenerationBusy(true)
    try {
      const batch = await createGenerationBatch(projectId, {
        provider: selectedProvider,
        prompt_version: promptVersion,
        ratio: generationRatio,
        generate_audio: recreationAudioMode !== 'none',
      })
      setSelectedBatch(batch)
      setStage('generation')
      setNotice(`已创建 ${batch.batch_size} 个生成任务（${batch.provider}）。`)
    } catch (error) {
      if (error instanceof ApiError && error.status === 409) {
        const existing = (await listGenerationBatches(projectId)).find((batch) => batch.status === 'queued' || batch.status === 'processing' || batch.status === 'uncertain')
        if (existing) {
          setSelectedBatch(existing)
          setStage('generation')
          setNotice('已打开正在执行的批次；未重复创建任务。')
          return
        }
      }
      setNotice(`创建批次失败：${errorMessage(error)}`)
    }
    finally { setGenerationBusy(false) }
  }

  async function refreshBatch() {
    if (!projectId || !selectedBatch) return
    try {
      const batch = await getGenerationBatch(projectId, selectedBatch.generation_batch_id)
      setSelectedBatch(batch)
    } catch (error) { setNotice(`刷新批次失败：${errorMessage(error)}`) }
  }

  async function retryPosition(generationId: string) {
    if (!projectId) return
    setRetryingGenerationId(generationId)
    try {
      await retryGeneration(projectId, generationId)
      await refreshBatch()
      setNotice('已提交本段重试。')
    } catch (error) { setNotice(`重试失败：${errorMessage(error)}`) }
    finally { setRetryingGenerationId(null) }
  }

  async function resolvePosition(generationId: string, resolution: 'submitted' | 'not_submitted', externalTaskId?: string) {
    if (!projectId) return
    try {
      await resolveGeneration(projectId, generationId, resolution === 'submitted' ? 'attach_task' : 'confirm_not_created', externalTaskId)
      await refreshBatch()
      setNotice('已提交人工解析。')
    } catch (error) { setNotice(`解析失败：${errorMessage(error)}`) }
  }

  async function beginShotAnalysis() {
    if (!projectId || !timelineConfirmed) return
    try {
      const result = await requestShotVisionAnalysis(projectId)
      await refreshJobs()
      setStage('analysis')
      setNotice(result.queued_shots
        ? `已提交 ${result.queued_shots} 个待处理镜头；已排除 ${result.skipped_succeeded} 个成功镜头。`
        : result.skipped_succeeded
          ? `没有需要重跑的镜头，已保留 ${result.skipped_succeeded} 个成功结果。`
          : '逐镜理解任务已存在，正在恢复任务状态。')
    } catch (error) { setNotice(`无法开始逐镜理解：${errorMessage(error)}`) }
  }

  async function saveCurrentShot(confirmed = false) {
    if (!projectId || !selectedShotId) return
    const savingShotId = selectedShotId
    setShotSaving(true)
    try {
      const saved = await saveShotEdit(projectId, savingShotId, {
        people: shotEdit.people,
        action: shotEdit.action,
        product: shotEdit.product,
        product_interaction: shotEdit.productInteraction,
        background: shotEdit.background,
        camera: shotEdit.camera,
        lighting: shotEdit.lighting,
        visual_style: shotEdit.visualStyle,
        visible_text: shotEdit.visibleText,
        uncertainties: shotEdit.uncertainties,
        keep_unchanged: shotEdit.keep.split('\n').map((item) => item.trim()).filter(Boolean),
        confirmed,
      }, selectedShot?.edit?.version ?? 0)
      setTimeline((current) => current ? { ...current, shots: current.shots.map((shot) => shot.id === savingShotId ? { ...shot, edit: saved } : shot) } : current)
      setShotEdit((current) => ({ ...current, confirmed }))
      setNotice(confirmed ? '当前镜头事实已确认。' : '当前镜头修改已保存。')
    } catch (error) { setNotice(`保存镜头失败：${errorMessage(error)}`) }
    finally { setShotSaving(false) }
  }

  async function deleteCurrentShot(shotId: string) {
    if (!projectId || !timeline || timeline.shots.length < 2) return
    const index = timeline.shots.findIndex((shot) => shot.id === shotId)
    if (index < 0) return
    const adjacentIndex = index === 0 ? 1 : index - 1
    const adjacent = timeline.shots[adjacentIndex]
    const shot = timeline.shots[index]
    const duration = Math.max(0, shot.end_sec - shot.start_sec).toFixed(2)
    const message = `删除镜头 ${index + 1} 后，这 ${duration} 秒画面会并入镜头 ${adjacentIndex + 1}，合并后的镜头将重新理解。其他已确认镜头不受影响。是否继续？`
    if (!window.confirm(message)) return

    setShotSaving(true)
    try {
      const merged = await mergeTimelineShots(projectId, timeline.revision_id, [shot.id, adjacent.id])
      await restoreProject(projectId)
      setSelectedShotId(merged.affected_shot_ids?.[0] || merged.shots[Math.min(index, merged.shots.length - 1)]?.id || '')
      try {
        const queued = await requestShotVisionAnalysis(projectId)
        await refreshJobs()
        setNotice(queued.queued_shots
          ? `镜头 ${index + 1} 已删除并合入相邻镜头，合并后的镜头正在重新理解。`
          : `镜头 ${index + 1} 已删除并合入相邻镜头。`)
      } catch (error) {
        setNotice(`镜头 ${index + 1} 已删除，但重新理解任务提交失败：${errorMessage(error)}`)
      }
    } catch (error) {
      setNotice(`删除镜头失败：${errorMessage(error)}`)
    } finally {
      setShotSaving(false)
    }
  }

  async function adoptLatestAI(shotId: string) {
    if (!projectId) return
    try {
      const result = await getLatestShotAISummary(projectId, shotId)
      if (!window.confirm('采用新的AI总结会替换当前编辑框内容，但不会删除已保存的历史人工版本。是否采用？')) return
      const value = result.content
      setShotEdit({
        people: value.people || '', action: value.action || '', product: value.product || '',
        productInteraction: value.product_interaction || '', background: value.background || '', camera: value.camera || '',
        lighting: value.lighting || '', visualStyle: value.visual_style || '', visibleText: value.visible_text || '',
        keep: value.keep_unchanged || '', uncertainties: value.uncertainties || '', confirmed: false,
      })
      setNotice(`已载入AI总结 v${result.version}，请校对后保存或确认。`)
    } catch (error) { setNotice(`读取新AI总结失败：${errorMessage(error)}`) }
  }

  function selectPromptMode(value: 'full_reference_video_edit' | 'standalone_video_recreation') {
    setPromptMode(value)
    const selected = promptVersions.find((item) => item.prompt_mode === value)
    setPromptVersion(selected?.version ?? 0)
    setPromptText(selected?.text ?? '')
    setPromptDirection(selected?.visual_direction || DEFAULT_PROMPT_DIRECTION)
    if (selected) setReplacePerson(selected.replace_person)
    setRecreationAudioMode(audioMode(selected?.audio_mode))
    setRecreationAudioRequirement(selected?.audio_style || '')
    setPromptDirty(false)
  }

  function selectPromptVersion(version: number) {
    const selected = choosePromptRevision(promptVersions, version)
    setPromptVersion(selected?.version ?? 0)
    setPromptText(selected?.text ?? '')
    setPromptDirection(selected?.visual_direction || DEFAULT_PROMPT_DIRECTION)
    if (selected) setReplacePerson(selected.replace_person)
    setRecreationAudioMode(audioMode(selected?.audio_mode))
    setRecreationAudioRequirement(selected?.audio_style || '')
    setPromptDirty(false)
  }

  async function savePrompt(useAi = false) {
    if (!projectId) return 0
    if (!useAi && !promptText.trim()) { setNotice('当前没有可保存的提示词。'); return 0 }
    setPromptTask(useAi ? 'generate' : null)
    try {
      const saved = await createPrompt(projectId, {
        visual_direction: promptDirection,
        prompt_text: useAi ? undefined : promptText,
        audio_mode: recreationAudioMode,
        audio_style: recreationAudioMode === 'custom' ? recreationAudioRequirement.trim() : '',
        replace_product: MODE === 'replace_product',
        replace_person: replacePerson,
        use_ai: useAi,
        prompt_mode: promptMode,
      })
      if (!useAi) {
        setPromptVersion(saved.version)
        if (saved.text) setPromptText(saved.text)
        setPromptDirty(false)
      }
      setPromptTask(useAi && (saved.status === 'queued' || saved.status === 'processing') ? 'generate' : null)
      if (!useAi) await restorePromptVersions(projectId, saved.version)
      const adaptationNotice = saved.adaptation_count ? ` 检测到 ${saved.adaptation_count} 个产品交互冲突，AI只会对这些镜头做最小动作适配。` : ''
      setNotice(useAi ? `GPT 提示词任务 v${saved.version} 已进入后台，完成后页面会自动显示。${adaptationNotice}` : `人工提示词已保存为 v${saved.version}。`)
      return saved.version
    } catch (error) { setPromptTask(null); setNotice(`${useAi ? '生成' : '保存'}提示词失败：${errorMessage(error)}`); return 0 }
  }

  async function refineCurrentPrompt() {
    if (!projectId || !promptText.trim() || !promptRefinement.trim()) return
    setPromptTask('refine')
    try {
      const saved = await refinePrompt(projectId, promptRefinement.trim(), promptVersion || undefined)
      setPromptVersion(saved.version)
      setNotice(`GPT-5.6 修改任务 v${saved.version} 已进入后台，当前提示词版本会继续保留。`)
    } catch (error) { setPromptTask(null); setNotice(`修改提示词失败：${errorMessage(error)}`) }
  }

  async function cancelCurrentPromptTask() {
    if (!projectId) return
    const active = latestPromptJob(jobs)
    if (!active || !['queued', 'uploaded', 'running', 'processing', 'retryable'].includes(active.status)) return
    setCancellingPromptTask(true)
    try {
      await cancelPromptJob(projectId, active.job_id)
      setPromptTask(null)
      await refreshJobs()
      setNotice('GPT 提示词任务已取消；供应商若稍后返回结果，系统也不会写入。')
    } catch (error) {
      setNotice(`取消 GPT 提示词任务失败：${errorMessage(error)}`)
    } finally {
      setCancellingPromptTask(false)
    }
  }

  async function copyCurrentPrompt() {
    if (!promptText.trim()) return
    try {
      await navigator.clipboard.writeText(promptText)
      setNotice('独立复刻提示词已复制到剪贴板。')
    } catch {
      setNotice('复制失败，请在提示词编辑框中全选后手动复制。')
    }
  }

  async function openHistory() {
    try { setProjects((await listProjects(MODE)).filter((project) => !project.mode || project.mode === MODE)); setProjectDrawer(true) }
    catch (error) { setNotice(`读取历史项目失败：${errorMessage(error)}`) }
  }

  async function finishSettingsSave(result: SettingsSaveResult, clear: () => void) {
    clear()
    const service = result.service || (comflyKey ? 'comfly_prompt' : volcengineKey ? 'volcengine_generation' : 'volcengine_vision')
    const connection = result.connection || await testServiceConnection(service)
    setConnectionChecks((old) => ({ ...old, [service]: connection }))
    setPreflight(await getPreflight())
    setNotice(connection.connected ? `配置已保存并立即生效：${connection.message}` : `配置已保存，但连通测试失败：${connection.message}`)
  }

  async function testConnection(service: string) {
    try {
      const result = await testServiceConnection(service)
      setConnectionChecks((old) => ({ ...old, [service]: result }))
      setNotice(result.connected ? result.message : `连通测试失败：${result.message}`)
    } catch (error) { setNotice(`连通测试失败：${errorMessage(error)}`) }
  }

  const unlocked: WorkflowStage[] = ['materials']
  if (video.filename && shots.length) unlocked.push('timeline')
  if (timelineConfirmed) unlocked.push('segments')
  if (timelineConfirmed) unlocked.push('analysis')
  if (allShotJobsDone) unlocked.push('shots')
  if (shots.length && shots.every((shot) => shot.edit?.confirmed)) unlocked.push('prompt')
  if (selectedBatch) unlocked.push('generation')
  const pageTwoProductReady = success(materials.product.status) && Boolean(materials.product.profile.trim()) && Boolean(productIdentity.name.trim()) && Boolean(productIdentity.category.trim()) && Boolean(productIdentity.packageForm) && productIdentity.confirmed
  const materialsReady = Boolean(video.filename && !video.uploading && (MODE === 'preserve_product' || pageTwoProductReady))
  const materialsBlockingReason = !video.filename ? '请先上传参考视频。' : !productIdentity.name.trim() ? '请填写产品名称。' : !productIdentity.category.trim() ? '请填写产品类别。' : !productIdentity.packageForm ? '请选择主包装形态。' : !materials.product.images.length ? '请上传目标产品图片。' : !materials.product.profile.trim() ? '请等待产品事实生成。' : !productIdentity.confirmed ? '请检查并确认产品事实。' : ''

  return <main className="workflow-app">
    <AppHeader mode={MODE} projectName={projectName} onNewProject={() => void newProject()} onOpenHistory={() => void openHistory()} onOpenSettings={() => setSettingsOpen(true)} />
    <div className="workflow-shell">
      <WorkflowRail active={stage} unlocked={unlocked} onSelect={setStage} />
      <div className="stage-host">
        {stage === 'materials' && <MaterialsStage mode={MODE} video={video} materials={materials} productIdentity={productIdentity} onVideo={(file) => void uploadVideo(file)} onImage={(kind, file) => void uploadImage(kind, file)} onProductImages={uploadProducts} onProductImageRename={renameProductImage} onProductName={(name) => setProductIdentity((current) => ({ ...current, name, confirmed: false }))} onProductCategory={(category) => setProductIdentity((current) => ({ ...current, category, confirmed: false }))} onProductPackageForm={(packageForm) => setProductIdentity((current) => ({ ...current, packageForm, confirmed: false }))} onProductSellingPoints={(sellingPoints) => setProductIdentity((current) => ({ ...current, sellingPoints, confirmed: false }))} onProductProfileSave={(confirmed) => void saveProductProfile(confirmed)} onRetry={(kind) => void retryProfile(kind)} deletingKind={deletingAssetKind} onDelete={(kind) => void removeReferenceImage(kind)} onProfile={(kind, profile) => { setMaterials((old) => ({ ...old, [kind]: { ...old[kind], profile } })); if (kind === 'product') setProductIdentity((current) => ({ ...current, confirmed: false })) }} onSaveProfile={(kind) => void saveProfile(kind)} onContinue={() => void startGlobalFlow()} ready={materialsReady} blockingReason={materialsBlockingReason} />}
        {stage === 'analysis' && <AnalysisStage shots={shots} jobs={jobs} onOpenShots={() => { if (!projectId) return; void restoreProject(projectId).then(() => setStage('shots')).catch((error) => setNotice(`读取分镜事实失败：${errorMessage(error)}`)) }} />}
        {stage === 'timeline' && timeline && <TimelineEditor videoUrl={video.previewUrl} videoRatio={videoRatio} videoRef={videoRef} shots={shots} selectedIds={selectedIds} selectedBoundary={selectedBoundary} playhead={playhead} fps={fps} dirty={timelineDirty} saving={timelineSaving} canContinue={timelineConfirmed} onMetadata={(width, height) => { setVideoRatio(`${width} / ${height}`); const element = videoRef.current; if (element && Number.isFinite(element.duration) && element.duration > 0) setFps(25) }} onPlayhead={seekTimeline} onSelectShot={selectTimelineShot} onSelectBoundary={setSelectedBoundary} onMoveBoundary={moveTimelineBoundary} onSplit={splitAtPlayhead} onMerge={mergeSelected} onRestore={() => void restoreAi()} onSave={() => void saveHumanTimeline()} onContinue={() => void beginShotAnalysis()} />}
        {stage === 'segments' && timeline && generationPlan && <GenerationSegmentsEditor projectId={projectId} planVersion={generationPlan.plan_version} timelineRevisionId={generationPlan.timeline_revision_id} segments={generationPlan.segments} shots={timeline.shots} durationSec={videoRef.current?.duration || shots[shots.length - 1]?.end_sec || 0} maxSegmentSeconds={generationPlan.max_segment_seconds} recommendedMinSeconds={generationPlan.recommended_min_seconds} busy={segmentBusy} selectedSegmentId={selectedSegmentId} onSelectSegment={setSelectedSegmentId} onAutoPlan={() => void autoPlanSegments()} onSave={(inputs) => void saveSegmentPlan(inputs)} onRestoreAuto={() => { if (!timelineConfirmed) return; void autoPlanSegments() }} />}
        {stage === 'generation' && <GenerationStage batch={selectedBatch} retryingGenerationId={retryingGenerationId} onRetry={retryPosition} onResolve={resolvePosition} onRefresh={() => refreshBatch()} onBackToPrompt={() => setStage('prompt')} />}
        {stage === 'shots' && <ShotWorkspace shots={shots} selectedId={selectedShotId} jobs={jobs} edit={shotEdit} saving={shotSaving} mode={MODE} compatibility={null} onSelect={selectFactShot} onEdit={setShotEdit} onSave={(confirmed) => void saveCurrentShot(confirmed)} onDelete={(id) => void deleteCurrentShot(id)} onAdoptAI={(id) => void adoptLatestAI(id)} onRetry={(id) => {
          // 重跑只产生新的 AI 总结，人工保存版本始终保留。
          if (!window.confirm('重新理解将产生一个新的 AI 总结版本，当前人工版本会继续保留。是否继续？')) return
          void retryShotAnalysis(projectId, id).then(refreshJobs).catch((error) => setNotice(`重试失败：${errorMessage(error)}`))
        }} onOpenPrompt={() => setStage('prompt')} />}
        {stage === 'prompt' && <PromptStage mode={MODE} promptMode={promptMode} onPromptMode={selectPromptMode} recreationAudioMode={recreationAudioMode} recreationAudioRequirement={recreationAudioRequirement} prompt={promptText} promptDirty={promptDirty} direction={promptDirection} refinement={promptRefinement} promptVersion={promptVersion} versions={promptVersions} selectedVersion={promptVersion} currentTimelineRevisionId={timeline?.revision_id} taskStatus={latestPromptJob(jobs)} personReady={success(materials.person.status) && Boolean(materials.person.profile.trim())} replacePerson={replacePerson} backgroundReady={Boolean(materials.background.filename)} busy={promptTask} blockingReason={!timelineConfirmed ? '人工时间轴尚未确认。' : !allShotJobsDone ? '逐镜理解尚未全部完成。' : !shots.every((shot) => shot.edit?.confirmed) ? '请先确认每个镜头的最终事实。' : ''} onPrompt={(value) => { setPromptText(value); setPromptDirty(true) }} onDirection={(value) => { setPromptDirection(value); setPromptDirty(true) }} onRecreationAudioMode={(value) => { setRecreationAudioMode(value); setPromptDirty(true) }} onRecreationAudioRequirement={(value) => { setRecreationAudioRequirement(value); setPromptDirty(true) }} onCopyPrompt={() => void copyCurrentPrompt()} onRefinement={setPromptRefinement} onReplacePerson={(value) => { setReplacePerson(value); setPromptDirty(true) }} onSelectVersion={selectPromptVersion} onSave={() => void savePrompt(false)} onGenerate={() => void savePrompt(true)} onRefine={() => void refineCurrentPrompt()} onCancelPromptTask={() => void cancelCurrentPromptTask()} cancellingPromptTask={cancellingPromptTask} person={materials.person} onPerson={(file) => void uploadImage('person', file)} onPersonRetry={() => void retryProfile('person')} onPersonProfile={(profile) => { setMaterials((old) => ({ ...old, person: { ...old.person, profile } })); setPromptDirty(true) }} onPersonProfileSave={() => void saveProfile('person')} personDeleting={deletingAssetKind === 'person'} onPersonDelete={() => void removeReferenceImage('person')} generationPlan={generationPlan} selectedPromptRevision={choosePromptRevision(promptVersions, promptVersion) ?? null} selectedProvider={selectedProvider} generationRatio={generationRatio} optimizeBusy={optimizeBusy} generationBusy={generationBusy} segmentBusy={segmentBusy} onOptimizeSellingPoints={() => void optimizeSellingPoints()} onProviderChange={setSelectedProvider} onGenerationRatioChange={setGenerationRatio} onAutoPlanSegments={() => void autoPlanSegments()} onCreateBatch={() => void createBatch()} />}
      </div>
    </div>
    <div className="global-notice" role="status"><i />{notice}</div>
    <ProjectDrawer open={projectDrawer} projects={projects} deletingId={deletingProjectId} onClose={() => setProjectDrawer(false)} onOpen={(id) => { setProjectDrawer(false); void restoreProject(id).then((project: ProjectDetails) => setNotice(`已打开项目“${project.name}”。`)).catch((error) => setNotice(errorMessage(error))) }} onRename={renameSavedProject} onDelete={(id, name) => void removeProject(id, name)} />
    <SettingsDialog open={settingsOpen} preflight={preflight} checks={connectionChecks} volcengineKey={volcengineKey} comflyKey={comflyKey} onClose={() => setSettingsOpen(false)} onPreflight={() => void getPreflight().then((value) => { setPreflight(value); setNotice('服务配置状态已刷新。') }).catch((error) => setNotice(errorMessage(error)))} onTest={(service) => void testConnection(service)} onVolcengineKey={setVolcengineKey} onComflyKey={setComflyKey} onSaveVolcengine={() => void saveVolcengineApiKey(volcengineKey.trim()).then((result) => finishSettingsSave(result, () => setVolcengineKey(''))).catch((error) => setNotice(errorMessage(error)))} onSaveComfly={() => void saveComflyApiKey(comflyKey.trim()).then((result) => finishSettingsSave(result, () => setComflyKey(''))).catch((error) => setNotice(errorMessage(error)))} />
  </main>
}

export default App
