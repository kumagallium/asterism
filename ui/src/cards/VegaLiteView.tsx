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
      } catch (err) {
        // vega-embed の失敗理由（例: encoding.field が行に存在しない）を
        // 開発者コンソールに出す ── 画面には「図を描けませんでした」としか
        // 出ないため、原因追跡に本文が必須。
        console.error('VegaLiteView: failed to render spec', err, spec)
        if (!cancelled) setFailed(true)
      }
    })()
    return () => {
      cancelled = true
      finalize?.()
    }
  }, [spec])

  return (
    // 埋め込み先の div は幅・高さとも `container`（`withHouseStyle`）に
    // 追従する ── レイアウト前の初期描画でラップ自身の高さが 0 だと
    // vega-embed が「取り直す土台」を持てないため、下の `cardview-vega`
    // の明示 height に加えてここにも min-height を持たせておく。
    <div className="cardview-vega-wrap" style={{ overflow: 'hidden', minHeight: height }}>
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
