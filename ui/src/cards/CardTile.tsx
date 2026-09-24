// 1 件のページ／絞り込みページの格子に並ぶ 1 カード。`defaultCardsForSubject`/
// `defaultCardsForSet` が返す 1 件（tool + params）を `runCard` で実行し、
// `defaultViewFor` → PR B の描画器（VegaLiteView/TableView/GraphView）に渡す。
//
// SubjectPage.tsx と SetPage.tsx の両方が使うので共通化した（新設・c2-pages の
// 担当外だが、両ページで同じ 80 行ほどを重複させないための追加。notes に記載）。
//
// クリックの扱い: 見出し（タイトル）は常にカード詳細を開く。カード本体も、
// 「順位表で行に主語（IRI）が乗っている」場合を除いて詳細を開く — 順位表の
// 行クリックは別の意味（その 1 件のページへ）を持つため、そこだけは本体の
// クリックを詳細開きに使わない（TableView の onRowClick は DOM イベントを
// 渡さないので stopPropagation で親のクリックを止められず、二重発火を避ける
// ための設計）。
import { useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { runCard } from './cardsApi'
import type { CardRef, CardToolResult, SubjectKey } from './cardsApi'
import { resolveCardTitle } from './cardTitle'
import { withFieldLabels } from './builtinFields'
import { useCardPresentation } from './cardPresentation'
import { GraphView } from './GraphView'
import { viewFor } from './presentation'
import { isDefinitionGapValue } from './placeShape'
import { TableView } from './TableView'
import type { GraphSpec, TableSpec, ViewSpec, VegaLiteSpec } from './viewSpec'
import { VegaLiteView } from './VegaLiteView'
import './pages.css'

/** 定義不備の定数（`value_iri === property_iri`）を「（値なし）」に落とす
 *  （契約 §4「事実の表」）。行そのものを書き換えず、新しい配列を返す。 */
function maskDefinitionGapValues(rows: Record<string, unknown>[], placeholder: string): Record<string, unknown>[] {
  return rows.map((row) =>
    isDefinitionGapValue(row.value_iri, row.property_iri) ? { ...row, value_iri: undefined, value: placeholder } : row,
  )
}

/** ページ上のカードで `facts` 表を切る行数。カード詳細（`CardDetail.tsx`）は
 *  切らずに全件出す — §2(a)。 */
const FACTS_TILE_LIMIT = 12

export interface CardTileProps {
  subject: SubjectKey
  card: CardRef
  onOpenDetail: (cardId: string) => void
  /** 順位表の行を押したときに、その行の主語（IRI）の 1 件ページへ。 */
  onOpenSubject: (iri: string) => void
  /** `subject_flow` が `found: false` を返したとき、親にグリッドから外すよう
   *  知らせる（契約メモ §3.1「辺が 0 なら found: false を返し、UI はカードを
   *  出さない」）。 */
  onFoundChange?: (cardId: string, found: boolean) => void
  /** 格子で 2 列ぶんを占めるか。無指定なら output_kind から決める
   *  （flow／facts は既定で wide・§2(b)）。 */
  wide?: boolean
}

export function CardTile({ subject, card, onOpenDetail, onOpenSubject, onFoundChange, wide }: CardTileProps) {
  const { t } = useTranslation('cards')
  // カード詳細（`CardDetail.tsx`）で選んだ見せ方を、同じキー
  // （`asterism.cardView.<card_id>`）で読むだけ（一覧側に切替 UI は出さない —
  // 契約メモ §1.4）。
  const { presentation } = useCardPresentation(card.card_id)
  // 呼び出しの実体（subject + tool + params）を文字列化して依存キーにする —
  // 親が `subject={{kind:'individual', iri}}` のようにインライン literal を渡す
  // と毎レンダリングで参照が変わるため、オブジェクト参照そのものを依存にすると
  // ask 入力欄の入力ごとに再実行されてしまう。契約メモの card_id は
  // subject+tool+params のハッシュなので、この depKey が変わるのは常に
  // card.card_id が変わるとき（= 親の .map の key で自然に再マウントされる）
  // と一致する。
  const depKey = JSON.stringify({ subject, tool: card.tool, params: card.params })
  // state 自身に「どの depKey に対する結果か」を持たせる（effect の本体先頭で
  // setState して同期的にリセットしない — react-hooks/set-state-in-effect。
  // ProvenanceTrace.tsx と同じ流儀）。
  const [fetched, setFetched] = useState<{ key: string; result: CardToolResult | null; error: boolean }>({
    key: '',
    result: null,
    error: false,
  })

  useEffect(() => {
    let cancelled = false
    runCard(subject, card.tool, card.params)
      .then((r) => {
        if (cancelled) return
        setFetched({ key: depKey, result: r, error: false })
        if (r.found === false) onFoundChange?.(card.card_id, false)
      })
      .catch(() => {
        if (!cancelled) setFetched({ key: depKey, result: null, error: true })
      })
    return () => {
      cancelled = true
    }
    // onFoundChange は毎レンダリングで作り直される親のクロージャなので依存に
    // 入れない。
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [depKey])

  const loaded = fetched.key === depKey
  const result = loaded ? fetched.result : null
  const error = loaded && fetched.error

  const titleInfo = resolveCardTitle(card.title)
  const titleText = titleInfo.isKey ? t(titleInfo.value) : titleInfo.value

  // 定義不備の定数（value_iri === property_iri）は表示前に「（値なし）」へ
  // 落とす（契約 §4「事実の表」）。ここで一度だけ変換し、以降はこの rows を使う。
  const rows = result ? maskDefinitionGapValues(result.items, t('builtin.value_missing')) : []

  const view =
    result && card.output_kind !== 'flow'
      ? viewFor(
          { name: card.tool, title: card.title, output_kind: result.output_kind, item: withFieldLabels(card.tool, result.item, t) },
          rows,
          presentation,
        )
      : null
  const rankedSpec = view && view.lang === 'table' ? (view.spec as TableSpec) : null
  const isRankedWithSubject = !!rankedSpec && rankedSpec.variant === 'ranked' && !!rankedSpec.subject_field

  // facts 表はページ上では 12 行に切る（超えるときはカード下部に「すべて見る」
  // リンク）。defaultView.ts の決定論は変えず、CardTile 側で TableSpec.limit
  // を上書きする（§2(a)）。
  const factsTotal = rows.length
  const isFactsTable = card.output_kind === 'facts' && view && view.lang === 'table'
  const factsTruncated = isFactsTable && factsTotal > FACTS_TILE_LIMIT
  const tileView =
    isFactsTable && view
      ? { ...view, spec: { ...(view.spec as TableSpec), limit: FACTS_TILE_LIMIT } }
      : view

  const isWide = wide ?? (card.output_kind === 'flow' || card.output_kind === 'facts')

  return (
    <div
      className={isWide ? 'card cardpage-tile cardpage-tile--wide' : 'card cardpage-tile'}
      role={isRankedWithSubject ? undefined : 'button'}
      tabIndex={isRankedWithSubject ? undefined : 0}
      onClick={isRankedWithSubject ? undefined : () => onOpenDetail(card.card_id)}
      onKeyDown={
        isRankedWithSubject
          ? undefined
          : (e) => {
              if (e.key === 'Enter' || e.key === ' ') {
                e.preventDefault()
                onOpenDetail(card.card_id)
              }
            }
      }
    >
      <div className="cardpage-tile-head">
        <button
          type="button"
          className="cardpage-tile-title"
          onClick={(e) => {
            e.stopPropagation()
            onOpenDetail(card.card_id)
          }}
        >
          {titleText}
        </button>
        <span className="cardpage-tile-pills">
          <span className="cardpage-kind">{t(`kind.${card.output_kind}`)}</span>
          {result && result.shareable !== null && (
            <span className={result.shareable ? 'pill-share pill-share--ok' : 'pill-share pill-share--warn'}>
              {t(result.shareable ? 'page.shareable_yes' : 'page.shareable_no')}
            </span>
          )}
        </span>
      </div>
      <div className="cardpage-tile-body">
        {error && <p className="ds-empty-note">{t('render_error')}</p>}
        {!error && !result && <p className="ds-empty-note">{t('page.loading')}</p>}
        {!error && result && card.output_kind === 'flow' && (
          <GraphView
            graph={(result.graph ?? { nodes: [], edges: [] }) as GraphSpec}
            ariaLabel={titleText}
            maxHeight={200}
          />
        )}
        {!error && result && card.output_kind !== 'flow' && tileView && (
          <CardTileBody
            view={tileView}
            rows={rows}
            ariaLabel={titleText}
            onOpenSubject={onOpenSubject}
            emptyText={t('empty')}
          />
        )}
        {factsTruncated && (
          <button
            type="button"
            className="link-btn cardpage-tile-more"
            onClick={(e) => {
              e.stopPropagation()
              onOpenDetail(card.card_id)
            }}
          >
            {t('tile.show_all', { n: factsTotal })}
          </button>
        )}
      </div>
    </div>
  )
}

function CardTileBody({
  view,
  rows,
  ariaLabel,
  onOpenSubject,
  emptyText,
}: {
  view: ViewSpec
  rows: Record<string, unknown>[]
  ariaLabel: string
  onOpenSubject: (iri: string) => void
  emptyText: string
}) {
  if (rows.length === 0) return <p className="ds-empty-note">{emptyText}</p>
  if (view.lang === 'vega-lite') {
    return <VegaLiteView spec={view.spec as VegaLiteSpec} ariaLabel={ariaLabel} height={200} />
  }
  if (view.lang === 'table') {
    const spec = view.spec as TableSpec
    const subjectField = spec.subject_field
    return (
      <TableView
        spec={spec}
        rows={rows}
        ariaLabel={ariaLabel}
        onRowClick={
          subjectField
            ? (row) => {
                const iri = row[subjectField]
                if (typeof iri === 'string') onOpenSubject(iri)
              }
            : undefined
        }
      />
    )
  }
  return null
}
