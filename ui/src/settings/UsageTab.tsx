import { useEffect, useMemo, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { useLlmSettings } from './context'
import { cacheMultipliers } from './model-pricing'
import type { RateCurrency, TokenRate } from './store'
import { type UsageEvent, fetchUsage } from './usageApi'

type Granularity = 'day' | 'month' | 'year'

const USD_JPY_KEY = 'asterism.usdJpy'

// The usage dashboard: pulls token-count events from the backend ledger, joins
// them with the model rate table (user-set rate, else a built-in reference
// price), and shows totals + a per-bucket bar chart + a feature/model breakdown.
// Cost is computed here at display time, so editing a rate re-prices history.
export function UsageTab() {
  const { t } = useTranslation('settings')
  const { models } = useLlmSettings()
  const [events, setEvents] = useState<UsageEvent[] | null>(null)
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(true)
  const [gran, setGran] = useState<Granularity>('day')
  const [currency, setCurrency] = useState<RateCurrency>('jpy')
  const [usdJpy, setUsdJpy] = useState<number>(() => {
    const raw = Number.parseFloat(localStorage.getItem(USD_JPY_KEY) ?? '')
    return Number.isFinite(raw) && raw > 0 ? raw : 150
  })

  useEffect(() => {
    let cancelled = false
    // `loading` starts true; this effect runs once and flips it in finally.
    fetchUsage()
      .then((r) => !cancelled && (setEvents(r.events), setError('')))
      .catch((e) => !cancelled && setError(e instanceof Error ? e.message : String(e)))
      .finally(() => !cancelled && setLoading(false))
    return () => {
      cancelled = true
    }
  }, [])

  function onUsdJpy(v: string) {
    const n = Number.parseFloat(v)
    if (Number.isFinite(n) && n > 0) {
      setUsdJpy(n)
      localStorage.setItem(USD_JPY_KEY, String(n))
    }
  }

  // Resolve a token rate for an event from the user-set model rate (no built-in
  // price table — see model-pricing). No rate → cost counts as 0 (shown as "—").
  const rateFor = useMemo(() => {
    return (provider: string, modelId: string): TokenRate | null => {
      const m = models.find((x) => x.provider === provider && x.modelId === modelId && x.rate)
      return m?.rate ?? null
    }
  }, [models])

  const eventCost = useMemo(() => {
    return (ev: UsageEvent): number => {
      const rate = rateFor(ev.provider, ev.model_id)
      if (!rate) return 0
      // Cache prices are a fixed fraction of the input price (provider-specific).
      const mult = cacheMultipliers(ev.provider)
      const native =
        (ev.input_tokens * rate.input +
          ev.output_tokens * rate.output +
          ev.cache_read_tokens * (rate.input * mult.read) +
          ev.cache_write_tokens * (rate.input * mult.write)) /
        1_000_000
      return convertCost(native, rate.currency, currency, usdJpy)
    }
  }, [rateFor, currency, usdJpy])

  const view = useMemo(() => {
    const evs = events ?? []
    let totalTokens = 0
    let totalCost = 0
    let unratedTokens = 0
    const buckets = new Map<string, { tokens: number; cost: number }>()
    // feature -> modelId -> {tokens, cost}
    const breakdown = new Map<string, Map<string, { tokens: number; cost: number }>>()
    for (const ev of evs) {
      const tokens =
        ev.input_tokens + ev.output_tokens + ev.cache_read_tokens + ev.cache_write_tokens
      const cost = eventCost(ev)
      totalTokens += tokens
      totalCost += cost
      if (!rateFor(ev.provider, ev.model_id)) unratedTokens += tokens
      const bk = bucketKey(ev.ts, gran)
      const b = buckets.get(bk) ?? { tokens: 0, cost: 0 }
      b.tokens += tokens
      b.cost += cost
      buckets.set(bk, b)
      let byModel = breakdown.get(ev.feature)
      if (!byModel) {
        byModel = new Map()
        breakdown.set(ev.feature, byModel)
      }
      const mid = ev.model_id || '(unknown)'
      const row = byModel.get(mid) ?? { tokens: 0, cost: 0 }
      row.tokens += tokens
      row.cost += cost
      byModel.set(mid, row)
    }
    const bucketList = [...buckets.entries()].sort((a, b) => a[0].localeCompare(b[0]))
    const maxTokens = Math.max(1, ...bucketList.map(([, v]) => v.tokens))
    return { totalTokens, totalCost, unratedTokens, bucketList, maxTokens, breakdown }
  }, [events, eventCost, rateFor, gran])

  if (loading) return <p className="settings-intro">{t('usage.loading')}</p>
  if (error) {
    return (
      <div>
        <p className="settings-intro">{t('usage.intro')}</p>
        <p className="usage-empty">{t('usage.error')}</p>
      </div>
    )
  }

  const featureRows = [...view.breakdown.entries()].sort((a, b) => {
    const at = [...a[1].values()].reduce((s, r) => s + r.tokens, 0)
    const bt = [...b[1].values()].reduce((s, r) => s + r.tokens, 0)
    return bt - at
  })

  return (
    <div className="usage-tab">
      <p className="settings-intro">{t('usage.intro')}</p>

      <div className="usage-totals">
        <div className="usage-total">
          <span className="num">{formatTokens(view.totalTokens)}</span>
          <span className="lbl">{t('usage.totalTokens')}</span>
        </div>
        <div className="usage-total">
          <span className="num">{formatCost(view.totalCost, currency)}</span>
          <span className="lbl">{t('usage.totalCost')}</span>
        </div>
      </div>

      {/* 単価未設定モデルの使用分はコスト 0 として合算される — 黙って少なく
          見せない（実支出より小さい合計だと誤解される） */}
      {view.unratedTokens > 0 && (
        <p className="field-help usage-unrated-note">
          {t('usage.unratedNote', { tokens: formatTokens(view.unratedTokens) })}
        </p>
      )}

      <div className="usage-controls">
        <div className="settings-seg" role="group">
          {(['day', 'month', 'year'] as Granularity[]).map((g) => (
            <button
              key={g}
              type="button"
              className={gran === g ? 'active' : ''}
              onClick={() => setGran(g)}
            >
              {t(`usage.granularity.${g}`)}
            </button>
          ))}
        </div>
        <div className="currency-toggle" role="group">
          {(['usd', 'jpy'] as RateCurrency[]).map((c) => (
            <button
              key={c}
              type="button"
              className={`currency-btn${currency === c ? ' active' : ''}`}
              onClick={() => setCurrency(c)}
            >
              {c === 'usd' ? 'USD ($)' : 'JPY (¥)'}
            </button>
          ))}
        </div>
        <label className="usage-rate-input">
          {t('usage.usdJpyRate')}
          <input
            type="number"
            step="any"
            min="0"
            value={usdJpy}
            onChange={(e) => onUsdJpy(e.target.value)}
          />
        </label>
      </div>

      {view.bucketList.length === 0 ? (
        <p className="usage-empty">{t('usage.empty')}</p>
      ) : (
        <>
          <div className="usage-bars">
            {view.bucketList.map(([key, v]) => (
              <div
                key={key}
                className="usage-bar"
                title={`${key} · ${formatTokens(v.tokens)} · ${formatCost(v.cost, currency)}`}
              >
                <div
                  className="bar"
                  style={{ height: `${Math.max(2, (v.tokens / view.maxTokens) * 100)}%` }}
                />
                <span className="bk">{shortBucket(key, gran)}</span>
              </div>
            ))}
          </div>

          <table className="usage-table">
            <thead>
              <tr>
                <th>{t('usage.feature')}</th>
                <th>{t('usage.model')}</th>
                <th className="num">{t('usage.tokens')}</th>
                <th className="num">{t('usage.cost')}</th>
              </tr>
            </thead>
            <tbody>
              {featureRows.flatMap(([feature, byModel]) =>
                [...byModel.entries()]
                  .sort((a, b) => b[1].tokens - a[1].tokens)
                  .map(([modelId, row], i) => (
                    <tr key={`${feature}:${modelId}`}>
                      <td className="feat">{i === 0 ? featureLabel(t, feature) : ''}</td>
                      <td className="mdl">{modelId}</td>
                      <td className="num">{formatTokens(row.tokens)}</td>
                      <td className="num">{formatCost(row.cost, currency)}</td>
                    </tr>
                  )),
              )}
            </tbody>
          </table>
        </>
      )}
    </div>
  )
}

// ---- helpers ----

function featureLabel(t: (k: string) => string, feature: string): string {
  const label = t(`usage.features.${feature}`)
  // i18next returns the key path when missing; fall back to the raw feature.
  return label.startsWith('usage.features.') ? feature : label
}

function convertCost(amount: number, from: RateCurrency, to: RateCurrency, usdJpy: number): number {
  if (from === to) return amount
  if (from === 'usd' && to === 'jpy') return amount * usdJpy
  if (from === 'jpy' && to === 'usd') return amount / usdJpy
  return amount
}

function bucketKey(ts: string, gran: Granularity): string {
  if (gran === 'year') return ts.slice(0, 4)
  if (gran === 'month') return ts.slice(0, 7)
  return ts.slice(0, 10)
}

function shortBucket(key: string, gran: Granularity): string {
  if (gran === 'day') return key.slice(5) // MM-DD
  return key
}

function formatTokens(n: number): string {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`
  if (n >= 1_000) return `${(n / 1_000).toFixed(1)}K`
  return String(n)
}

function formatCost(n: number, currency: RateCurrency): string {
  if (n === 0) return '—'
  if (currency === 'jpy') {
    if (n < 1) return '<¥1'
    return `¥${Math.round(n).toLocaleString()}`
  }
  if (n < 0.01) return '<$0.01'
  return `$${n.toFixed(2)}`
}
