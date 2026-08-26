import type { ProjectMode } from './workspaceDomain'
export type { ProjectMode } from './workspaceDomain'

const baseUrl = import.meta.env.VITE_API_BASE_URL || 'http://localhost:8010'

export type AssetKind = 'product' | 'target_product' | 'person' | 'background'
export type AnalysisState = 'pending' | 'not_started' | 'queued' | 'uploaded' | 'running' | 'processing' | 'retryable' | 'succeeded' | 'completed' | 'failed' | 'cancelled'

export type Evidence = { timestamp_sec: number; image_url: string }
export type ShotEdit = {
  people: string
  action: string
  product: string
  product_interaction: string
  background: string
  camera: string
  lighting: string
  visual_style: string
  visible_text: string
  uncertainties: string
  keep_unchanged: string[]
  confirmed: boolean
  version?: number
  ai_summary_version?: number
}
export type TimelineShot = {
  id: string
  start_sec: number
  end_sec: number
  purpose?: string | null
  people?: string | null
  action?: string | null
  product?: string | null
  product_interaction?: string | null
  background?: string | null
  camera?: string | null
  lighting?: string | null
  visual_style?: string | null
  keep_unchanged?: string | null
  on_screen_text?: string | null
  observations?: string | null
  inferences?: string | null
  uncertainties?: string | null
  evidence?: Evidence[]
  edit?: ShotEdit | null
  analysis_status?: AnalysisState | null
  analysis_error?: string | null
  ai_summary_version?: number
  has_new_ai_summary?: boolean
}
export type Timeline = { revision_id: string; source: string; shots: TimelineShot[]; affected_shot_ids?: string[] }
export type GenerationSummary = {
  id: string
  version: number
  status: string
  provider: string
  prompt_version?: number
  generation_segment_id?: string | null
  generation_batch_id?: string | null
  batch_position?: number | null
  batch_size?: number | null
  result_url?: string | null
  local_video_url?: string | null
  external_task_id?: string | null
  attempts?: number
  error_message?: string | null
  created_at?: string
  completed_at?: string | null
}
export type BatchStatus = 'queued' | 'processing' | 'complete' | 'partial' | 'failed' | 'uncertain'
export type GenerationBatch = {
  generation_batch_id: string
  project_id: string
  provider: 'volcengine' | 'comfly'
  prompt_version: number
  batch_size: number
  status: BatchStatus
  generations: GenerationSummary[]
}

export type Project = {
  id: string
  name: string
  mode?: ProjectMode
  reference_video_name?: string | null
  reference_video_url?: string | null
  person_reference_image_name?: string | null
  person_profile?: string | null
  person_analysis_status?: AnalysisState | null
  person_analysis_error?: string | null
  background_reference_image_name?: string | null
  product_reference_image_name?: string | null
  product_reference_images?: Array<{ id: string; filename: string; image_url: string; status: AnalysisState; view_label: string; display_name: string; note: string }>
  product_profile?: string | null
  product_analysis_status?: AnalysisState | null
  product_analysis_error?: string | null
  product_name?: string
  product_category?: string
  product_package_form?: string
  product_selling_points?: string
  product_profile_confirmed?: boolean
  target_product_reference_image_name?: string | null
  target_product_reference_images?: Array<{ id: string; filename: string; image_url: string; status: AnalysisState; view_label: string; display_name: string; note: string }>
  target_product_profile?: string | null
  target_product_analysis_status?: AnalysisState | null
  target_product_analysis_error?: string | null
  latest_prompt_version?: number
  latest_prompt_text?: string
  prompt_visual_direction?: string
  prompt_audio_mode?: string
  prompt_audio_style?: string
  prompt_replace_product?: boolean
  prompt_replace_person?: boolean
}
export type ProjectDetails = Project & { timeline?: Timeline | null; generations?: GenerationSummary[] }

