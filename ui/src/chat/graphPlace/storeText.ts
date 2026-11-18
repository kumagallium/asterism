// 「確認しましたが、正本には置けませんでした」の理由の文（記号ごと・ja／en は graphPlace.json の store.reason）。
import { useTranslation } from 'react-i18next'
import type { StoreReport } from './knowledgeRelationsApi'

export function useStoreText() {
  const { t } = useTranslation('graphPlace')
  /** 理由の記号 → 文。知らない記号・無いときは記号をそのまま（黙って捨てない）。 */
  const reasonText = (reason: string | undefined): string => {
    const r = reason || 'error'
    return t(`store.reason.${r}`, { defaultValue: r })
  }
  /** 行の 1 行（置けなかった件）。 */
  const lineText = (reason: string | undefined): string => t('store.line', { reason: reasonText(reason) })
  /** 操作の返りの `store` → その場の 1 行。置けた・何も無いときは null。 */
  const noteText = (store: StoreReport | null | undefined): string | null => {
    if (!store) return null
    if (store.error) return t('store.errorNote')
    if (store.failed.length === 0) return null
    return t('store.failedNote', { n: store.failed.length, reason: reasonText(store.failed[0].reason) })
  }
  return { reasonText, lineText, noteText }
}
