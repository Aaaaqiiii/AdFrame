export const PRODUCT_IMAGE_VIEWS = [
  ['front', '正面'], ['left', '左侧'], ['right', '右侧'], ['back', '背面'],
  ['top', '顶部'], ['bottom', '底部'], ['packaging', '包装细节'], ['logo', 'Logo细节'],
  ['opening', '开口细节'], ['detail', '其他细节'], ['other', '其他'],
] as const

export function productImageDisplayName(viewLabel: string, displayName: string) {
  return displayName.trim() || PRODUCT_IMAGE_VIEWS.find(([value]) => value === viewLabel)?.[1] || '其他'
}
