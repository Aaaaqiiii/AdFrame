import { renderToStaticMarkup } from 'react-dom/server'
import { describe, expect, it, vi } from 'vitest'
import { ProductReferenceCard } from './ProductReferenceCard'

function render(status: 'queued' | 'processing' | 'succeeded' | 'failed', profile = '', imageStatus: 'queued' | 'processing' | 'succeeded' | 'failed' = status) {
  return renderToStaticMarkup(<ProductReferenceCard
    productName="测试产品"
    productCategory="发膜"
    packageForm="jar"
    sellingPoints=""
    images={[{ id: 'image-1', filename: 'front.png', previewUrl: '/front.png', viewLabel: 'front', displayName: '正面', note: '', status: imageStatus }]}
    status={status}
    error=""
    profile={profile}
    confirmed={false}
    onProductName={vi.fn()}
    onProductCategory={vi.fn()}
    onPackageForm={vi.fn()}
    onSellingPoints={vi.fn()}
    onUpload={vi.fn()}
    onRename={vi.fn()}
    onProfile={vi.fn()}
    onSave={vi.fn()}
    onRetry={vi.fn()}
  />)
}

describe('ProductReferenceCard', () => {
  it('shows a visible progress message while the product profile is queued or processing', () => {
    expect(render('queued')).toContain('正在生成产品档案…')
    expect(render('processing')).toContain('AI 正在理解已上传的 1 张图片')
  })

  it('removes the progress message after analysis succeeds', () => {
    expect(render('succeeded', '产品事实')).not.toContain('正在生成产品档案…')
  })

  it('shows the status of each uploaded product view', () => {
    expect(render('failed', '部分产品事实', 'failed')).toContain('分析失败')
  })
})
