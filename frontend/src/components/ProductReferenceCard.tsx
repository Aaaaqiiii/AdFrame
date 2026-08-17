import { useRef, useState } from 'react'
import type { ChangeEvent, DragEvent } from 'react'
import type { AnalysisState } from '../api'
import { PRODUCT_IMAGE_VIEWS, productImageDisplayName } from '../productImageLabels'
import { StatusBadge } from './StatusBadge'

export type ProductImageUpload = { file: File; viewLabel: string; displayName: string; note: string }
export type ProductReferenceImage = { id?: string; filename: string; previewUrl: string; viewLabel: string; displayName: string; note: string }

export function ProductReferenceCard(p: {
  productName: string
  sellingPoints: string
  images: ProductReferenceImage[]
  status: AnalysisState
  error: string
  profile: string
  confirmed: boolean
  onProductName: (value: string) => void
  onSellingPoints: (value: string) => void
  onUpload: (items: ProductImageUpload[], productName: string, sellingPoints: string) => Promise<boolean>
  onRename: (imageId: string, viewLabel: string, displayName: string) => Promise<boolean>
  onProfile: (value: string) => void
  onSave: (confirmed: boolean) => void
  onRetry: () => void
}) {
  const input = useRef<HTMLInputElement>(null)
  const [pending, setPending] = useState<Array<ProductImageUpload & { previewUrl: string }>>([])
  const [dragging, setDragging] = useState(false)
  const [uploading, setUploading] = useState(false)
  const [editing, setEditing] = useState<{ id: string; viewLabel: string; displayName: string } | null>(null)
  const [savingName, setSavingName] = useState(false)

  function receive(files: File[]) {
    const accepted = files.filter((file) => file.type.startsWith('image/'))
    // 选择阶段只生成本地预览；用户填写完每张图的角度和备注后才真正上传。
    setPending((current) => [...current, ...accepted.map((file) => ({ file, viewLabel: 'front', displayName: '正面', note: '', previewUrl: URL.createObjectURL(file) }))])
  }
  function changed(event: ChangeEvent<HTMLInputElement>) {
    receive(Array.from(event.target.files || []))
    event.target.value = ''
  }
  function dropped(event: DragEvent<HTMLDivElement>) {
    event.preventDefault()
    setDragging(false)
    receive(Array.from(event.dataTransfer.files || []))
  }
  function updatePending(index: number, patch: Partial<ProductImageUpload>) {
    setPending((current) => current.map((item, position) => position === index ? { ...item, ...patch } : item))
  }
  function removePending(index: number) {
    setPending((current) => {
      URL.revokeObjectURL(current[index].previewUrl)
      return current.filter((_, position) => position !== index)
    })
  }
  async function submit() {
    setUploading(true)
    const saved = await p.onUpload(pending, p.productName.trim(), p.sellingPoints.trim())
    setUploading(false)
    if (saved) {
      // 只有整批上传成功才清空，网络失败时保留用户已经填写的逐图备注。
      pending.forEach((item) => URL.revokeObjectURL(item.previewUrl))
      setPending([])
    }
  }
  async function saveName() {
    if (!editing?.displayName.trim()) return
    setSavingName(true)
    const saved = await p.onRename(editing.id, editing.viewLabel, editing.displayName.trim())
    setSavingName(false)
    if (saved) setEditing(null)
  }

  return <article className="asset-card product-reference-card">
    <header><div><span className="eyebrow">必填素材</span><h3>目标产品档案</h3></div>{p.images.length > 0 && <StatusBadge status={p.status} />}</header>
    <p>先填写产品信息，再为每张图片标注角度和备注，最后统一上传并理解。</p>
    <div className="product-basics">
      <label><strong>产品名称</strong><input value={p.productName} onChange={(event) => p.onProductName(event.target.value)} placeholder="例如：蓬松洗发水" /></label>
      <label><strong>产品卖点</strong><textarea value={p.sellingPoints} onChange={(event) => p.onSellingPoints(event.target.value)} placeholder="每行一条，例如：增加发根蓬松感；改善贴头皮。卖点会标记为用户提供信息。" /></label>
    </div>
    {p.images.length > 0 && <div className="product-image-cards stored">{p.images.map((item, index) => {
      const edit = item.id && editing?.id === item.id ? editing : null
      return <section key={item.id || `${item.filename}-${index}`}>
        <img src={item.previewUrl} alt={`${item.viewLabel || '产品'}参考图`} />
        <div>{edit ? <div className="image-name-editor">
          <select aria-label="图片角度" value={edit.viewLabel} onChange={(event) => { const viewLabel = event.target.value; setEditing((current) => current ? { ...current, viewLabel, displayName: productImageDisplayName(viewLabel, '') } : null) }}>{PRODUCT_IMAGE_VIEWS.map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select>
          <input aria-label="图片名称" maxLength={40} value={edit.displayName} onChange={(event) => { const displayName = event.target.value; setEditing((current) => current ? { ...current, displayName } : null) }} placeholder="例如：瓶身正面" autoFocus />
          <span><button type="button" disabled={savingName || !edit.displayName.trim()} onClick={() => void saveName()}>{savingName ? '保存中…' : '保存'}</button><button type="button" disabled={savingName} onClick={() => setEditing(null)}>取消</button></span>
        </div> : <button type="button" className="image-name" disabled={!item.id} onClick={() => item.id && setEditing({ id: item.id, viewLabel: item.viewLabel, displayName: productImageDisplayName(item.viewLabel, item.displayName) })}><strong>{productImageDisplayName(item.viewLabel, item.displayName)}</strong><small>点击修改</small></button>}<small>{item.filename}</small>{item.note && <p>{item.note}</p>}</div>
      </section>
    })}</div>}
    {pending.length > 0 && <div className="product-image-cards pending">{pending.map((item, index) => <section key={`${item.file.name}-${item.file.lastModified}-${index}`}>
      <img src={item.previewUrl} alt={`待上传产品图 ${index + 1}`} />
      <div><select aria-label={`待上传图片 ${index + 1} 的角度`} value={item.viewLabel} onChange={(event) => { const viewLabel = event.target.value; updatePending(index, { viewLabel, displayName: productImageDisplayName(viewLabel, '') }) }}>{PRODUCT_IMAGE_VIEWS.map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select><input aria-label={`待上传图片 ${index + 1} 的名称`} maxLength={40} value={item.displayName} onChange={(event) => updatePending(index, { displayName: event.target.value })} placeholder="图片名称，例如：瓶身正面" /><textarea value={item.note} onChange={(event) => updatePending(index, { note: event.target.value })} placeholder="备注这张图需要AI注意的内容" /><button type="button" onClick={() => removePending(index)}>移除</button></div>
    </section>)}</div>}
    <div className={`asset-drop product-drop ${dragging ? 'dragging' : ''}`} onClick={() => input.current?.click()} onDragEnter={(event) => { event.preventDefault(); setDragging(true) }} onDragOver={(event) => { event.preventDefault(); setDragging(true) }} onDragLeave={() => setDragging(false)} onDrop={dropped} role="button" tabIndex={0} onKeyDown={(event) => { if (event.key === 'Enter') input.current?.click() }}>
      <span className="asset-icon">IMG</span><div><strong>{pending.length ? `已选择 ${pending.length} 张，继续添加` : p.images.length ? '继续添加产品图片' : '选择或拖入产品图片'}</strong><small>支持正面、侧面、背面、Logo和包装细节图</small></div>
      <input ref={input} hidden type="file" multiple accept="image/jpeg,image/png,image/webp" onChange={changed} />
    </div>
    <div className="asset-batch-actions"><button className="button primary" disabled={uploading || !p.productName.trim() || !pending.length} onClick={() => void submit()}>{uploading ? '正在上传…' : pending.length ? `上传并理解这 ${pending.length} 张图片` : '请先选择图片'}</button></div>
    {p.error && <div className="inline-error"><span>{p.error}</span><button onClick={p.onRetry}>重试理解</button></div>}
    {(p.profile || p.images.length > 0) && <div className="product-fact-summary">
      <header><div><strong>产品事实总结</strong><small>只会把确认后的版本写入最终提示词</small></div><span className={p.confirmed ? 'confirmed' : ''}>{p.confirmed ? '已确认' : '待确认'}</span></header>
      <textarea value={p.profile} onChange={(event) => p.onProfile(event.target.value)} placeholder={p.status === 'failed' ? '理解失败，可以重试或人工填写产品事实。' : '全部图片理解完成后，这里会生成可编辑的产品事实。'} />
      <footer><button className="button compact" disabled={!p.profile.trim()} onClick={() => p.onSave(false)}>保存人工修改</button><button className="button primary" disabled={!p.profile.trim()} onClick={() => p.onSave(true)}>确认产品档案</button></footer>
    </div>}
  </article>
}
