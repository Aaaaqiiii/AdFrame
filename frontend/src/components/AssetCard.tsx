import { useRef, useState } from 'react'
import type { ChangeEvent, DragEvent } from 'react'
import type { AnalysisState } from '../api'
import { StatusBadge } from './StatusBadge'

type Props = {
  title: string
  description: string
  required?: boolean
  filename?: string
  previewUrl?: string
  accept: string
  status?: AnalysisState | null
  error?: string | null
  profile?: string
  uploading?: boolean
  multiple?: boolean
  deferMultiple?: boolean
  images?: Array<{ filename: string; previewUrl: string }>
  allowManualProfile?: boolean
  onFile: (file: File) => void
  onFiles?: (files: File[]) => void
  onRetry?: () => void
  onProfile?: (value: string) => void
  onSaveProfile?: () => void
}

export function AssetCard({ title, description, required, filename, previewUrl, accept, status, error, profile, uploading, multiple, deferMultiple, images = [], allowManualProfile, onFile, onFiles, onRetry, onProfile, onSaveProfile }: Props) {
  const input = useRef<HTMLInputElement>(null)
  const [pendingFiles, setPendingFiles] = useState<Array<{ file: File; previewUrl: string }>>([])
  const [dragging, setDragging] = useState(false)
  const [dropError, setDropError] = useState('')
  function receiveFiles(files: File[]) {
    const mediaPrefix = accept.startsWith('video') ? 'video/' : 'image/'
    const accepted = files.filter((file) => file.type.startsWith(mediaPrefix))
    if (!accepted.length) {
      setDropError(mediaPrefix === 'video/' ? '请拖入 MP4、MOV 或 WebM 视频。' : '请拖入 JPG、PNG 或 WebP 图片。')
      return
    }
    setDropError('')
    const selected = multiple ? accepted : accepted.slice(0, 1)
    if (multiple && deferMultiple) setPendingFiles((current) => [...current, ...selected.map((file) => ({ file, previewUrl: URL.createObjectURL(file) }))])
    else if (multiple && onFiles) onFiles(selected)
    else if (selected[0]) onFile(selected[0])
  }
  function changed(event: ChangeEvent<HTMLInputElement>) {
    receiveFiles(Array.from(event.target.files || []))
    event.target.value = ''
  }
  function dropped(event: DragEvent<HTMLDivElement>) {
    event.preventDefault()
    setDragging(false)
    receiveFiles(Array.from(event.dataTransfer.files || []))
  }
  function submitPending() {
    onFiles?.(pendingFiles.map((item) => item.file))
    pendingFiles.forEach((item) => URL.revokeObjectURL(item.previewUrl))
    setPendingFiles([])
  }
  function clearPending() {
    pendingFiles.forEach((item) => URL.revokeObjectURL(item.previewUrl))
    setPendingFiles([])
  }
  const image = accept.startsWith('image')
  const storedImages = images.length ? images : previewUrl ? [{ filename: filename || title, previewUrl }] : []
  const displayedImages = [...storedImages, ...pendingFiles.map((item) => ({ filename: item.file.name, previewUrl: item.previewUrl }))]
  return <article className={`asset-card ${filename ? 'has-file' : ''}`}>
    <header><div><span className="eyebrow">{required ? '必填素材' : '可选素材'}</span><h3>{title}</h3></div>{filename && <StatusBadge status={status} />}</header>
    <p>{description}</p>
    {!image && previewUrl && <video className="asset-video-preview" src={previewUrl} controls preload="metadata">浏览器无法播放这个视频。</video>}
    <div className={`asset-drop ${dragging ? 'dragging' : ''}`} onClick={() => input.current?.click()} onDragEnter={(event) => { event.preventDefault(); setDragging(true) }} onDragOver={(event) => { event.preventDefault(); setDragging(true) }} onDragLeave={() => setDragging(false)} onDrop={dropped} role="button" tabIndex={0} onKeyDown={(event) => { if (event.key === 'Enter') input.current?.click() }}>
      {displayedImages.length && image ? <div className="asset-preview-grid">{displayedImages.map((item, index) => <img key={`${item.filename}-${index}`} src={item.previewUrl} alt={`${title}参考图 ${index + 1}`} />)}</div> : <span className="asset-icon">{image ? 'IMG' : 'MOV'}</span>}
      <div><strong>{uploading ? '正在上传…' : pendingFiles.length ? `待上传 ${pendingFiles.length} 张图片` : multiple && storedImages.length ? `已上传 ${storedImages.length} 张图片` : filename || `上传${title}`}</strong><small>{accept.startsWith('video') ? '点击选择或拖入 MP4 / MOV / WebM' : multiple ? '点击选择或拖入多张 JPG / PNG / WebP' : '点击选择或拖入 JPG / PNG / WebP'}</small></div>
      <input ref={input} hidden type="file" accept={accept} multiple={multiple} onChange={changed} />
    </div>
    {deferMultiple && <div className="asset-batch-actions">
      {pendingFiles.length > 0 && <button className="button compact" onClick={clearPending}>清空待选</button>}
      <button className="button primary" disabled={!pendingFiles.length} onClick={submitPending}>{pendingFiles.length ? `上传并理解这 ${pendingFiles.length} 张图片` : '上传并理解选中的图片'}</button>
    </div>}
    {dropError && <div className="inline-error"><span>{dropError}</span></div>}
    {error && <div className="inline-error"><span>{error}</span>{onRetry && <button onClick={onRetry}>重试理解</button>}</div>}
    {(filename || allowManualProfile) && image && <div className="profile-editor">
      <label>{filename ? 'AI 文字档案' : '人物文字档案'} <small>{filename ? '图片不会默认提交给 Seedance' : '没有照片也可以直接填写'}</small></label>
      <textarea autoComplete="off" value={profile || ''} onChange={(event) => onProfile?.(event.target.value)} placeholder={allowManualProfile && !filename ? '例如：25岁左右女性，黑色齐肩直发，白色衬衫，自然妆容，亲和微笑。' : status === 'failed' ? '理解失败，可重试；也可以人工填写档案。' : 'AI 理解完成后会在这里生成可编辑文字档案。'} />
      {profile && onSaveProfile && <button className="button compact" onClick={onSaveProfile}>{filename ? '保存人工校对' : '保存人物文字档案'}</button>}
    </div>}
  </article>
}
