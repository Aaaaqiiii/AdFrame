export type ProductObservation = {
  productInteraction?: string | null
  observations?: string | null
}

export function inferProductProfile(shots: ProductObservation[]): string {
  return [...new Set(shots
    .map((shot) => shot.productInteraction?.trim() || shot.observations?.trim() || '')
    .filter(Boolean))].join('；')
}
