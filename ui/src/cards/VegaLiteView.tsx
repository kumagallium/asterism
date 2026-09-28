import { useEffect, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import type { VegaLiteSpec } from './viewSpec'
import { withHouseStyle } from './vegaTheme'

/**
 * Vega-Lite の実描画器。`spec` は「データであって実行しない」── 評価するのは
 * 固定ライブラリの vega-embed だけで、外部の文字列から `expr` を組み立てたり
 * `eval` / `new Function` を呼んだりする経路は無い。色は必ず `withHouseStyle`
 * で house に揃える（`custom: true` の仕様が持ち込む色も上書きされる）。
 *
 * `vega-embed` は動的 import（初期バンドルに含めない・別チャンクにする）。
 */
export function VegaLiteView({
  spec,
  ariaLabel,
  height = 280,
}: {
  spec: VegaLiteSpec
  ariaLabel: string
  height?: number
}) {
  const { t } = useTranslation()
  const containerRef = useRef<HTMLDivElement | null>(null)
  const [failed, setFailed] = useState(false)

  useEffect(() => {
    // コンテナは失敗時も `hidden` で残す（DOM から外さない）── 外すと ref が
    // 空になり、次の spec 変更で「取り直す土台が無い」まま固まってしまう。
    const el = containerRef.current
    if (!el) return
    let cancelled = false
    let finalize: (() => void) | undefined
    setFailed(false)
    void (async () => {
      try {
        const embed = (await import('vega-embed')).default
        const result = await embed(el, withHouseStyle(spec), {
          actions: false,
          renderer: 'svg',
          tooltip: true,
        })
        if (cancelled) {
          result.finalize()
          return
        }
        finalize = () => result.finalize()
      } catch {
        if (!cancelled) setFailed(true)
      }
    })()
    return () => {
      cancelled = true
      finalize?.()
    }
  }, [spec])

  return (
    <div className="cardview-vega-wrap" style={{ overflow: 'hidden' }}>
      <div
        ref={containerRef}
        className="cardview-vega"
        role="img"
        aria-label={ariaLabel}
        style={{ height, overflow: 'hidden' }}
        hidden={failed}
      />
      {failed && <p className="ds-empty-note">{t('cards:render_error')}</p>}
    </div>
  )
}
