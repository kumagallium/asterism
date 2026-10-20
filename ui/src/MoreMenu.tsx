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
 * 外を押す・Esc で閉じる。開くと最初の項目にフォーカスが移り、↑↓ で動かせる。星図の右上とテーマのカードで同じ部品を使う。
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
  const btnRef = useRef<HTMLButtonElement | null>(null)
  const listRef = useRef<HTMLDivElement | null>(null)
  const enabledItems = () =>
    Array.from(listRef.current?.querySelectorAll<HTMLElement>('[role^="menuitem"]:not(:disabled)') ?? [])
  // 開いたら最初の項目へ。
  useEffect(() => {
    if (open) enabledItems()[0]?.focus()
  }, [open])
  useEffect(() => {
    if (!open) return
    const onDown = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false)
    }
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        setOpen(false)
        btnRef.current?.focus()
        return
      }
      // ↑↓ Home End で項目を移る。Tab は閉じる。
      const items = enabledItems()
      if (items.length === 0) return
      const at = items.indexOf(document.activeElement as HTMLElement)
      if (e.key === 'ArrowDown') {
        e.preventDefault()
        items[(at + 1) % items.length].focus()
      } else if (e.key === 'ArrowUp') {
        e.preventDefault()
        items[(at <= 0 ? items.length : at) - 1].focus()
      } else if (e.key === 'Home') {
        e.preventDefault()
        items[0].focus()
      } else if (e.key === 'End') {
        e.preventDefault()
        items[items.length - 1].focus()
      } else if (e.key === 'Tab') {
        setOpen(false)
      }
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
        ref={btnRef}
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
        <div className="more-menu-list" role="menu" ref={listRef}>
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
