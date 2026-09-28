// カード詳細（試作 画面 3）。パンくず（1 件/絞り込みのラベル ／ カード）→
// 見出し（カード title）→ タブ「結果／材料／作りかた」。契約メモ §6.3。
import { useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { applyPresentation } from './applyPresentation'
import { KNOWN_LICENSE_IDS, putDatasetLicense, runCard } from './cardsApi'
import type { CardRef, CardMaterial, CardToolResult, CardView, SubjectKey } from './cardsApi'
import { resolveCardTitle } from './cardTitle'
import { withFieldLabels } from './builtinFields'
import { defaultViewFor } from './defaultView'
import { GraphView } from './GraphView'
import { parseMermaidFlowchart } from './mermaidFlow'
// PR F18（ui-store 担当）が新設する型。まだ存在しない間もこの担当（ui-page）は
// 契約メモ §1.3 どおりに import だけ書いておき、統合段で繋ぐ（契約メモ §1
// 「並列中の仮置き」と同じ流儀）。
import type { PageChatTurn } from './pageChatThreads'
import { isDefinitionGapValue } from './placeShape'
import './pages.css'
import { formatShareReasons } from './shareReasons'
import { TableView } from './TableView'
import type { GraphSpec, TableSpec, ViewSpec, VegaLiteSpec } from './viewSpec'
import { VegaLiteView } from './VegaLiteView'

// PR F13: AI が Vega-Lite／表仕様／Mermaid で「書いた」見せ方（`card.view`。
// `cardsApi.ts` の {@link CardView}）。CardTile.tsx と同じ変換。
function renderableCustomView(
  view: CardView | undefined,
  rows: Record<string, unknown>[],
): { view: ViewSpec } | { graph: GraphSpec } | null {
  if (!view) return null
  if (view.lang === 'vega-lite' && view.spec && typeof view.spec === 'object') {
    const spec = { ...view.spec, data: { values: rows } }
    return { view: { lang: 'vega-lite', spec: spec as VegaLiteSpec, custom: true } }
  }
  if (view.lang === 'table' && view.spec && typeof view.spec === 'object') {
    return { view: { lang: 'table', spec: view.spec as unknown as TableSpec, custom: true } }
  }
  if (view.lang === 'mermaid' && typeof view.text === 'string') {
    return { graph: parseMermaidFlowchart(view.text).graph }
  }
  return null
}

/** 定義不備の定数（`value_iri === property_iri`）を「（値なし）」に落とす
 *  （契約 §4「事実の表」）。CardTile.tsx と同じ判定・同じ流儀。 */
function maskDefinitionGapValues(rows: Record<string, unknown>[], placeholder: string): Record<string, unknown>[] {
  return rows.map((row) =>
    isDefinitionGapValue(row.value_iri, row.property_iri) ? { ...row, value_iri: undefined, value: placeholder } : row,
  )
}

export interface CardDetailProps {
  subject: SubjectKey
  /** パンくず・「<ラベル> に聞く」に使う、1 件/絞り込みの表示名（K4: 生の
   *  識別子ではなく、呼び出し側が resolve 済みのラベル）。 */
  breadcrumbLabel: string
  card: CardRef
  onBack: () => void
  onAsk: (question: string) => void
  /** 材料タブの「定義を直す」→ `#/datasets/<dataset_id>/design`。 */
  onEditDefinition: (datasetId: string) => void
  /** このカードが cardStore にある「足したカード」かどうか（PR F4 §1-5）。
   *  true かつ {@link onRemoveCard} が渡されているときだけ、タブの下部に
   *  「このカードを消す」を出す。既定カードは消せない。 */
  isAddedCard?: boolean
  /** 「このカードを消す」を押したときに呼ぶ（cardStore からの削除は呼び出し側
   *  の責務）。押下後は自動で {@link onBack} も呼ぶ。 */
  onRemoveCard?: () => void
  /** 「直す」を押したときに呼ぶ（契約メモ PR F18 §1.3）。会話ドロワーを
   *  このカードの会話で開くのは呼び出し側の責務。{@link isAddedCard} が
   *  true かつこの prop が渡されているときだけボタンを出す。 */
  onFixCard?: () => void
  /** このカードに結びついた会話（契約メモ PR F18 §1.3）。あれば「どう作ったか」
   *  タブに「会話の記録」を読むだけの節として出す。 */
  conversation?: PageChatTurn[]
}

type DetailTabId = 'result' | 'materials' | 'recipe'
type Translate = (key: string, options?: Record<string, unknown>) => string

export function CardDetail({
  subject,
  breadcrumbLabel,
  card,
  onBack,
  onAsk,
  onEditDefinition,
  isAddedCard,
  onRemoveCard,
  onFixCard,
  conversation,
}: CardDetailProps) {
  const { t } = useTranslation('cards')
  const depKey = JSON.stringify({ subject, tool: card.tool, params: card.params })
  // 結果は depKey で紐づけ、then/catch でだけ書き込む — effect の本体で同期的に
  // setState しない（react-hooks/set-state-in-effect。ProvenanceTrace.tsx／
  // CardTile.tsx と同じ流儀）。
  const [fetched, setFetched] = useState<{ key: string; result: CardToolResult | null; error: boolean }>({
    key: '',
    result: null,
    error: false,
  })
  // タブはカードが変わったら 'result' に戻す（React の「prop が変わったら
  // state を調整する」パターン — effect を使わない）。
  const [tab, setTab] = useState<DetailTabId>('result')
  const [tabFor, setTabFor] = useState(card.card_id)
  if (tabFor !== card.card_id) {
    setTabFor(card.card_id)
    setTab('result')
  }
  const [askText, setAskText] = useState('')
  // 「ライセンスを書く」の入力欄（材料タブ・licenses が不明の最初の材料に
  // 対して）。card_id が変わったら忘れる — 下の tab リセットと同じ流儀。
  const [licenseDraft, setLicenseDraft] = useState('')
  const [licenseBusy, setLicenseBusy] = useState(false)
  const [licenseError, setLicenseError] = useState<string | null>(null)

  useEffect(() => {
    let cancelled = false
    runCard(subject, card.tool, card.params)
      .then((r) => {
        if (!cancelled) setFetched({ key: depKey, result: r, error: false })
      })
      .catch(() => {
        if (!cancelled) setFetched({ key: depKey, result: null, error: true })
      })
    return () => {
      cancelled = true
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [depKey])

  const loaded = fetched.key === depKey
  const result = loaded ? fetched.result : null
  const error = loaded && fetched.error

  const titleInfo = resolveCardTitle(card.title, card.title_params)
  const titleText = titleInfo.isKey ? t(titleInfo.value, titleInfo.params) : titleInfo.value
  const hasCustomView = !!card.view
  const editMaterial: CardMaterial | undefined = result?.materials[0]
  const editDatasetId = editMaterial?.dataset_id
  const licenseUnknown = !!editMaterial && editMaterial.license == null

  async function handleWriteLicense() {
    if (!editDatasetId || !licenseDraft.trim()) return
    setLicenseBusy(true)
    setLicenseError(null)
    try {
      await putDatasetLicense(editDatasetId, licenseDraft.trim())
      // ライセンスは registry（mie.yaml/metadata.ttl）の正本を書き換えただけ
      // なので、カードを再実行して materials/shareable を作り直させる
      // （runCard は毎回 materials_for で読み直す — ここではキャッシュしない）。
      const refreshed = await runCard(subject, card.tool, card.params)
      setFetched({ key: depKey, result: refreshed, error: false })
      setLicenseDraft('')
    } catch {
      setLicenseError(t('license.write_error'))
    } finally {
      setLicenseBusy(false)
    }
  }

  return (
    <div className="cardpage-body">
      <div className="cardpage-head">
        <div>
          <div className="cardpage-crumb">
            <button type="button" className="link-btn" onClick={onBack}>
              {breadcrumbLabel}
            </button>
            {' / '}
            {t('detail.tab_result')}
          </div>
          <h2 className="cardpage-title">{titleText}</h2>
        </div>
        {hasCustomView && <span className="cardpage-kind">{t('tile.custom_view')}</span>}
        {result && result.shareable !== null && (
          <span className={result.shareable ? 'pill-share pill-share--ok' : 'pill-share pill-share--warn'}>
            {t(result.shareable ? 'page.shareable_yes' : 'page.shareable_no')}
          </span>
        )}
      </div>
      <div className="cardpage-tabs" role="tablist">
        {(['result', 'materials', 'recipe'] as const).map((id) => (
          <button
            key={id}
            type="button"
            role="tab"
            aria-selected={tab === id}
            className={tab === id ? 'cardpage-tab cardpage-tab--on' : 'cardpage-tab'}
            onClick={() => setTab(id)}
          >
            {t(id === 'result' ? 'detail.tab_result' : id === 'materials' ? 'materials' : 'recipe')}
          </button>
        ))}
      </div>
      <div className="cardpage-tab-body">
        {error && <p className="ds-empty-note">{t('render_error')}</p>}
        {!error && !result && <p className="ds-empty-note">{t('page.loading')}</p>}
        {!error && result && tab === 'result' && renderResultTab(card, result, titleText, t)}
        {!error && result && tab === 'materials' && renderMaterialsTab(subject, result, t)}
        {!error && result && tab === 'recipe' && renderRecipeTab(card, result, t, conversation)}
        {!error && result && tab === 'materials' && editDatasetId && (
          <div className="cardpage-materials-actions">
            <button
              type="button"
              className="btn btn--ghost btn--sm cardpage-edit-definition"
              onClick={() => onEditDefinition(editDatasetId)}
            >
              {t('detail.edit_definition')}
            </button>
            {licenseUnknown && (
              <div className="cardpage-license-form">
                <label className="cardpage-license-label" htmlFor="cardpage-license-input">
                  {t('license.write_label')}
                </label>
                <input
                  id="cardpage-license-input"
                  className="cardpage-license-input"
                  list="cardpage-known-license-ids"
                  value={licenseDraft}
                  onChange={(e) => setLicenseDraft(e.target.value)}
                  placeholder={t('license.write_placeholder')}
                />
                <datalist id="cardpage-known-license-ids">
                  {KNOWN_LICENSE_IDS.map((id) => (
                    <option key={id} value={id} />
                  ))}
                </datalist>
                <button
                  type="button"
                  className="btn btn--soft btn--sm"
                  disabled={!licenseDraft.trim() || licenseBusy}
                  onClick={handleWriteLicense}
                >
                  {t('license.write_submit')}
                </button>
                {licenseError && <span className="cardpage-license-error">{licenseError}</span>}
              </div>
            )}
          </div>
        )}
      </div>
      {isAddedCard && (onFixCard || onRemoveCard) && (
        <div className="cardpage-materials-actions cardpage-detail-actions-row">
          {onFixCard && (
            <button type="button" className="btn btn--ghost btn--sm" onClick={onFixCard}>
              {t('detail.fix')}
            </button>
          )}
          {onRemoveCard && (
            <button
              type="button"
              className="btn btn--ghost btn--sm"
              onClick={() => {
                onRemoveCard()
                onBack()
              }}
            >
              {t('detail.delete_card')}
            </button>
          )}
        </div>
      )}
      <div className="cardpage-bar">
        <span className="cardpage-bar-who">{t('page.ask_who', { label: breadcrumbLabel })}</span>
        <input
          className="cardpage-bar-input"
          value={askText}
          onChange={(e) => setAskText(e.target.value)}
          placeholder={t('ask_placeholder')}
        />
        <button
          type="button"
          className="btn btn--soft btn--sm"
          disabled={!askText.trim()}
          onClick={() => {
            onAsk(t('ask.compose', { label: breadcrumbLabel, text: askText }))
            setAskText('')
          }}
        >
          {t('page.ask_submit')}
        </button>
      </div>
    </div>
  )
}

function renderResultTab(card: CardRef, result: CardToolResult, ariaLabel: string, t: Translate) {
  // 定義不備の定数（value_iri === property_iri）は「（値なし）」に落とす
  // （契約 §4「事実の表」）。カード詳細は事実カードでも件数を切らない（全件・
  // §2(a)）— その全件に対して行う。
  const rows = maskDefinitionGapValues(result.items, t('builtin.value_missing'))

  // PR F13: AI が書いた見せ方（`card.view`）があれば既定描画の代わりにそれを
  // 使う（Mermaid は GraphSpec に変換して flow と同じ GraphView で描く）。
  const customRendered = renderableCustomView(card.view, rows)
  if (customRendered && 'graph' in customRendered) {
    return <GraphView graph={customRendered.graph} ariaLabel={ariaLabel} maxHeight={360} />
  }

  if (card.output_kind === 'flow') {
    // `result.graph` は cardsApi.ts の CardToolResult に合わせて緩い型
    // （nodes/edges: unknown[]）— subject_flow は prov_graph.graph をそのまま
    // 返す契約（契約メモ §3.1）なので、実体は GraphSpec の形をしている。
    const graph = (result.graph ?? { nodes: [], edges: [] }) as GraphSpec
    return <GraphView graph={graph} ariaLabel={ariaLabel} maxHeight={360} />
  }
  const view =
    customRendered && 'view' in customRendered
      ? customRendered.view
      : applyPresentation(
          defaultViewFor(
            { name: card.tool, title: card.title, output_kind: result.output_kind, item: withFieldLabels(card.tool, result.item, t) },
            rows,
          ),
          card.presentation,
        )
  if (view.lang === 'vega-lite') {
    return <VegaLiteView spec={view.spec as VegaLiteSpec} ariaLabel={ariaLabel} height={360} />
  }
  if (view.lang === 'table') {
    return <TableView spec={view.spec as TableSpec} rows={rows} ariaLabel={ariaLabel} />
  }
  return null
}

/** `material.kind`（'own' | 'open' | 'unknown'）の文言キー。契約メモ §2 の
 *  固定語彙どおり — サーバが返す以外の値は来ない想定だが、来ても保守側で
 *  「不明」に倒す（UI を落とさない）。 */
function materialKindLabel(kind: string, t: Translate): string {
  if (kind === 'own' || kind === 'open') return t(`detail.materials_kind_${kind}`)
  return t('detail.materials_kind_unknown')
}

function renderMaterialsTab(subject: SubjectKey, result: CardToolResult, t: Translate) {
  // Phase 1: materials は個体ごとの出典 IRI を持たない（契約メモ §3.3）ので、
  // 「出典を見る」は 1 件ページのときだけ、その主語の /describe へ向ける。
  const describeHref = subject.kind === 'individual' ? `/describe?iri=${encodeURIComponent(subject.iri)}` : null
  const reasons = result.shareable_reasons ?? []
  return (
    <div className="cardpage-materials">
      {result.materials.length === 0 ? (
        <p className="ds-empty-note">{t('detail.materials_empty')}</p>
      ) : (
        <div className="table-wrap">
          <table className="jobs-table">
            <thead>
              <tr>
                <th>{t('detail.materials_table_material')}</th>
                <th>{t('detail.materials_table_source')}</th>
                <th>{t('detail.materials_table_snapshot')}</th>
                <th>{t('detail.materials_table_license')}</th>
                <th aria-hidden="true" />
              </tr>
            </thead>
            <tbody>
              {result.materials.map((m, i) => (
                <tr key={`${m.dataset_id}-${m.snapshot ?? ''}-${i}`}>
                  <td>
                    {m.dataset_label}
                    <span className="cardpage-materials-count">
                      {' '}
                      {t('detail.materials_row_count', { count: m.count })}
                    </span>
                  </td>
                  <td>{materialKindLabel(m.kind, t)}</td>
                  <td>{m.snapshot ?? t('detail.materials_unknown')}</td>
                  <td>{m.license ?? t('license.unknown')}</td>
                  <td>
                    {describeHref && (
                      <a className="link-btn" href={describeHref} target="_blank" rel="noreferrer">
                        {t('detail.materials_table_link')}
                      </a>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {result.shareable !== null && (
        <p className={result.shareable ? 'cardview-verdict cardview-verdict--ok' : 'cardview-verdict cardview-verdict--warn'}>
          {result.shareable ? t('detail.verdict_ok') : t('detail.verdict_no', { reasons: formatShareReasons(reasons, t) })}
        </p>
      )}
    </div>
  )
}

function renderRecipeTab(card: CardRef, result: CardToolResult, t: Translate, conversation: PageChatTurn[] | undefined) {
  return (
    <div className="cardpage-recipe-tab">
      <div className="cardpage-bundle">
        <p className="cardpage-bundle-title">{t('bundle.title')}</p>
        <ul className="tree">
          <li>{t('bundle.facts')}</li>
          <li>{t('bundle.tools')}</li>
          <li>{t('bundle.cards')}</li>
          <li>{t('bundle.agent_md')}</li>
          <li>{t('bundle.mcp_json')}</li>
        </ul>
      </div>
      {conversation && conversation.length > 0 && renderConversationRecord(conversation, t)}
      {/* K4: 生の識別子・クエリは「技術情報」として折る。 */}
      <details className="cardpage-recipe">
        <summary>{t('detail.recipe_tech_info')}</summary>
        <p className="cardpage-recipe-line">
          <b>{t('detail.recipe_tool')}</b>: <code>{card.tool}</code>
        </p>
        <pre className="cardpage-recipe-pre">{JSON.stringify(card.params, null, 2)}</pre>
        <p className="cardpage-recipe-line">
          <b>{t('detail.recipe_sparql')}</b>
        </p>
        <pre className="cardpage-recipe-pre">{result.sparql}</pre>
      </details>
    </div>
  )
}

/** 提案の決着（`AssistantTurn.decision`・PR F18 §1.2）を「会話の記録」の
 *  文言キーに変換する。`added`／`added_as_new` はどちらも「足した」——
 *  記録としては元の会話がカードに結びついたか別カードとして足されたかの
 *  違いは意味を持たない。 */
function conversationOutcomeKey(decision: 'added' | 'replaced' | 'added_as_new' | 'discarded' | undefined): string | null {
  if (decision === 'added' || decision === 'added_as_new') return 'detail.conversation_outcome_added'
  if (decision === 'replaced') return 'detail.conversation_outcome_replaced'
  if (decision === 'discarded') return 'detail.conversation_outcome_discarded'
  return null
}

/** 「会話の記録」（契約メモ PR F18 §1.3）: このカードに結びついた会話の
 *  ターンを上から読むだけで並べる。あなたの発言はそのまま、AI の発言は
 *  提案が付いていれば「提案: <題名>（<結末>）」の 1 行に、無ければ返信文
 *  そのままにする。決着（足した／差し替えた／やめた）はターン自体の
 *  `decision`（PageChatDrawer.tsx が決着のたびに書く）から出す — 決着して
 *  いない（まだ選ばれていない）古い提案は結末を書かず題名だけ出す。 */
function renderConversationRecord(conversation: PageChatTurn[], t: Translate) {
  return (
    <div className="cardpage-conversation">
      <p className="cardpage-bundle-title">{t('detail.conversation')}</p>
      <ul className="cardpage-conversation-list">
        {conversation.map((turn) => {
          if (turn.role === 'user') {
            return (
              <li key={turn.id} className="cardpage-conversation-turn">
                <b>{t('detail.conversation_you')}</b>: {turn.text}
              </li>
            )
          }
          const proposal = turn.result?.proposal
          if (proposal) {
            const outcomeKey = conversationOutcomeKey(turn.decision)
            return (
              <li key={turn.id} className="cardpage-conversation-turn">
                {outcomeKey
                  ? t('detail.conversation_proposal', { title: proposal.title, outcome: t(outcomeKey) })
                  : t('detail.conversation_proposal_undecided', { title: proposal.title })}
              </li>
            )
          }
          return (
            <li key={turn.id} className="cardpage-conversation-turn">
              <b>{t('detail.conversation_ai')}</b>: {turn.result?.reply ?? ''}
            </li>
          )
        })}
      </ul>
    </div>
  )
}
