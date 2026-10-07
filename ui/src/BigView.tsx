// 図を「大きく見る」の共通部品。もとは VocabMap.tsx と kantan/ShapeGraph.tsx が
// 同じ処理を重複して持っていた（契約メモ contract_big_view_and_overview.md §1.1）。
// 見た目は App.css の shape-overlay* / shape-graph-expand をそのまま使う。
import { forwardRef, useEffect, useRef, type ReactNode } from 'react'
import { createPortal } from 'react-dom'
import { useTranslation } from 'react-i18next'
import { CloseIcon, ExpandIcon } from './icons'

/** 図の枠の右上の小さなボタン（枠は position: relative であること）。 */
export const ExpandButton = forwardRef<HTMLButtonElement, { onClick: () => void }>(
  function ExpandButton({ onClick }, ref) {
    const { t } = useTranslation()
    return (
      <button
        ref={ref}
        type="button"
        className="shape-graph-expand"
        onClick={onClick}
        aria-label={t('skeletongate:diagram.expand')}
        title={t('skeletongate:diagram.expand')}
      >
        <ExpandIcon size={15} />
      </button>
    )
  },
)

/** 画面いっぱいの重ね表示。Esc・背景クリック・閉じるで onClose。
 *  開いたら閉じるボタンへフォーカス、閉じたら開く前の要素へ戻す。
 *  開いている間は背景のスクロールを止める。 */
export function BigViewOverlay({
  open,
  onClose,
  title,
  children,
}: {
  open: boolean
  onClose: () => void
  title: string
  children: ReactNode
}) {
  const { t } = useTranslation()
  const closeRef = useRef<HTMLButtonElement>(null)
  const onCloseRef = useRef(onClose)
  useEffect(() => {
    onCloseRef.current = onClose
  })

  useEffect(() => {
    if (!open) return
    const opener = document.activeElement as HTMLElement | null
    const prevOverflow = document.body.style.overflow
    document.body.style.overflow = 'hidden'
    closeRef.current?.focus()
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onCloseRef.current()
    }
    window.addEventListener('keydown', onKey)
    return () => {
      window.removeEventListener('keydown', onKey)
      document.body.style.overflow = prevOverflow
      if (opener && typeof opener.focus === 'function' && document.contains(opener)) opener.focus()
    }
  }, [open])

  if (!open) return null
  return createPortal(
    <div className="shape-overlay" role="dialog" aria-modal="true" aria-label={title} onClick={onClose}>
      <div className="shape-overlay-panel" onClick={(e) => e.stopPropagation()}>
        <div className="shape-overlay-head">
          <span>{title}</span>
          <button
            ref={closeRef}
            type="button"
            className="shape-overlay-close"
            onClick={onClose}
            aria-label={t('skeletongate:diagram.close')}
          >
            <CloseIcon size={18} />
          </button>
        </div>
        {children}
      </div>
    </div>,
    document.body,
  )
}