export type ReferenceProfile = {
  asset_id?: string | null
  status: AnalysisState
  profile: string
  editable_profile?: string | null
  structure?: Record<string, unknown> | null
  error?: string | null
}
export type AnalysisJob = {
  job_id: string
  shot_id?: string | null
  kind?: string
  status: AnalysisState
  error_message?: string | null
  attempts?: number
}
export type PromptMode = 'full_reference_video_edit' | 'standalone_video_recreation' | 'reference_video_edit' | 'full_video_description'
export type PromptRevisionSummary = {
  id: string
  version: number
  text: string
  status: AnalysisState
  prompt_mode: PromptMode
  generation_segment_id: string | null
  error_message?: string | null
  source_timeline_revision_id: string | null
  replace_product: boolean
  replace_person: boolean
  visual_direction?: string
  audio_mode?: string
  audio_style?: string
  created_at: string
}
export type CompatibilityConflict = { shot_id: string; start_sec: number; end_sec: number; reason: string; suggestion: string; severity?: 'adaptable' | 'blocked' }
export type ProductCompatibility = {
  status: 'pending' | 'compatible' | 'warning' | 'blocked'
  summary: string
  product_kind?: string | null
  conflicts: CompatibilityConflict[]
}
export type GenerationSegment = {
  id: string
  position: number
  source_start_sec: number
  source_end_sec: number
  start_boundary_type: 'video_edge' | 'shot_boundary' | 'inside_shot'
  end_boundary_type: 'video_edge' | 'shot_boundary' | 'inside_shot'
  short_segment_accepted: boolean
}
export type GenerationSegmentPlan = {
  plan_version: number
  timeline_revision_id: string | null
  max_segment_seconds: number
  recommended_min_seconds: number
  segments: GenerationSegment[]
}
export type GenerationSegmentInput = {
  source_start_sec: number
  source_end_sec: number
  start_boundary_type: 'video_edge' | 'shot_boundary' | 'inside_shot'
  end_boundary_type: 'video_edge' | 'shot_boundary' | 'inside_shot'
  short_segment_accepted: boolean
}
export type ConnectionCheck = { connected: boolean; message: string }
export type SettingsSaveResult = { configured: boolean; service: string; connection: ConnectionCheck }

