import type { ReactNode } from 'react'
import { useTranslation } from 'react-i18next'
import { defaultViewFor } from './defaultView'
import './gallery.css'
import { GraphView } from './GraphView'
import { parseMermaidFlowchart } from './mermaidFlow'
import { TableView } from './TableView'
import type { OutputKind, Row, TableSpec, ToolContract, VegaLiteSpec } from './viewSpec'
import { VegaLiteView } from './VegaLiteView'

/**
 * 見本ページ（スクショ用・裏タブ `#/cardsdemo`）。§1〜§5 の描画器 3 つが
 * 既定ビューの決定論（`defaultViewFor`）を通してどう見えるかを、架空の
 * 2 分野（図書館の貸出／気象観測）の固定データで並べる。実データには繋がない。
 *
 * 各カードの下の「材料」「作りかた」は見た目だけ（押しても何もしない・Phase 1
 * の C で結線）。ページ最下部の 1 行入力も見た目だけ。
 */

// ── 見本データ（ファイル内定数。実データ API には繋がない） ─────────────────

const tool = (
  name: string,
  title: string,
  output_kind: OutputKind,
  item: ToolContract['item'],
): ToolContract => ({ name, title, output_kind, item })

const QUANTITY: { tool: ToolContract; rows: Row[] } = {
  tool: tool('monthly_checkouts', '今月の貸出冊数', 'quantity', {
    checkout_count: { var: 'checkout_count', role: 'value', number: true, unit: '冊' },
    checked_out_at: { var: 'checked_out_at', role: 'at' },
    branch_name: { var: 'branch_name', role: 'label' },
  }),
  rows: [{ checkout_count: 428, checked_out_at: '2026-09', branch_name: '中央館' }],
}

const SERIES: { tool: ToolContract; rows: Row[] } = {
  tool: tool('monthly_rainfall', '観測地点別の月別降水量', 'series', {
    observed_month: { var: 'observed_month', role: 'x', number: false },
    rainfall: { var: 'rainfall', role: 'y', number: true, unit: 'mm' },
    station_name: { var: 'station_name', role: 'series' },
  }),
  rows: [
    { observed_month: '5月', rainfall: 88, station_name: '北観測所' },
    { observed_month: '6月', rainfall: 142, station_name: '北観測所' },
    { observed_month: '7月', rainfall: 176, station_name: '北観測所' },
    { observed_month: '8月', rainfall: 121, station_name: '北観測所' },
    { observed_month: '5月', rainfall: 54, station_name: '南観測所' },
    { observed_month: '6月', rainfall: 97, station_name: '南観測所' },
    { observed_month: '7月', rainfall: 133, station_name: '南観測所' },
    { observed_month: '8月', rainfall: 88, station_name: '南観測所' },
  ],
}

const PAIRS: { tool: ToolContract; rows: Row[] } = {
  tool: tool('pages_vs_loan_days', 'ページ数と貸出日数の関係', 'pairs', {
    pages: { var: 'pages', role: 'x', number: true },
    loan_days: { var: 'loan_days', role: 'y', number: true },
  }),
  rows: [
    { pages: 120, loan_days: 7 },
    { pages: 260, loan_days: 10 },
    { pages: 340, loan_days: 14 },
    { pages: 180, loan_days: 8 },
    { pages: 410, loan_days: 21 },
    { pages: 90, loan_days: 5 },
  ],
}

const RANKED: { tool: ToolContract; rows: Row[] } = {
  tool: tool('rainiest_stations', '降水量の多い観測地点', 'ranked', {
    station_name: { var: 'station_name', role: 'label' },
    rainfall: { var: 'rainfall', role: 'value', number: true, unit: 'mm' },
    station_iri: { var: 'station_iri', role: 'subject' },
  }),
  rows: [
    { station_name: '南観測所', rainfall: 372, station_iri: 'https://example.org/station/south' },
    { station_name: '北観測所', rainfall: 527, station_iri: 'https://example.org/station/north' },
    { station_name: '東観測所', rainfall: 245, station_iri: 'https://example.org/station/east' },
  ],
}

