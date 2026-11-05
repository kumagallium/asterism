// 根拠の文から論文を開く入口を、深い部品（EvidenceLine・出典・論文の 1 枚）へ渡す文脈（13b）。
// 文脈が無い（テスト・別の画面）ときは null で、根拠の文は押せない見た目のまま。
import { createContext, useContext } from 'react'

export interface PaperReaderApi {
  /** 論文を開く（`focus` = 強調して送る文の IRI）。 */
  open: (datasetId: string, focus: string | null) => void
}

export const PaperReaderContext = createContext<PaperReaderApi | null>(null)

/** 論文を開ける文脈。`datasetId` が無い（文脈も無い）なら null（押せない）。開けるかは押したときに api が決める（404 はその回だけ「開けません」）。 */
export function useOpenPaper(datasetId: string | null | undefined): ((focus: string | null) => void) | null {
  const ctx = useContext(PaperReaderContext)
  if (!ctx || !datasetId) return null
  return (focus) => ctx.open(datasetId, focus)
}
