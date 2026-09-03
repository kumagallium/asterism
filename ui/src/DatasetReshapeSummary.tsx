// 詳細モードの「取り込んだファイル」タブに出す「表の形」の要約
// （ADR source-reshape.md R19・最小段）。K1（詳細モードで機能を削らない）と
// K4（専門表記は詳細モードへ逃がす）に沿い、op の種類・派生表と行数を平易な
// 見出しで、判断表そのもの（spec の生 JSON）は畳んだ技術情報として出す。
// 編集はまだ無い — PUT /api/datasets/{id}/reshape は次の段。
import { useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'
import type { TFunction } from 'i18next'

import { type DatasetReshapeLedger, type ReshapeSpec, getDatasetReshape } from './api'

/** 派生表 1 枚の平易な呼び名（K4「識別子を生で見せない、見せるなら出自の
 *  言葉で」）。pivot は群の代表表記＋単位（ReshapeGate と同じ語り方）、
 *  flatten は細長い表／列にした表のどちらか、explode は op の種類名。 */
function tableLabel(t: TFunction, spec: ReshapeSpec, name: string, opIndex: number): string {
  const op = spec.ops[opIndex]
  if (op?.kind === 'pivot') {
    const g = op.groups.find((g) => g.table === name)
    if (g) return `${g.label}（${g.unit}）`
  } else if (op?.kind === 'flatten') {
    if (op.long.table === name) return t('gallery:files.reshapeLongTable')
    if (op.wide.table === name) return t('gallery:files.reshapeWideTable')
  }
  return t(`gallery:files.reshapeKind.${op?.kind ?? 'pivot'}`)
}

export function DatasetReshapeSummary({ datasetId }: { datasetId: string }) {
  const { t } = useTranslation()
  const [ledger, setLedger] = useState<DatasetReshapeLedger | null>(null)
  const [loaded, setLoaded] = useState(false)

  // datasetId が変わるとこのコンポーネントごと作り直される（呼び出し側が
  // `key={datasetId}` を付ける）ので、この effect の中で state を初期化に
  // 戻す必要は無い — 読み込みが済み次第、結果を1回だけ書く。
  useEffect(() => {
    let cancelled = false
    void getDatasetReshape(datasetId)
      .then((l) => {
        if (!cancelled) setLedger(l)
      })
      .catch(() => {
        // 404 = このデータセットは「表の形」を一度も通っていない（ほとんどの
        // データセット）— それ以外の失敗も含め、この欄は上乗せの参考情報
        // （enrichment）なので黙って何も出さない。
      })
      .finally(() => {
        if (!cancelled) setLoaded(true)
      })
    return () => {
      cancelled = true
    }
  }, [datasetId])

  if (!loaded || !ledger) return null

  const tables = Object.entries(ledger.spec.tables ?? {})

  return (
    <div className="ds-reshape-summary">
      <div className="ds-subhead">{t('gallery:files.reshapeTitle')}</div>
      <ul className="ds-reshape-summary-list">
        {ledger.spec.ops.map((op, i) => (
          <li key={i}>{t(`gallery:files.reshapeKind.${op.kind}`)}</li>
        ))}
      </ul>
      {tables.length > 0 && (
        <ul className="ds-reshape-summary-list">
          {tables.map(([name, meta]) => {
            const rows = ledger.counts[String(meta.op)]?.tables?.[name]
            return (
              <li key={name}>
                {/* K4: 識別子（表名）を単独で見せず、代表表記＋単位のような
                    平易な呼び名を主にし、機械的な表名は控えめな添え書きにする。 */}
                {tableLabel(t, ledger.spec, name, meta.op)}{' '}
                <span className="ds-file-tag">
                  <code>{name}</code>
                </span>
                {rows !== undefined && (
                  <>
                    {' — '}
                    {rows.toLocaleString()} {t('gallery:files.reshapeRowsSuffix')}
                  </>
                )}
              </li>
            )
          })}
        </ul>
      )}
      {ledger.stale.length > 0 && (
        <p className="ds-empty-note">
          {t('gallery:files.reshapeStale', { count: ledger.stale.length })}
        </p>
      )}
      <details className="ds-advisory-raw">
        <summary>{t('gallery:files.reshapeRawSummary')}</summary>
        <pre className="rules-code-block">{JSON.stringify(ledger.spec, null, 2)}</pre>
      </details>
    </div>
  )
}