const BREAKDOWN: { tool: ToolContract; rows: Row[] } = {
  tool: tool('checkouts_by_genre', 'ジャンル別の貸出件数', 'breakdown', {
    genre: { var: 'genre', role: 'category' },
    title_count: { var: 'title_count', role: 'count', number: true },
  }),
  rows: [
    { genre: '小説', title_count: 88 },
    { genre: '実用書', title_count: 61 },
    { genre: '絵本', title_count: 47 },
    { genre: 'その他', title_count: 23 },
  ],
}

const FACTS: { tool: ToolContract; rows: Row[] } = {
  tool: tool('station_facts', '観測地点の基本情報', 'facts', {
    station_name: { var: 'station_name' },
    elevation: { var: 'elevation', number: true, unit: 'm' },
    // 年は桁区切りを出したくない（1,978 は不自然）ので number: false にして text 表示にする。
    established: { var: 'established', number: false },
    station_iri: { var: 'station_iri' },
  }),
  rows: [
    {
      station_name: '北観測所',
      elevation: 320,
      established: 1978,
      station_iri: 'https://example.org/station/north',
    },
  ],
}

const NUMERIC_CARDS = [QUANTITY, SERIES, PAIRS, RANKED, BREAKDOWN, FACTS]

const FLOW_TITLE = '貸出の手順'
const FLOW_TEXT = `graph LR
  request[予約]:::entity --> lend[貸出]:::activity
  lend --> give_back[返却]:::activity
  give_back --> reshelve[戻す]:::entity`

// ── 部品 ─────────────────────────────────────────────────────────────────

function CardShell({
  title,
  kind,
  children,
  wide = false,
}: {
  title: string
  kind: OutputKind
  children: ReactNode
  wide?: boolean
}) {
  const { t } = useTranslation('cards')
  const cls = wide ? 'card cardview-card cardview-card--wide' : 'card cardview-card'
  return (
    <div className={cls}>
      <div className="cardview-card-head">
        <b className="card-h">{title}</b>
        <span className="cardview-kind">{t(`kind.${kind}`)}</span>
      </div>
      <div className="cardview-card-body">{children}</div>
      <div className="cardview-card-foot">
        {/* 見た目だけ（押しても何もしない）。見本では disabled にして押せない見た目
            にする（チェッカー指摘: 押せそうに見えて何もしないのは K39 違反）。 */}
        <button
          type="button"
          className="cardview-card-link"
          disabled
          title={t('gallery.linkDisabled')}
        >
          {t('materials')}
        </button>
        <button
          type="button"
          className="cardview-card-link"
          disabled
          title={t('gallery.linkDisabled')}
        >
          {t('recipe')}
        </button>
      </div>
    </div>
  )
}

function DemoCard({ tool: t, rows }: { tool: ToolContract; rows: Row[] }) {
  const view = defaultViewFor(t, rows)
  return (
    <CardShell title={t.title} kind={t.output_kind}>
      {view.lang === 'vega-lite' && (
        <VegaLiteView spec={view.spec as VegaLiteSpec} ariaLabel={t.title} height={200} />
      )}
      {view.lang === 'table' && (
        <TableView spec={view.spec as TableSpec} rows={rows} ariaLabel={t.title} />
      )}
    </CardShell>
  )
}

function FlowCard() {
  const { graph } = parseMermaidFlowchart(FLOW_TEXT)
  return (
    <CardShell title={FLOW_TITLE} kind="flow" wide>
      <GraphView graph={graph} ariaLabel={FLOW_TITLE} maxHeight={200} compact />
    </CardShell>
  )
}

export function CardsGallery() {
  const { t } = useTranslation('cards')
  return (
    <div className="cardview-gallery">
      <p className="cardview-gallery-note">{t('demo_note')}</p>
      <div className="cardview-grid">
        {NUMERIC_CARDS.map((c) => (
          <DemoCard key={c.tool.name} tool={c.tool} rows={c.rows} />
        ))}
        <FlowCard />
      </div>
      <div className="cardview-ask" aria-hidden="true">
        <span className="cardview-ask-input">{t('ask_placeholder')}</span>
      </div>
    </div>
  )
}
