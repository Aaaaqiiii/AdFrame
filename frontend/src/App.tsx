import { useCallback, useEffect, useRef, useState } from 'react'
import './App.css'
import {
  analyzeReferenceImage,
  createProject,
  createPrompt,
  getPreflight,
  getLatestShotAISummary,
  getProject,
  getReferenceProfile,
  listAnalysisJobs,
  listPromptRevisions,
  listProjects,
  mediaUrl,
  referenceImageAssetUrl,
  referenceImageUrl,
  refinePrompt,
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
import type { AnalysisJob, AssetKind, ConnectionCheck, GenerationSegmentInput, GenerationSegmentPlan, Project, ProjectDetails, PromptRevisionSummary, SettingsSaveResult, Timeline, TimelineShot } from './api'
import { autoPlanGenerationSegments, getGenerationSegments, saveGenerationSegments } from './api'
import { AppHeader } from './components/AppHeader'
import { AnalysisStage } from './components/AnalysisStage'
import { MaterialsStage } from './components/MaterialsStage'
import type { MaterialState } from './components/MaterialsStage'
import type { ProductImageUpload } from './components/ProductReferenceCard'
import { ProjectDrawer, SettingsDialog } from './components/Overlays'
import { PromptStage } from './components/PromptStage'
import { ShotWorkspace } from './components/ShotWorkspace'
import type { EditDraft } from './components/ShotWorkspace'
import { GenerationSegmentsEditor } from './components/GenerationSegmentsEditor'
import { TimelineEditor } from './components/TimelineEditor'
import { WorkflowRail } from './components/WorkflowRail'
import { inferProductProfile } from './productProfile'
import { restoreProjectId, saveProjectId } from './projectSession'
import { shotIndexAtTime } from './timelineScrubbing'
import { modeFromPath, modePath, preserveShotSelection, shouldRefreshProjectDuringPolling } from './workspaceDomain'
import { choosePromptRevision, latestPromptJob, promptTaskFromJobs } from './promptWorkflow'
import { localId } from './runtime'
import type { WorkflowStage } from './workspaceDomain'

const MODE = modeFromPath(window.location.pathname)
const EMPTY_MATERIAL = (): MaterialState => ({ filename: '', previewUrl: '', images: [], status: 'pending', profile: '', error: '' })
const EMPTY_EDIT: EditDraft = {
  people: '', action: '', product: '', productInteraction: '', background: '', camera: '',
  lighting: '', visualStyle: '', visibleText: '', uncertainties: '', keep: '', confirmed: false,
}
const terminal = (status?: string | null) => status === 'completed' || status === 'succeeded' || status === 'failed'
const success = (status?: string | null) => status === 'completed' || status === 'succeeded'

function errorMessage(error: unknown) {
  if (error instanceof Error) return error.message
  return '请求失败，请稍后重试'
}

function draftFromShot(shot?: TimelineShot): EditDraft {
  // 人工版本优先；首次进入时用 GPT 综合事实填充一份可编辑副本。
  return {
    people: shot?.edit?.people || shot?.people || '',
    action: shot?.edit?.action || shot?.action || '',
    product: shot?.edit?.product || shot?.product || '',
    productInteraction: shot?.edit?.product_interaction || shot?.product_interaction || '',
    background: shot?.edit?.background || shot?.background || '',
    camera: shot?.edit?.camera || shot?.camera || '',
    lighting: shot?.edit?.lighting || shot?.lighting || '',
    visualStyle: shot?.edit?.visual_style || shot?.visual_style || '',
    visibleText: shot?.edit?.visible_text || shot?.on_screen_text || '',
    uncertainties: shot?.edit?.uncertainties || shot?.uncertainties || '',
    keep: shot?.edit?.keep_unchanged.join('\n') || shot?.keep_unchanged || '',
    confirmed: Boolean(shot?.edit?.confirmed),
  }
}

function timelineRanges(shots: TimelineShot[]) {
  return shots.map(({ start_sec, end_sec }) => ({ start_sec, end_sec }))
}

function App() {
  const [stage, setStage] = useState<WorkflowStage>('materials')
  const [projectId, setProjectId] = useState(() => restoreProjectId(localStorage, MODE) || '')
  const [projectName, setProjectName] = useState('')
  const [projects, setProjects] = useState<Project[]>([])
  const [projectDrawer, setProjectDrawer] = useState(false)
  const [settingsOpen, setSettingsOpen] = useState(false)
  const [notice, setNotice] = useState('上传参考视频后，系统会先理解完整内容，再让你校正镜头。')
  const [video, setVideo] = useState({ filename: '', previewUrl: '', uploading: false })
  const [materials, setMaterials] = useState<Record<AssetKind, MaterialState>>({ product: EMPTY_MATERIAL(), target_product: EMPTY_MATERIAL(), person: EMPTY_MATERIAL(), background: EMPTY_MATERIAL() })
  const [productIdentity, setProductIdentity] = useState({ name: '', sellingPoints: '', confirmed: false })
  const [timeline, setTimeline] = useState<Timeline | null>(null)
  const [aiRevisionId, setAiRevisionId] = useState('')
  const [timelineDirty, setTimelineDirty] = useState(false)
  const [timelineConfirmed, setTimelineConfirmed] = useState(false)
  const [timelineSaving, setTimelineSaving] = useState(false)
  const [generationPlan, setGenerationPlan] = useState<GenerationSegmentPlan | null>(null)
  const [segmentBusy, setSegmentBusy] = useState(false)
  const [selectedSegmentId, setSelectedSegmentId] = useState<string | null>(null)
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
  const [promptDirection, setPromptDirection] = useState('保持原视频的镜头时长、动作节奏、构图、运镜与原BGM。')
  const [promptRefinement, setPromptRefinement] = useState('')
  const [replacePerson, setReplacePerson] = useState(false)
  const [promptTask, setPromptTask] = useState<'generate' | 'refine' | null>(null)
  const [promptVersion, setPromptVersion] = useState(0)
  const [promptVersions, setPromptVersions] = useState<PromptRevisionSummary[]>([])
  const [preflight, setPreflight] = useState<Record<string, { ready: boolean; model: string; endpoint: string }> | null>(null)
  const [connectionChecks, setConnectionChecks] = useState<Record<string, ConnectionCheck>>({})
  const [volcengineKey, setVolcengineKey] = useState('')
  const [comflyKey, setComflyKey] = useState('')
  const videoRef = useRef<HTMLVideoElement>(null)

  const shots = timeline?.shots || []
  const selectedShot = shots.find((shot) => shot.id === selectedShotId)
  const promptProduct = materials.product
  const productProfile = promptProduct.profile.trim() || inferProductProfile(shots.map((shot) => ({ productInteraction: shot.product_interaction, observations: shot.observations })))
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

  const restorePromptVersions = useCallback(async (id: string, preferredVersion?: number, preferLatest = false) => {
    const versions = await listPromptRevisions(id)
    setPromptVersions(versions)
    const selected = choosePromptRevision(versions, preferLatest ? undefined : preferredVersion)
    setPromptVersion(selected?.version ?? 0)
    setPromptText(selected?.text ?? '')
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
      product: { ...current.product, filename: project.product_reference_image_name || '', previewUrl: project.product_reference_image_name ? referenceImageUrl(project.id, 'product') : '', images: (project.product_reference_images || []).map((item) => ({ id: item.id, filename: item.filename, previewUrl: referenceImageAssetUrl(item.image_url), viewLabel: item.view_label || 'other', displayName: item.display_name || '', note: item.note || '' })), status: project.product_analysis_status || 'pending', profile: project.product_profile || '', error: project.product_analysis_error || '' },
      target_product: { ...current.target_product, filename: project.target_product_reference_image_name || '', previewUrl: project.target_product_reference_image_name ? referenceImageUrl(project.id, 'target_product') : '', images: (project.target_product_reference_images || []).map((item) => ({ id: item.id, filename: item.filename, previewUrl: referenceImageAssetUrl(item.image_url), viewLabel: item.view_label || 'other', displayName: item.display_name || '', note: item.note || '' })), status: project.target_product_analysis_status || 'pending', profile: project.target_product_profile || '', error: project.target_product_analysis_error || '' },
      person: { ...current.person, filename: project.person_reference_image_name || '', previewUrl: project.person_reference_image_name ? referenceImageUrl(project.id, 'person') : '', images: project.person_reference_image_name ? [{ filename: project.person_reference_image_name, previewUrl: referenceImageUrl(project.id, 'person'), viewLabel: 'other', displayName: '', note: '' }] : [], status: project.person_analysis_status || 'pending', profile: project.person_profile || '', error: project.person_analysis_error || '' },
      background: { ...current.background, filename: project.background_reference_image_name || '', previewUrl: project.background_reference_image_name ? referenceImageUrl(project.id, 'background') : '', images: project.background_reference_image_name ? [{ filename: project.background_reference_image_name, previewUrl: referenceImageUrl(project.id, 'background'), viewLabel: 'other', displayName: '', note: '' }] : [] },
    }))
    setProductIdentity({ name: project.product_name || '', sellingPoints: project.product_selling_points || '', confirmed: Boolean(project.product_profile_confirmed) })
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
    await restorePromptVersions(project.id, undefined, true)
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
        const promptJobKind = effectivePromptTask === 'generate' ? 'final_prompt_generation' : 'prompt_refinement'
        const promptJob = effectivePromptTask ? current.find((job) => job.kind === promptJobKind) : undefined
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
          setNotice(success(promptJob.status) ? 'GPT 提示词新版本已生成并恢复到编辑框。' : `GPT 提示词任务失败：${promptJob.error_message || '未知错误'}`)
        } else if (effectivePromptTask && promptJob && (promptJob.attempts || 0) > 0) {
          setNotice(`GPT 正在补全缺失镜头，已自动重试 ${promptJob.attempts} 次…`)
        }
        for (const kind of ['product', 'target_product', 'person', 'background'] as AssetKind[]) {
          if (materials[kind].filename && !terminal(materials[kind].status)) {
            const profile = await getReferenceProfile(projectId, kind)
            setMaterials((old) => ({ ...old, [kind]: { ...old[kind], status: profile.status, profile: profile.profile || '', error: profile.error || '' } }))
            if (kind === 'product') setProductIdentity((current) => ({
              name: String(profile.structure?.product_name || current.name),
              sellingPoints: String(profile.structure?.selling_points || current.sellingPoints),
              confirmed: Boolean(profile.structure?.summary_confirmed),
            }))
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

  async function ensureProject(name = '未命名参考广告') {
    if (projectId) return projectId
    const created = await createProject(name, MODE)
    setProjectId(created.id)
    setProjectName(created.name)
    saveProjectId(localStorage, created.id, MODE)
    return created.id
  }

  async function newProject() {
    const created = await createProject(`参考广告 ${new Date().toLocaleString('zh-CN', { hour12: false })}`, MODE)
    saveProjectId(localStorage, created.id, MODE)
    setProjectId(created.id)
    setProjectName(created.name)
    setVideo({ filename: '', previewUrl: '', uploading: false })
    setMaterials({ product: EMPTY_MATERIAL(), target_product: EMPTY_MATERIAL(), person: EMPTY_MATERIAL(), background: EMPTY_MATERIAL() })
    setProductIdentity({ name: '', sellingPoints: '', confirmed: false })
    setTimeline(null)
    setAiRevisionId('')
    setJobs([])
    setGlobalStatus('pending')
    setTimelineConfirmed(false)
    setPromptText('')
    setPromptRefinement('')
    setPromptVersion(0)
    setPromptVersions([])
    setStage('materials')
    setNotice('新项目已创建，请上传参考视频。')
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
      setJobs([])
      setGlobalStatus('pending')
      setTimelineConfirmed(false)
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
      const image = { id: result.asset_id, filename: file.name, previewUrl, viewLabel: 'other', displayName: '', note: '' }
      setMaterials((old) => ({ ...old, [kind]: { filename: file.name, previewUrl, images: append ? [...old[kind].images, image] : [image], status: result.analysis_status || result.status || 'queued', profile: '', error: '' } }))
      setNotice(`${kind === 'product' ? '原产品' : kind === 'target_product' ? '目标产品' : kind === 'person' ? '人物' : '背景'}图片已上传，AI 正在生成可编辑文字档案。`)
      return true
    } catch (error) { setNotice(`图片上传失败：${errorMessage(error)}`); return false }
  }

  async function uploadProducts(items: ProductImageUpload[], productName: string, sellingPoints: string) {
    if (!productName) { setNotice('请先填写产品名称。'); return false }
    let id = ''
    try { id = await ensureProject() }
    catch (error) { setNotice(`无法创建产品项目：${errorMessage(error)}`); return false }
    let uploaded = 0
    for (const item of items) {
      try {
        const result = await uploadReferenceImage(id, 'product', item.file, false, { view_label: item.viewLabel, display_name: item.displayName.trim(), note: item.note, product_name: productName, selling_points: sellingPoints })
        const previewUrl = URL.createObjectURL(item.file)
        setMaterials((old) => ({ ...old, product: { ...old.product, filename: item.file.name, previewUrl, images: [...old.product.images, { id: result.asset_id, filename: item.file.name, previewUrl, viewLabel: item.viewLabel, displayName: item.displayName.trim(), note: item.note }], status: result.analysis_status || result.status || 'queued', profile: '', error: '' } }))
        uploaded += 1
      } catch (error) { setNotice(`产品图“${item.file.name}”上传失败：${errorMessage(error)}`) }
    }
    if (uploaded) {
      setProductIdentity({ name: productName, sellingPoints, confirmed: false })
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
      setMaterials((old) => ({ ...old, [kind]: { ...old[kind], status: result.status, error: '' } }))
      setNotice('已重新提交图片理解任务。')
    } catch (error) { setNotice(`重试失败：${errorMessage(error)}`) }
  }

  async function saveProfile(kind: AssetKind) {
    if (!materials[kind].profile.trim()) return
    try {
      const id = projectId || await ensureProject()
      const result = await updateReferenceProfile(id, kind, materials[kind].profile.trim())
      setMaterials((old) => ({ ...old, [kind]: { ...old[kind], status: result.status, profile: result.profile || '', error: '' } }))
      setNotice('人工校对后的文字档案已保存。')
    } catch (error) { setNotice(`保存档案失败：${errorMessage(error)}`) }
  }

  async function saveProductProfile(confirmed: boolean) {
    if (!materials.product.profile.trim() || !productIdentity.name.trim()) return
    try {
      const id = projectId || await ensureProject()
      const result = await updateReferenceProfile(id, 'product', materials.product.profile.trim(), {
        product_name: productIdentity.name.trim(),
        selling_points: productIdentity.sellingPoints.trim(),
        summary_confirmed: confirmed,
      })
      setMaterials((old) => ({ ...old, product: { ...old.product, status: result.status, profile: result.profile || '', error: '' } }))
      setProductIdentity((current) => ({ ...current, confirmed }))
      setNotice(confirmed ? '目标产品档案已确认，后续提示词将只使用这份产品事实。' : '产品事实的人工修改已保存。')
    } catch (error) { setNotice(`保存产品档案失败：${errorMessage(error)}`) }
  }

  async function startGlobalFlow() {
    if (!projectId || !video.filename) return
    try {
      setNotice('正在用 FFmpeg 提取精确候选切点和证据帧…')
      const candidates = await startAnalysis(projectId)
      setTimelineState({ ...candidates, source: 'ffmpeg_candidates' })
      // 用户先确认分镜边界；确认前不启动豆包或 GPT 理解。
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
      })
      setTimeline((current) => current ? { ...current, shots: current.shots.map((shot) => shot.id === savingShotId ? { ...shot, edit: saved } : shot) } : current)
      setShotEdit((current) => ({ ...current, confirmed }))
      setNotice(confirmed ? '当前镜头事实已确认。' : '当前镜头修改已保存。')
    } catch (error) { setNotice(`保存镜头失败：${errorMessage(error)}`) }
    finally { setShotSaving(false) }
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

  async function savePrompt(useAi = false) {
    if (!projectId) return 0
    if (!useAi && !promptText.trim()) { setNotice('当前没有可保存的提示词。'); return 0 }
    setPromptTask(useAi ? 'generate' : null)
    try {
      const saved = await createPrompt(projectId, {
        product_profile: productProfile,
        visual_direction: useAi ? promptDirection : promptText,
        audio_mode: 'keep_original',
        audio_style: '',
        replace_product: MODE === 'replace_product',
        replace_person: replacePerson,
        use_ai: useAi,
      })
      setPromptVersion(saved.version)
      if (saved.text) setPromptText(saved.text)
      setPromptTask(useAi && (saved.status === 'queued' || saved.status === 'processing') ? 'generate' : null)
      if (!useAi) await restorePromptVersions(projectId, saved.version)
      setNotice(useAi ? `GPT 提示词任务 v${saved.version} 已进入后台，完成后页面会自动显示。` : `人工提示词已保存为 v${saved.version}。`)
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
  const pageTwoProductReady = success(materials.product.status) && Boolean(materials.product.profile.trim()) && productIdentity.confirmed
  const materialsReady = Boolean(video.filename && !video.uploading && (MODE === 'preserve_product' || pageTwoProductReady))
  const materialsBlockingReason = !video.filename ? '请先上传参考视频。' : !productIdentity.name.trim() ? '请填写产品名称。' : !materials.product.images.length ? '请上传目标产品图片。' : !materials.product.profile.trim() ? '请等待产品事实生成。' : !productIdentity.confirmed ? '请检查并确认产品事实。' : ''

  return <main className="workflow-app">
    <AppHeader mode={MODE} projectName={projectName} onNewProject={() => void newProject()} onOpenHistory={() => void openHistory()} onOpenSettings={() => setSettingsOpen(true)} />
    <div className="workflow-shell">
      <WorkflowRail active={stage} unlocked={unlocked} onSelect={setStage} />
      <div className="stage-host">
        {stage === 'materials' && <MaterialsStage mode={MODE} video={video} materials={materials} productIdentity={productIdentity} onVideo={(file) => void uploadVideo(file)} onImage={(kind, file) => void uploadImage(kind, file)} onProductImages={uploadProducts} onProductImageRename={renameProductImage} onProductName={(name) => setProductIdentity((current) => ({ ...current, name, confirmed: false }))} onProductSellingPoints={(sellingPoints) => setProductIdentity((current) => ({ ...current, sellingPoints, confirmed: false }))} onProductProfileSave={(confirmed) => void saveProductProfile(confirmed)} onRetry={(kind) => void retryProfile(kind)} onProfile={(kind, profile) => { setMaterials((old) => ({ ...old, [kind]: { ...old[kind], profile } })); if (kind === 'product') setProductIdentity((current) => ({ ...current, confirmed: false })) }} onSaveProfile={(kind) => void saveProfile(kind)} onContinue={() => void startGlobalFlow()} ready={materialsReady} blockingReason={materialsBlockingReason} />}
        {stage === 'analysis' && <AnalysisStage shots={shots} jobs={jobs} onOpenShots={() => { if (!projectId) return; void restoreProject(projectId).then(() => setStage('shots')).catch((error) => setNotice(`读取分镜事实失败：${errorMessage(error)}`)) }} />}
        {stage === 'timeline' && timeline && <TimelineEditor videoUrl={video.previewUrl} videoRatio={videoRatio} videoRef={videoRef} shots={shots} selectedIds={selectedIds} selectedBoundary={selectedBoundary} playhead={playhead} fps={fps} dirty={timelineDirty} saving={timelineSaving} canContinue={timelineConfirmed} onMetadata={(width, height) => { setVideoRatio(`${width} / ${height}`); const element = videoRef.current; if (element && Number.isFinite(element.duration) && element.duration > 0) setFps(25) }} onPlayhead={seekTimeline} onSelectShot={selectTimelineShot} onSelectBoundary={setSelectedBoundary} onMoveBoundary={moveTimelineBoundary} onSplit={splitAtPlayhead} onMerge={mergeSelected} onRestore={() => void restoreAi()} onSave={() => void saveHumanTimeline()} onContinue={() => void beginShotAnalysis()} />}
        {stage === 'segments' && timeline && generationPlan && <GenerationSegmentsEditor projectId={projectId} planVersion={generationPlan.plan_version} timelineRevisionId={generationPlan.timeline_revision_id} segments={generationPlan.segments} shots={timeline.shots} durationSec={videoRef.current?.duration || shots[shots.length - 1]?.end_sec || 0} maxSegmentSeconds={generationPlan.max_segment_seconds} recommendedMinSeconds={generationPlan.recommended_min_seconds} busy={segmentBusy} selectedSegmentId={selectedSegmentId} onSelectSegment={setSelectedSegmentId} onAutoPlan={() => void autoPlanSegments()} onSave={(inputs) => void saveSegmentPlan(inputs)} onRestoreAuto={() => { if (!timelineConfirmed) return; void autoPlanSegments() }} />}
        {stage === 'shots' && <ShotWorkspace shots={shots} selectedId={selectedShotId} jobs={jobs} edit={shotEdit} saving={shotSaving} mode={MODE} compatibility={null} onSelect={setSelectedShotId} onEdit={setShotEdit} onSave={(confirmed) => void saveCurrentShot(confirmed)} onAdoptAI={(id) => void adoptLatestAI(id)} onRetry={(id) => {
          // 重跑只产生新的 AI 总结，人工保存版本始终保留。
          if (!window.confirm('重新理解将产生一个新的 AI 总结版本，当前人工版本会继续保留。是否继续？')) return
          void retryShotAnalysis(projectId, id).then(refreshJobs).catch((error) => setNotice(`重试失败：${errorMessage(error)}`))
        }} onOpenPrompt={() => setStage('prompt')} />}
        {stage === 'prompt' && <PromptStage mode={MODE} prompt={promptText} direction={promptDirection} refinement={promptRefinement} promptVersion={promptVersion} versions={promptVersions} selectedVersion={promptVersion} taskStatus={latestPromptJob(jobs)} personReady={success(materials.person.status) && Boolean(materials.person.profile.trim())} replacePerson={replacePerson} busy={promptTask} blockingReason={!timelineConfirmed ? '人工时间轴尚未确认。' : !allShotJobsDone ? '逐镜理解尚未全部完成。' : !shots.every((shot) => shot.edit?.confirmed) ? '请先确认每个镜头的最终事实。' : ''} onPrompt={setPromptText} onDirection={setPromptDirection} onRefinement={setPromptRefinement} onReplacePerson={setReplacePerson} onSelectVersion={(version) => { const selected = choosePromptRevision(promptVersions, version); setPromptVersion(selected?.version ?? 0); setPromptText(selected?.text ?? '') }} onSave={() => void savePrompt(false)} onGenerate={() => void savePrompt(true)} onRefine={() => void refineCurrentPrompt()} person={materials.person} onPerson={(file) => void uploadImage('person', file)} onPersonRetry={() => void retryProfile('person')} onPersonProfile={(profile) => setMaterials((old) => ({ ...old, person: { ...old.person, profile } }))} onPersonProfileSave={() => void saveProfile('person')} />}
      </div>
    </div>
    <div className="global-notice" role="status"><i />{notice}</div>
      <ProjectDrawer open={projectDrawer} projects={projects} onClose={() => setProjectDrawer(false)} onOpen={(id) => { setProjectDrawer(false); void restoreProject(id).then((project: ProjectDetails) => setNotice(`已打开项目“${project.name}”。`)).catch((error) => setNotice(errorMessage(error))) }} />
    <SettingsDialog open={settingsOpen} preflight={preflight} checks={connectionChecks} volcengineKey={volcengineKey} comflyKey={comflyKey} onClose={() => setSettingsOpen(false)} onPreflight={() => void getPreflight().then((value) => { setPreflight(value); setNotice('服务配置状态已刷新。') }).catch((error) => setNotice(errorMessage(error)))} onTest={(service) => void testConnection(service)} onVolcengineKey={setVolcengineKey} onComflyKey={setComflyKey} onSaveVolcengine={() => void saveVolcengineApiKey(volcengineKey.trim()).then((result) => finishSettingsSave(result, () => setVolcengineKey(''))).catch((error) => setNotice(errorMessage(error)))} onSaveComfly={() => void saveComflyApiKey(comflyKey.trim()).then((result) => finishSettingsSave(result, () => setComflyKey(''))).catch((error) => setNotice(errorMessage(error)))} />
  </main>
}

export default App
