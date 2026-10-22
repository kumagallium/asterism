// AI が設定されていないとき、📎 で右の領域（図の代わり）に出す「データを添える」（PR 11c）。
// 見た目は SkyView の AddArea（`sky-add-area`）と同じ。中身はかんたん（WorkbenchTier）をそのまま置く。
import { lazy, Suspense } from 'react'
import { useTranslation } from 'react-i18next'
import { openHostedSky, useHostedState } from '../kantan/hostedStage'
import '../sky/sky.css'

const WorkbenchTier = lazy(() => import('../kantan/WorkbenchTier').then((m) => ({ default: m.WorkbenchTier })))

export interface AddPanelProps {
  /** 閉じる（図を取り直す）。 */
  onClose: () => void
}

export function AddPanel({ onClose }: AddPanelProps) {
  const { t } = useTranslation('chat')
  const { done } = useHostedState()
  // ⑦完了のときの「閉じる」は、⑦の「星図で見る」と同じ道（`#/home?new=` → `#/` に転送される）
  const close = () => {
    if (done && openHostedSky()) return
    onClose()
  }
  return (
    <section className="sky-add-area chat-add-area" aria-label={t('attach.area')} data-testid="add-area">
      <div className="sky-add-head">
        <span className="sky-add-title">{t('attach.area')}</span>
        <button type="button" className="btn btn--ghost btn--sm" onClick={close} data-testid="add-close">
          {t('attach.close')}
        </button>
      </div>
      <Suspense fallback={null}>
        <WorkbenchTier onShortcutDone={() => onClose()} onOpenDataset={() => onClose()} />
      </Suspense>
    </section>
  )
}
