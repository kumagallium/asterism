// 元の線の名前を変えず、確定・提案・根拠と操作中の強調を描画属性に写す。
interface EdgeInput {
  kind?: string
  thin?: boolean
  proposed?: boolean
  size?: number
  label?: string | null
}
interface EdgePalette {
  edge: string
  edgeProposed: string
  edgeOn: string
  faded: string
}

export function mapEdgeAppearance<T extends EdgeInput>(
  data: T,
  pal: EdgePalette,
  state: { hovered: boolean; on: boolean; faded: boolean; lang: 'ja' | 'en' },
) {
  const baseSize = data.size ?? 1
  const thinSize = data.thin ? Math.max(0.6, baseSize * 0.75) : baseSize
  const size = data.kind === 'evidence' ? Math.min(0.6, thinSize) : thinSize
  const color = state.hovered || state.on ? pal.edgeOn : state.faded ? pal.faded : data.proposed ? pal.edgeProposed : pal.edge
  const label = state.hovered && data.label
    ? `${data.label}${data.proposed ? ` (${state.lang === 'ja' ? '提案' : 'proposed'})` : ''}`
    : ''
  return { ...data, size: state.hovered ? size + 1 : size, color, label, zIndex: state.hovered ? 2 : state.on ? 1 : 0, forceLabel: state.hovered }
}
