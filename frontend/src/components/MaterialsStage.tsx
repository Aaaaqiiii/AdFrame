import type { AnalysisState, AssetKind } from '../api'
import type { ProjectMode } from '../workspaceDomain'
import { AssetCard } from './AssetCard'
import { ProductReferenceCard } from './ProductReferenceCard'
import type { ProductImageUpload, ProductReferenceImage } from './ProductReferenceCard'

export type MaterialState = { filename: string; previewUrl: string; images: ProductReferenceImage[]; status: AnalysisState; profile: string; error: string }

type Props = {
  mode: ProjectMode
  video: { filename: string; previewUrl: string; uploading: boolean }
  materials: Record<AssetKind, MaterialState>
  productIdentity: { name: string; sellingPoints: string; confirmed: boolean }
  onVideo: (file: File) => void
  onImage: (kind: AssetKind, file: File) => void
  onProductImages: (items: ProductImageUpload[], productName: string, sellingPoints: string) => Promise<boolean>
  onProductName: (value: string) => void
  onProductSellingPoints: (value: string) => void
  onProductProfileSave: (confirmed: boolean) => void
  onRetry: (kind: AssetKind) => void
  onProfile: (kind: AssetKind, profile: string) => void
  onSaveProfile: (kind: AssetKind) => void
  onContinue: () => void
  ready: boolean
  blockingReason: string
}

export function MaterialsStage(p: Props) {
  const productTitle = p.mode === 'replace_product' ? '目标产品图' : '原产品辅助图'
  const productCard = p.mode === 'replace_product'
    ? <ProductReferenceCard productName={p.productIdentity.name} sellingPoints={p.productIdentity.sellingPoints} images={p.materials.product.images} status={p.materials.product.status} error={p.materials.product.error} profile={p.materials.product.profile} confirmed={p.productIdentity.confirmed} onProductName={p.onProductName} onSellingPoints={p.onProductSellingPoints} onUpload={p.onProductImages} onProfile={(value) => p.onProfile('product', value)} onSave={p.onProductProfileSave} onRetry={() => p.onRetry('product')} />
    : <AssetCard title={productTitle} description="辅助核对原视频里的产品，不会自动作为生成参考图。" filename={p.materials.product.filename} previewUrl={p.materials.product.previewUrl} images={p.materials.product.images} accept="image/jpeg,image/png,image/webp" status={p.materials.product.status} error={p.materials.product.error} profile={p.materials.product.profile} onFile={(file) => p.onImage('product', file)} onRetry={() => p.onRetry('product')} onProfile={(value) => p.onProfile('product', value)} onSaveProfile={() => p.onSaveProfile('product')} />
  const videoCard = <AssetCard title="参考视频" required description="先在本地切分镜头；人工确认边界后才启动豆包和 GPT 逐镜理解。" filename={p.video.filename} previewUrl={p.video.previewUrl} accept="video/mp4,video/quicktime,video/webm" uploading={p.video.uploading} onFile={p.onVideo} />
  return <section className="stage-content">
    <div className="stage-heading"><div><span className="eyebrow">第一步</span><h1>{p.mode === 'replace_product' ? '先让 AI 认识参考广告和新产品' : '先让 AI 认识参考广告里的产品'}</h1><p>{p.mode === 'replace_product' ? '参考视频决定广告动作与节奏；目标产品图决定必须替换成什么。两者缺一不可。' : '参考视频是必填。产品图可选，但上传清晰产品图能更准确锁定包装、Logo 和结构。'}</p></div><div className="mode-rule"><strong>{p.mode === 'replace_product' ? '替换规则' : '锁定规则'}</strong><span>{p.mode === 'replace_product' ? '新产品将应用于所有产品相关镜头，动作必须与包装结构兼容。' : '原产品身份、包装、Logo 和文字不得被替换。'}</span></div></div>
    <div className="material-grid primary-assets">
      {p.mode === 'replace_product' ? <>{productCard}{videoCard}</> : <>{videoCard}{productCard}</>}
    </div>
    <details className="optional-assets"><summary><span><strong>人物与背景参考</strong><small>仅在需要改变人物或背景时上传，AI 先转成文字档案</small></span><b>展开</b></summary><div className="material-grid">
      <AssetCard title="人物参考图或文字描述" description="有照片可以上传；没有合适照片时，直接在下方填写人物外貌、发型、服装和表情。" filename={p.materials.person.filename} previewUrl={p.materials.person.previewUrl} allowManualProfile accept="image/jpeg,image/png,image/webp" status={p.materials.person.status} error={p.materials.person.error} profile={p.materials.person.profile} onFile={(file) => p.onImage('person', file)} onRetry={() => p.onRetry('person')} onProfile={(value) => p.onProfile('person', value)} onSaveProfile={() => p.onSaveProfile('person')} />
      <AssetCard title="背景参考图" description="AI 提取空间、陈设、光线、景深与排除物。" filename={p.materials.background.filename} previewUrl={p.materials.background.previewUrl} accept="image/jpeg,image/png,image/webp" status={p.materials.background.status} error={p.materials.background.error} profile={p.materials.background.profile} onFile={(file) => p.onImage('background', file)} onRetry={() => p.onRetry('background')} onProfile={(value) => p.onProfile('background', value)} onSaveProfile={() => p.onSaveProfile('background')} />
    </div></details>
    <footer className="stage-footer"><div>{!p.ready && <p className="blocking-message">{p.blockingReason}</p>}<small>下一步只在本地切分视频，不会启动豆包或 GPT。</small></div><button className="button primary large" disabled={!p.ready} onClick={p.onContinue}>开始切分视频</button></footer>
  </section>
}
