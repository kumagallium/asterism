import { useEffect, useRef, useState } from 'react'
import './more-menu.css'

export interface MoreMenuItem {
  key: string
  label: string
  /** 押したとき（`href` を持つ項目では使わない）。 */
  onSelect?: () => void
  /** リンクとして開く項目（ダウンロードなど）。 */
  href?: string
  download?: boolean
  checked?: boolean
  disabled?: boolean
  title?: string
}

/**
 * 「…」1 つにまとめる操作のメニュー（UI 再設計 PR 5）。常時並べるボタンやチェックボックスの代わりに使う。
 * 外を押す・Esc で閉じる。星図の右上とテーマのカードで同じ部品を使う。
 */
export function MoreMenu({
  label,
  items,
  className,
}: {
  /** 「…」ボタンの名前（スクリーンリーダー・ツールチップ）。 */
  label: string
  items: MoreMenuItem[]
  /** 外側の配置（星図の右上に重ねる等）は呼び出し側が決める。 */
  className?: string
}) {
  const [open, setOpen] = useState(false)
  const ref = useRef<HTMLDivElement | null>(null)
  useEffect(() => {
    if (!open) return
    const onDown = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false)
    }
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') setOpen(false)
    }
    document.addEventListener('mousedown', onDown)
    document.addEventListener('keydown', onKey)
    return () => {
      document.removeEventListener('mousedown', onDown)
      document.removeEventListener('keydown', onKey)
    }
  }, [open])
  return (
    <div className={`more-menu${className ? ` ${className}` : ''}`} ref={ref}>
      <button
        type="button"
        className="btn btn--ghost btn--sm more-menu-btn"
        aria-haspopup="menu"
        aria-expanded={open}
        aria-label={label}
        title={label}
        onClick={() => setOpen((o) => !o)}
      >
        …
      </button>
      {open && (
        <div className="more-menu-list" role="menu">
          {items.map((it) => {
            const check = <span className="more-menu-check">{it.checked ? '✓' : ''}</span>
            if (it.href) {
              return (
                <a
                  key={it.key}
                  role="menuitem"
                  className="more-menu-item"
                  href={it.href}
                  download={it.download}
                  title={it.title}
                  onClick={() => setOpen(false)}
                >
                  {check}
                  {it.label}
                </a>
              )
            }
            return (
              <button
                key={it.key}
                type="button"
                role={it.checked === undefined ? 'menuitem' : 'menuitemcheckbox'}
                aria-checked={it.checked}
                className="more-menu-item"
                disabled={it.disabled}
                title={it.title}
                onClick={() => {
                  setOpen(false)
                  it.onSelect?.()
                }}
              >
                {check}
                {it.label}
              </button>
            )
          })}
        </div>
      )}
    </div>
  )
}
