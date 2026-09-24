// 「データを置く」画面（試作 画面 1・契約メモ §4・§6.3。担当 c2-place）。
// ドロップ → 形の一致 → 1 件ごとの 3 状態 →「ページを並べる」で棚へ永続化。
import { useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'
import './place.css'
import {
  buildShapePreview,
  footerSummary,
  pillFor,
  readHeading,
  readSummary,
  type ShapePreview,
  type TextFragment,
} from './placeShape'
import {
  commitPlace,
  createStaging,
  inspectPlace,
  placeSubjects,
  resolveSet,
  type PlaceInspectResult,
  type PlaceSubjectRow,
  type SetSpec,
  type SubjectItem,
} from './cardsApi'

/** `App.tsx`（c2-shell）の実 `navigate`/`Route` を汎用に受ける。統合段で実
 *  型に差し替わる想定（このファイル自体は `{tab:'workbench'|'cards', …}` の
 *  形さえ渡せればよい）。 */
export type PlaceNavigateFn = (route: { tab: string; [key: string]: unknown }) => void

export interface PlaceViewProps {
  navigate: PlaceNavigateFn
  onPlaced: (subjects: SubjectItem[], set: { set_id: string; spec: SetSpec }) => void
  /** `#/cards/place?dataset=<id>` — KantanWizard から「ページに戻る」で入って
   *  きたとき（契約メモ §6.3）。あるときはファイル投入を丸ごとスキップし、
   *  既に棚にあるデータセットとして inspect/subjects を呼ぶ。 */
  datasetId?: string
}

/** ウィザードへ渡す最小の引き継ぎ（KantanWizard.tsx 側フック §6.3 の相手）。 */
interface KantanHandoff {
  stagingId: string
  sourceNames: { name: string; size: number }[]
  autoInspect: true
  returnTo: string
}

const KZ_STORAGE = 'asterism.kantan'

/** 数値だけ `Intl.NumberFormat` の桁区切りに整形してから i18next の補間へ渡す
 *  （文字列はそのまま）。呼び出し側の言語を跨いでも表記の一貫性が保てる。 */
function formatVars(vars: Record<string, string | number> | undefined, locale: string) {
  if (!vars) return undefined
  const out: Record<string, string> = {}
  for (const [k, v] of Object.entries(vars)) {
    out[k] = typeof v === 'number' ? new Intl.NumberFormat(locale).format(v) : v
  }
  return out
}

export function PlaceView({ navigate, onPlaced, datasetId }: PlaceViewProps) {
  const { t, i18n } = useTranslation()
  const tFrag = (frag: TextFragment) => t(`cards:${frag.key}`, formatVars(frag.vars, i18n.language))
  const [dragOver, setDragOver] = useState(false)
  const [staging, setStaging] = useState<{ stagingId: string; sourceNames: string[] } | null>(null)
  const [inspecting, setInspecting] = useState(false)
  const [inspectResult, setInspectResult] = useState<PlaceInspectResult | null>(null)
  const [inspectErr, setInspectErr] = useState('')
  const [subjects, setSubjects] = useState<PlaceSubjectRow[] | null>(null)
  const [subjectsErr, setSubjectsErr] = useState('')
  // 形が合わなかったときのプレビュー（列名と先頭 3 行・契約 §4）。ドロップされた
  // ファイルそのものから決定論で切り出す（見本や生成データは混ぜない）— File
  // オブジェクトが手元にある「ファイルを置く」経路だけで作れる。既に棚にある
  // データセットの経路（`datasetId`）は File が無いので null のまま
  // （列名だけは `inspectResult.files[0].columns` から出す）。
  const [shapePreview, setShapePreview] = useState<ShapePreview | null>(null)
  const [choices, setChoices] = useState<Record<string, string | null>>({})
  const [committing, setCommitting] = useState(false)
  const [commitErr, setCommitErr] = useState('')

  const file = inspectResult?.files[0] ?? null
  const columnCount = file?.columns.length ?? 0

  // 既に棚にあるデータセットの経路（契約メモ §6.3・App.tsx の placeDatasetId）。
  // ファイル投入を丸ごとスキップし、mount（datasetId が決まった時点）で
  // inspect → subjects を呼ぶ（handleFiles と同じ形の状態を埋めるだけ）。
  useEffect(() => {
    if (!datasetId) return
    let cancelled = false
    void (async () => {
      setInspectErr('')
      setSubjectsErr('')
      setCommitErr('')
      setSubjects(null)
      setChoices({})
      setInspectResult(null)
      setShapePreview(null)
      setInspecting(true)
      try {
        const result = await inspectPlace({ dataset_id: datasetId })
        if (cancelled) return
        setInspectResult(result)
        if (result.match.type_id) {
          try {
            const sub = await placeSubjects({ dataset_id: datasetId, type_id: result.match.type_id })
            if (!cancelled) setSubjects(sub.items)
          } catch (e) {
            if (!cancelled) setSubjectsErr(e instanceof Error ? e.message : String(e))
          }
        }
      } catch (e) {
        if (!cancelled) setInspectErr(e instanceof Error ? e.message : String(e))
      } finally {
        if (!cancelled) setInspecting(false)
      }
    })()
    return () => {
      cancelled = true
    }
  }, [datasetId])

  async function handleFiles(list: FileList | File[] | null) {
    const files = Array.from(list ?? [])
    if (files.length === 0) return
    setInspectErr('')
    setSubjectsErr('')
    setCommitErr('')
    setSubjects(null)
    setChoices({})
    setInspectResult(null)
    setShapePreview(null)
    setInspecting(true)
    try {
      const st = await createStaging(files)
      setStaging(st)
      const result = await inspectPlace({ staging_id: st.stagingId })
      setInspectResult(result)
      if (result.match.type_id) {
        setInspecting(false)
        try {
          const sub = await placeSubjects({ staging_id: st.stagingId, type_id: result.match.type_id })
          setSubjects(sub.items)
        } catch (e) {
          setSubjectsErr(e instanceof Error ? e.message : String(e))
        }
        return
      }
      // 形が合わなかった（type_id: null）— 列名と先頭 3 行のプレビューを見せる
      // （契約 §4）。テキスト系（csv/tsv/txt）だけ実際の中身を読む。それ以外
      // （.xlsx 等）はサーバがすでに解いた列名（file.columns）だけ見せる —
      // ブラウザで生バイト列を読んでも文字化けするだけで、実物のプレビューに
      // ならないため。ベストエフォート: 読めなくても致命的にしない。
      const first = files[0]
      if (first && /\.(csv|tsv|txt)$/i.test(first.name)) {
        try {
          const text = await first.text()
          setShapePreview(buildShapePreview(text))
        } catch {
          /* best-effort: プレビューが作れなくても列名だけの表示にフォールバック */
        }
      }
    } catch (e) {
      setInspectErr(e instanceof Error ? e.message : String(e))
    } finally {
      setInspecting(false)
    }
  }

  /** 「棚を作る（かんたんウィザードへ）」— type_id が無いときの唯一の道
   *  （§4.1・§6.3）。KantanWizard.tsx の mount effect フックが受け取る。 */
  function goBuildShelf() {
    if (!staging) return
    const handoff: KantanHandoff = {
      stagingId: staging.stagingId,
      sourceNames: staging.sourceNames.map((name) => ({ name, size: 0 })),
      autoInspect: true,
      // KantanWizard.tsx 内部の「ページに戻る」リンク用（S9・snap.returnTo）。
      // App.tsx の Route.returnTo（下の navigate 呼び出し）とは別物 — こちらは
      // 旧 URL 互換の `#/cards/place` 経由でも CardsView が `#/datasets/add` へ
      // 置き換える（契約メモ contract_pr_f8.md §1.2）ので、そのまま残す。
      returnTo: '#/cards/place?dataset=',
    }
    try {
      sessionStorage.setItem(KZ_STORAGE, JSON.stringify(handoff))
    } catch {
      /* best-effort: セッションストレージが無くても遷移だけは通す */
    }
    // ウィザードを完了・中止したらワークスペース（`#/cards`）へ戻れるように
    // Route.returnTo を立てる（契約メモ contract_pr_f8.md §1.3）。App.tsx の
    // onWorkbenchDone がこれを見て、そのデータセットのページ
    // （`#/cards/d/<id>`）に着地させる。
    navigate({ tab: 'workbench', returnTo: '#/cards' })
  }

  function chooseCandidate(value: string, iri: string | null) {
    setChoices((cur) => ({ ...cur, [value]: iri }))
  }

  async function handleCommit() {
    if (!staging || !inspectResult?.match.type_id || !subjects) return
    setCommitting(true)
    setCommitErr('')
    try {
      const name = (staging.sourceNames[0] ?? file?.name ?? 'dataset').replace(/\.[^./\\]+$/, '')
      const result = await commitPlace({
        staging_id: staging.stagingId,
        type_id: inspectResult.match.type_id,
        choices,
        name,
      })
      onPlaced(result.subjects, result.set)
      // 追加が終わったら回答へ（契約メモ contract_pr_f8.md §1.3）: そのデータ
      // セットのワークスペースのページへ。
      navigate({ tab: 'cards', datasetPageId: result.dataset_id })
    } catch (e) {
      setCommitErr(e instanceof Error ? e.message : String(e))
    } finally {
      setCommitting(false)
    }
  }

  /** dataset_id 経路の「ページを並べる」（契約メモ §6.3 該当項）。commit（staging
   *  前提）は呼ばない — 既に棚にあるデータセットとして、place/subjects が返した
   *  linked/own_only の行をそのまま SubjectItem にする。set_id は
   *  `resolveSet` で api に決めてもらう（ui では計算しない）。 */
  async function handleCommitFromDataset() {
    if (!datasetId || !inspectResult?.match.type_id || !subjects) return
    setCommitting(true)
    setCommitErr('')
    try {
      const typeId = inspectResult.match.type_id
      const { set_id, spec } = await resolveSet({
        class: typeId,
        where: [],
        order_by: null,
        limit: 20,
        source_scope: 'own',
      })
      const now = new Date().toISOString()
      const subjectItems: SubjectItem[] = subjects
        .filter((item) => (item.match === 'linked' || item.match === 'own_only') && !!item.iri)
        .map((item) => ({
          kind: 'individual',
          id: item.iri as string,
          label: item.value,
          class_label: inspectResult.signature_label,
          source: 'own',
          card_count: null,
          match: item.match,
          subject_key: `i:${item.iri}`,
          created_at: now,
        }))
      onPlaced(subjectItems, { set_id, spec })
      // 追加が終わったら回答へ（契約メモ contract_pr_f8.md §1.3）: そのデータ
      // セットのワークスペースのページへ。
      navigate({ tab: 'cards', datasetPageId: datasetId })
    } catch (e) {
      setCommitErr(e instanceof Error ? e.message : String(e))
    } finally {
      setCommitting(false)
    }
  }

  return (
    <section className="place-view">
      {/* 「作る › データセット › データを追加」— データが入る唯一の入口の見出し
       *  （契約メモ contract_pr_f8.md §1.1・§3）。 */}
      <h2 className="place-heading">{t('cards:place.title', { defaultValue: 'データを追加' })}</h2>
      {!datasetId && !inspectResult && (
        <label
          className={`kz-drop${dragOver ? ' drag' : ''}`}
          onDragOver={(e) => {
            e.preventDefault()
            setDragOver(true)
          }}
          onDragLeave={() => setDragOver(false)}
          onDrop={(e) => {
            e.preventDefault()
            setDragOver(false)
            void handleFiles(e.dataTransfer.files)
          }}
        >
          <input
            type="file"
            multiple
            onChange={(e) => {
              void handleFiles(e.target.files)
              e.target.value = ''
            }}
          />
          <span className="kz-drop-main">
            {t('cards:place.dropTitle', { defaultValue: 'ここにファイルを置く' })}
          </span>
          <span className="kz-drop-sub">
            {t('cards:place.dropSub', { defaultValue: 'クリックして選ぶこともできます' })}
          </span>
        </label>
      )}

      {inspecting && (
        <p className="kz-note">{t('cards:place.inspecting', { defaultValue: '読んでいます…' })}</p>
      )}
      {inspectErr && <p className="kz-note kz-note--warn">{inspectErr}</p>}

      {inspectResult && file && (
        <div className="place-head">
          <h2 className="place-heading">
            {tFrag(readHeading(file.name, datasetId ? inspectResult.signature_label : null))}
          </h2>
          <p className="kz-note place-summary">
            {readSummary({ rows: file.rows, columnCount }, inspectResult.match, inspectResult.signature_label)
              .map(tFrag)
              .join(' ・ ')}
          </p>
          {!datasetId && (
            <button
              type="button"
              className="btn btn--ghost btn--sm"
              onClick={() => {
                setInspectResult(null)
                setShapePreview(null)
              }}
            >
              {t('cards:place.anotherFile', { defaultValue: '別のファイル' })}
            </button>
          )}
        </div>
      )}

      {inspectResult && !inspectResult.match.type_id && (
        <div className="place-noshelf card">
          {shapePreview && shapePreview.columns.length > 0 ? (
            <>
              <p className="kz-note">
                {t('cards:place.previewLead', {
                  defaultValue: 'この中にある列と、先頭 {{count}} 行です',
                  count: shapePreview.rows.length,
                })}
              </p>
              <div className="table-wrap place-preview-table">
                <table className="jobs-table">
                  <thead>
                    <tr>
                      {shapePreview.columns.map((col, i) => (
                        <th key={`${col}-${i}`}>{col}</th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {shapePreview.rows.map((row, ri) => (
                      <tr key={ri}>
                        {shapePreview.columns.map((_, ci) => (
                          <td key={ci}>{row[ci] ?? ''}</td>
                        ))}
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </>
          ) : (
            <>
              <p className="kz-note">
                {t('cards:place.previewColumnsOnly', { defaultValue: 'この中にある列です' })}
              </p>
              <div className="place-preview-columns">
                {(file?.columns ?? []).map((col) => (
                  <span className="place-preview-col-chip" key={col}>
                    {col}
                  </span>
                ))}
              </div>
            </>
          )}
          <div className="place-preview-actions">
            <button type="button" className="btn btn--accent" onClick={goBuildShelf}>
              {t('cards:place.buildShelf', { defaultValue: '設定の手順へ（順番に質問します）' })}
            </button>
            <button type="button" className="btn btn--ghost" onClick={() => navigate({ tab: 'cards' })}>
              {t('cards:place.tryDemo', { defaultValue: '見本で試す' })}
            </button>
          </div>
        </div>
      )}

      {subjectsErr && <p className="kz-note kz-note--warn">{subjectsErr}</p>}

      {subjects && (
        <>
          <div className="place-lbl">
            {t('cards:place.foundLabel', { defaultValue: 'この中に出てきたもの' })}
          </div>
          <ul className="place-rows">
            {subjects.map((item) => {
              const pill = pillFor(item.match)
              const chosen = choices[item.value]
              return (
                <li key={item.value} className="place-row">
                  <div className="place-row-main">
                    <div className="place-row-nm">
                      <b>{item.value}</b>
                      <small>
                        {t('cards:place.rowRows', { defaultValue: '{{count}} 行', count: item.rows })}
                      </small>
                    </div>
                    <span className={`place-pill place-pill--${pill.tone}`}>
                      {pill.tone === 'ok' &&
                        t('cards:place.pillLinked', {
                          defaultValue: '公開データと同じ ・ {{count}} 件',
                          count: item.rows,
                        })}
                      {pill.tone === 'warn' &&
                        t('cards:place.pillAmbiguous', {
                          defaultValue: '候補が {{count}} つ',
                          count: item.candidates?.length ?? 0,
                        })}
                      {pill.tone === 'mute' &&
                        t('cards:place.pillOwnOnly', { defaultValue: '自分のデータだけ' })}
                    </span>
                  </div>
                  {item.match === 'ambiguous' && item.candidates && (
                    <div className="place-choice">
                      <span className="place-choice-lead">
                        {t('cards:place.choiceLead', { defaultValue: '同じものを指す候補：' })}
                      </span>
                      {item.candidates.map((c) => (
                        <button
                          type="button"
                          key={c.iri}
                          className={`link-btn${chosen === c.iri ? ' place-choice-on' : ''}`}
                          onClick={() => chooseCandidate(item.value, c.iri)}
                        >
                          {t('cards:place.candidateLabel', {
                            defaultValue: '{{label}}（{{count}} 件）',
                            label: c.label,
                            count: c.count,
                          })}
                        </button>
                      ))}
                      <button
                        type="button"
                        className={`place-choice-none${chosen === null ? ' place-choice-on' : ''}`}
                        onClick={() => chooseCandidate(item.value, null)}
                      >
                        {t('cards:place.candidateNone', { defaultValue: 'どちらでもない' })}
                      </button>
                    </div>
                  )}
                </li>
              )
            })}
          </ul>

          <div className="place-foot">
            <div className="place-foot-sum">{tFrag(footerSummary(subjects))}</div>
            {commitErr && <p className="kz-note kz-note--warn">{commitErr}</p>}
            <button
              type="button"
              className="btn"
              disabled={committing}
              onClick={() => void (datasetId ? handleCommitFromDataset() : handleCommit())}
            >
              {t('cards:place.arrangePages', { defaultValue: 'ページを並べる' })}
            </button>
          </div>
        </>
      )}
    </section>
  )
}
