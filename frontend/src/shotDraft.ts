import type { TimelineShot } from './api'

export type EditDraft = {
  people: string
  action: string
  product: string
  productInteraction: string
  background: string
  camera: string
  lighting: string
  visualStyle: string
  visibleText: string
  keep: string
  uncertainties: string
  confirmed: boolean
}

export function draftFromShot(shot?: TimelineShot): EditDraft {
  // 空字符串也是有效的人工修改；只有人工字段不存在时才使用 AI 初始事实。
  return {
    people: shot?.edit?.people ?? shot?.people ?? '',
    action: shot?.edit?.action ?? shot?.action ?? '',
    product: shot?.edit?.product ?? shot?.product ?? '',
    productInteraction: shot?.edit?.product_interaction ?? shot?.product_interaction ?? '',
    background: shot?.edit?.background ?? shot?.background ?? '',
    camera: shot?.edit?.camera ?? shot?.camera ?? '',
    lighting: shot?.edit?.lighting ?? shot?.lighting ?? '',
    visualStyle: shot?.edit?.visual_style ?? shot?.visual_style ?? '',
    visibleText: shot?.edit?.visible_text ?? shot?.on_screen_text ?? '',
    uncertainties: shot?.edit?.uncertainties ?? shot?.uncertainties ?? '',
    keep: shot?.edit?.keep_unchanged?.join('\n') ?? shot?.keep_unchanged ?? '',
    confirmed: Boolean(shot?.edit?.confirmed),
  }
}
