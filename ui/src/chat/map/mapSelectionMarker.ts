import type Sigma from 'sigma'

/** 元の丸と当たり判定を保ち、選んだ一点の上にだけ静かな星を添える。 */
export function attachMapSelectionMarker(
  sigma: Pick<Sigma, 'on' | 'off' | 'getNodeDisplayData' | 'framedGraphToViewport' | 'scaleSize'>,
  host: HTMLElement,
  selectedId: () => string | null,
  palette: { fill: string; paper: string },
): () => void {
  const ns = 'http://www.w3.org/2000/svg'
  const marker = host.ownerDocument.createElementNS(ns, 'svg')
  marker.setAttribute('viewBox', '-8 -8 16 16')
  marker.setAttribute('width', '14')
  marker.setAttribute('height', '14')
  marker.setAttribute('aria-hidden', 'true')
  marker.classList.add('chat-map-selection-marker')
  Object.assign(marker.style, {
    position: 'absolute', pointerEvents: 'none', display: 'none',
    overflow: 'visible', transform: 'translate(-50%, -50%)',
  })
  const star = host.ownerDocument.createElementNS(ns, 'path')
  // 各辺の12%だけ頂点を丸め、六つの先端と谷を小さな表示でも残す。
  const vertices = Array.from({ length: 12 }, (_, i) => {
    const angle = -Math.PI / 2 + i * Math.PI / 6
    const radius = i % 2 === 0 ? 7 : 3.2
    return { x: Math.cos(angle) * radius, y: Math.sin(angle) * radius }
  })
  const near = (vertex: typeof vertices[number], neighbor: typeof vertices[number]) =>
    `${vertex.x + (neighbor.x - vertex.x) * 0.12},${vertex.y + (neighbor.y - vertex.y) * 0.12}`
  star.setAttribute('d', `M${near(vertices[0], vertices[11])} ${vertices.map((v, i) => {
    const next = vertices[(i + 1) % 12]
    return `Q${v.x},${v.y} ${near(v, next)} L${near(next, v)}`
  }).join(' ')} Z`)
  star.setAttribute('fill', palette.fill)
  star.setAttribute('stroke', palette.paper)
  star.setAttribute('stroke-width', '1.25')
  star.setAttribute('stroke-linejoin', 'round')
  marker.appendChild(star)
  host.appendChild(marker)

  const hide = () => { marker.style.display = 'none' }
  const render = () => {
    const id = selectedId()
    const data = id === null ? undefined : sigma.getNodeDisplayData(id)
    if (!data || data.hidden) return hide()
    // Sigma 3 の renderLabels と同じ変換。キャッシュの x/y は画面座標ではない。
    const { x, y } = sigma.framedGraphToViewport(data)
    const radius = sigma.scaleSize(data.size)
    if (![x, y, radius].every(Number.isFinite)) return hide()
    marker.style.left = `${x}px`
    marker.style.top = `${y - radius - 10}px`
    marker.style.display = 'block'
  }
  let detached = false
  const detach = () => {
    if (detached) return
    detached = true
    sigma.off('afterRender', render)
    sigma.off('afterClear', hide)
    sigma.off('kill', detach)
    marker.remove()
  }
  // カメラ・容器の寸法・選択の変更は、描画後の最新座標で一括して追従する。
  sigma.on('afterRender', render)
  sigma.on('afterClear', hide)
  sigma.on('kill', detach)
  render()
  return detach
}
