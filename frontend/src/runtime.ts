export function localId() {
  // 兼容没有 crypto.randomUUID 的旧版浏览器，避免时间轴操作直接让 React 白屏。
  return globalThis.crypto?.randomUUID?.() ?? `local-${Date.now()}-${Math.random().toString(16).slice(2)}`
}
