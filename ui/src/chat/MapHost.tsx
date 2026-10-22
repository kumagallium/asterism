// 地図の置き場（PR 11h 担当 2）。本体が `map/MapView` に差し替えるまでは、高さだけ確保した空の箱を出す。
// 差し替え: このファイルの本体を `export { MapView as MapHost } from './map/MapView'` にするか、
// ChatApp の `import { MapHost } from './MapHost'` を `import { MapView as MapHost } from './map/MapView'` に変える（1 行）。
import type { DraftSky, SkyData, SkyDataset } from '../sky/skyApi'
import type { PointSel } from './chatState'

/** 契約 pr11h-contract.md §3 の `MapViewProps`（`Selection` は `PointSel`）。 */
export interface MapHostProps {
  sky: SkyData
  drafts: readonly DraftSky[]
  proposedDatasets: readonly SkyDataset[]
  lang: 'ja' | 'en'
  height: number
  /** この話の点（濃く・名前を必ず出す）。 */
  focusIds: ReadonlySet<string>
  selectedId: string | null
  /** 1 枚・表・出典で押したもの（11d の highlight）。null なら無し。 */
  highlightIds: ReadonlySet<string> | null
  /** 外す提案の用語の id。 */
  removeIds: ReadonlySet<string>
  onSelect: (sel: PointSel | null, nodeId: string | null) => void
  ariaLabel: string
  fitLabel: string
}

export function MapHost({ height }: MapHostProps) {
  return <div className="chat-map" data-testid="chat-map-placeholder" style={{ height }} />
}
