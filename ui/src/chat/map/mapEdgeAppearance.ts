// 元の線の名前を変えず、確定・提案・根拠と操作中の強調を描画属性に写す。
interface EdgeInput {
  kind?: string
  thin?: boolean
  proposed?: boolean
  /** 台帳の未確認の関係（薄い色・「提案」の印は付けない）。 */
  unconfirmed?: boolean
  /** 台帳の線（確認済みは濃く太く描く）。 */
  ledger?: boolean
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
  state: { hovered: boolean; on: boolean; faded: boolean; lang: 'ja' | 'en'; /** 大きい図: 線の名前を常に出す（faded のときは出さない） */ names?: boolean; /** 線が多い図: 未確認の線の名前は選んだ点のまわりだけ（重なって読めなくなるため） */ quiet?: boolean },
) {
  const baseSize = data.size ?? 1
  const thinSize = data.thin ? Math.max(0.6, baseSize * 0.75) : baseSize
  const strong = data.ledger === true && !data.unconfirmed && !data.proposed
  const size = data.kind === 'evidence' ? Math.min(0.6, thinSize) : strong ? Math.max(thinSize, 1) * 1.8 : thinSize
  const color = state.hovered || state.on ? pal.edgeOn : state.faded ? pal.faded : strong ? pal.edgeOn : data.proposed || data.unconfirmed ? pal.edgeProposed : pal.edge
  const named = state.hovered || (state.names === true && !state.faded && !(state.quiet && data.unconfirmed && !state.on))
  const label = named && data.label
    ? `${data.label}${state.hovered && data.proposed ? ` (${state.lang === 'ja' ? '提案' : 'proposed'})` : ''}`
    : ''
  return { ...data, size: state.hovered ? size + 1 : size, color, label, zIndex: state.hovered ? 2 : state.on ? 1 : 0, forceLabel: state.hovered }
}