export class ApiError extends Error {
  status: number
  detail: unknown
  constructor(status: number, detail: unknown) {
    super(typeof detail === 'string' ? detail : detail && typeof detail === 'object' && 'message' in detail ? String((detail as { message: unknown }).message) : '请求失败')
    this.status = status
    this.detail = detail
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${baseUrl}${path}`, init)
  if (!response.ok) {
    const body = await response.json().catch(() => null)
    throw new ApiError(response.status, body?.detail ?? body ?? '请求失败')
  }
  return response.status === 204 ? undefined as T : response.json() as Promise<T>
}

const json = (body: unknown): RequestInit => ({ headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) })

export function mediaUrl(path: string) {
  if (!path || /^https?:|^blob:/.test(path)) return path
  return `${baseUrl}${path}`
}

export function referenceImageUrl(projectId: string, kind: AssetKind) {
  return mediaUrl(`/api/projects/${projectId}/reference-images/${kind}/content`)
}
export function referenceImageAssetUrl(imageUrl: string) { return mediaUrl(imageUrl) }

export function createProject(name: string, mode: ProjectMode = 'preserve_product') {
  return request<Project>('/api/projects', { method: 'POST', ...json({ name, mode }) })
}
export function listProjects(mode?: ProjectMode) {
  const query = mode ? `?mode=${mode}` : ''
  return request<Project[]>(`/api/projects${query}`)
}
export function getProject(projectId: string) { return request<ProjectDetails>(`/api/projects/${projectId}`) }
export function renameProject(projectId: string, name: string) { return request<Project>(`/api/projects/${projectId}`, { method: 'PATCH', ...json({ name }) }) }
export function deleteProject(projectId: string) { return request<{ deleted: boolean; media_deleted: boolean }>(`/api/projects/${projectId}`, { method: 'DELETE' }) }
export function getPreflight() { return request<Record<string, { ready: boolean; model: string; endpoint: string }>>('/api/preflight') }
export function saveVolcengineApiKey(volcengineApiKey: string) { return request<SettingsSaveResult>('/api/local-settings', { method: 'PUT', ...json({ volcengine_api_key: volcengineApiKey }) }) }
export function saveComflyApiKey(comflyApiKey: string) { return request<SettingsSaveResult>('/api/local-settings', { method: 'PUT', ...json({ comfly_api_key: comflyApiKey }) }) }
export function saveVisionSettings(payload: { volcengine_access_key: string; volcengine_secret_key: string; volcengine_vod_space: string }) { return request<SettingsSaveResult>('/api/local-settings', { method: 'PUT', ...json(payload) }) }
export function testServiceConnection(service: string) { return request<ConnectionCheck>(`/api/local-settings/test/${service}`, { method: 'POST' }) }

export function uploadReferenceVideo(projectId: string, file: File) {
  const body = new FormData(); body.append('file', file)
  return request<{ filename?: string; asset_kind?: string; job_kind?: string }>(`/api/projects/${projectId}/reference-video`, { method: 'POST', body })
}
export function uploadReferenceImage(projectId: string, kind: AssetKind, file: File, consent = false, metadata?: { view_label: string; display_name: string; note: string; product_name: string; product_category: string; package_form: string; selling_points: string }) {
  const body = new FormData(); body.append('file', file)
  if (metadata) Object.entries(metadata).forEach(([key, value]) => body.append(key, value))
  return request<{ asset_id: string; filename: string; job_id?: string; status?: AnalysisState; analysis_status?: AnalysisState }>(`/api/projects/${projectId}/reference-images/${kind}?consent=${consent}`, { method: 'POST', body })
}
export function deleteReferenceImage(projectId: string, kind: 'person' | 'background') {
  return request<{ deleted: number; media_deleted: boolean }>(`/api/projects/${projectId}/reference-images/${kind}`, { method: 'DELETE' })
}
export function updateProductReferenceImage(projectId: string, kind: 'product' | 'target_product', assetId: string, payload: { view_label: string; display_name: string }) {
  return request<{ id: string; filename: string; image_url: string; status: AnalysisState; view_label: string; display_name: string; note: string }>(`/api/projects/${projectId}/reference-images/${kind}/${assetId}`, { method: 'PATCH', ...json({ ...payload, display_name: payload.display_name.trim() }) })
}
export function analyzeReferenceImage(projectId: string, kind: AssetKind) {
  return request<{ job_id?: string; status: AnalysisState; profile?: string }>(`/api/projects/${projectId}/reference-images/${kind}/analyze`, { method: 'POST' })
}
export const analyzeProductReference = (projectId: string) => analyzeReferenceImage(projectId, 'product')
export function getReferenceProfile(projectId: string, kind: AssetKind) {
  return request<ReferenceProfile>(`/api/projects/${projectId}/reference-profiles/${kind}`)
}
export function updateReferenceProfile(projectId: string, kind: AssetKind, profile: string, structure: Record<string, unknown> = {}) {
  return request<ReferenceProfile>(`/api/projects/${projectId}/reference-profiles/${kind}`, { method: 'PUT', ...json({ profile, structure }) })
}

export function startAnalysis(projectId: string) { return request<Timeline>(`/api/projects/${projectId}/analysis/start`, { method: 'POST' }) }
export function requestVisionAnalysis(projectId: string) { return request<{ job_id: string; status: AnalysisState }>(`/api/projects/${projectId}/analysis/vision`, { method: 'POST' }) }
export function requestShotVisionAnalysis(projectId: string) { return request<{ queued_shots: number; skipped_succeeded: number; already_active: number; cancelled_obsolete: number }>(`/api/projects/${projectId}/analysis/vision-shots`, { method: 'POST' }) }
export function getVisionAnalysisJob(projectId: string, jobId: string) { return request<AnalysisJob>(`/api/projects/${projectId}/analysis/jobs/${jobId}`) }
export async function listAnalysisJobs(projectId: string): Promise<AnalysisJob[]> {
  const result = await request<AnalysisJob[] | { jobs: AnalysisJob[] }>(`/api/projects/${projectId}/analysis/jobs`)
  const jobs = Array.isArray(result) ? result : result.jobs
  return jobs.map((job) => ({ ...job, error_message: job.error_message || (job as AnalysisJob & { error?: string }).error }))
}
export function retryShotAnalysis(projectId: string, shotId: string) { return request<AnalysisJob>(`/api/projects/${projectId}/analysis/shots/${shotId}/retry`, { method: 'POST' }) }
export function cancelPromptJob(projectId: string, jobId: string) { return request<AnalysisJob>(`/api/projects/${projectId}/analysis/jobs/${jobId}/cancel`, { method: 'POST' }) }
export function getLatestShotAISummary(projectId: string, shotId: string) { return request<{ version: number; content: Record<string, string> }>(`/api/projects/${projectId}/shots/${shotId}/ai-summary/latest`) }

export function saveTimeline(projectId: string, shots: Array<{ start_sec: number; end_sec: number }>) { return request<Timeline>(`/api/projects/${projectId}/timeline`, { method: 'PUT', ...json({ shots }) }) }
export function splitTimelineShot(projectId: string, revisionId: string, shotId: string, atSec: number) { return request<Timeline>(`/api/projects/${projectId}/timeline/${revisionId}/split`, { method: 'POST', ...json({ shot_id: shotId, at_sec: atSec }) }) }
export function mergeTimelineShots(projectId: string, revisionId: string, shotIds: string[]) { return request<Timeline>(`/api/projects/${projectId}/timeline/${revisionId}/merge`, { method: 'POST', ...json({ shot_ids: shotIds }) }) }
export function restoreAiTimeline(projectId: string, revisionId: string) { return request<Timeline>(`/api/projects/${projectId}/timeline/${revisionId}/restore-ai`, { method: 'POST' }) }
export function getGenerationSegments(projectId: string) { return request<GenerationSegmentPlan>(`/api/projects/${projectId}/generation-segments`) }
export function autoPlanGenerationSegments(projectId: string) { return request<GenerationSegmentPlan>(`/api/projects/${projectId}/generation-segments/auto`, { method: 'POST' }) }
export function saveGenerationSegments(projectId: string, segments: GenerationSegmentInput[]) { return request<GenerationSegmentPlan>(`/api/projects/${projectId}/generation-segments`, { method: 'PUT', ...json({ segments }) }) }

export function saveShotEdit(projectId: string, shotId: string, payload: ShotEdit, expectedVersion = 0) {
  return request<ShotEdit>(`/api/projects/${projectId}/shots/${shotId}/edit`, {
    method: 'PUT', headers: { 'Content-Type': 'application/json', 'If-Match': String(expectedVersion) }, body: JSON.stringify(payload),
  })
}
export function getProductCompatibility(projectId: string, promptMode: 'full_reference_video_edit' | 'standalone_video_recreation' = 'full_reference_video_edit') { return request<ProductCompatibility>(`/api/projects/${projectId}/product-compatibility?prompt_mode=${promptMode}`) }
export function createPrompt(projectId: string, payload: { visual_direction: string; prompt_text?: string; audio_mode: string; audio_style: string; replace_product: boolean; replace_person: boolean; use_ai: boolean; prompt_mode: 'full_reference_video_edit' | 'standalone_video_recreation' }) { return request<{ version: number; text: string; status: AnalysisState; adaptation_count: number; adaptation_conflicts: CompatibilityConflict[] }>(`/api/projects/${projectId}/prompts`, { method: 'POST', ...json(payload) }) }
export function listPromptRevisions(projectId: string, promptMode?: PromptMode) {
  const query = promptMode ? `&prompt_mode=${promptMode}` : ''
  return request<PromptRevisionSummary[]>(`/api/projects/${projectId}/prompts?status=completed${query}`)
}
export function refinePrompt(projectId: string, instruction: string, sourceVersion?: number) { return request<{ version: number; text: string; status: AnalysisState }>(`/api/projects/${projectId}/prompts/refine`, { method: 'POST', ...json({ instruction, source_version: sourceVersion }) }) }
export function publishReferenceVideo(projectId: string) { return request<{ url: string; expires_at: string; notice: string }>(`/api/projects/${projectId}/reference-video/publish`, { method: 'POST' }) }
export function createGeneration(projectId: string, payload: { provider: string; prompt_version: number; ratio: string; duration: number; generate_audio: boolean; include_person_reference: boolean; include_background_reference: boolean }) { return request<{ id: string; status: string; error_message?: string | null }>(`/api/projects/${projectId}/generations`, { method: 'POST', ...json(payload) }) }
export function getGeneration(projectId: string, generationId: string) { return request<{ id: string; status: string; result_url?: string | null; local_video_url?: string | null; error_message?: string | null }>(`/api/projects/${projectId}/generations/${generationId}`) }

export function listGenerationBatches(projectId: string) { return request<GenerationBatch[]>(`/api/projects/${projectId}/generation-batches`) }
export function getGenerationBatch(projectId: string, batchId: string) { return request<GenerationBatch>(`/api/projects/${projectId}/generation-batches/${batchId}`) }
export function createGenerationBatch(projectId: string, input: { provider: 'volcengine' | 'comfly'; prompt_version: number; ratio: 'adaptive' | '16:9' | '4:3' | '1:1' | '3:4' | '9:16' | '21:9'; generate_audio: boolean }) {
  return request<GenerationBatch>(`/api/projects/${projectId}/generation-batches`, { method: 'POST', ...json(input) })
}
export function retryGeneration(projectId: string, generationId: string) { return request<GenerationSummary>(`/api/projects/${projectId}/generations/${generationId}/retry`, { method: 'POST' }) }
export function generationContentUrl(projectId: string, generationId: string) { return mediaUrl(`/api/projects/${projectId}/generations/${generationId}/content`) }
export function generationDownloadUrl(projectId: string, generationId: string) { return mediaUrl(`/api/projects/${projectId}/generations/${generationId}/content?download=true`) }
export function generationBatchMergedDownloadUrl(projectId: string, batchId: string) { return mediaUrl(`/api/projects/${projectId}/generation-batches/${batchId}/merged-content`) }
export function prepareGenerationBatchMergedDownload(projectId: string, batchId: string) { return request<{ ready: boolean }>(`/api/projects/${projectId}/generation-batches/${batchId}/merged-content`, { method: 'POST' }) }
export function optimizePromptSellingPoints(projectId: string, sourceVersion: number) {
  return request<{ version: number; text: string; status: string }>(`/api/projects/${projectId}/prompts/optimize-selling-points`, { method: 'POST', ...json({ source_version: sourceVersion }) })
}
export function resolveGeneration(projectId: string, generationId: string, action: 'attach_task' | 'confirm_not_created', externalTaskId?: string) {
  return request<GenerationSummary>(`/api/projects/${projectId}/generations/${generationId}/resolve`, {
    method: 'POST', ...json({ action, external_task_id: externalTaskId }),
  })
}
