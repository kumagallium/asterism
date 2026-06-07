import { useState } from 'react'
import {
  type IngestProgress,
  type IngestResult,
  type MaterializeResult,
  type TrapResult,
  ingestDataset,
} from './api'
import { IngestProgressView } from './IngestProgressView'

const STATUS_GLYPH: Record<TrapResult['status'], string> = {
  pass: '✓',
  fail: '✗',
  warn: '⚠',
  skip: '·',
}

// Japanese trap labels (the backend `name` is English). Keyed by trap id so the
// page reads in Japanese without relying on the browser's auto-translate.
const TRAP_JA: Record<string, string> = {
  T1: 'IRI の一意性（複合キーが全体で一意か）',
  T2: 'BOM（ingester が utf-8-sig で開くか）',
  T3: '空白ノードなし（bnode-free）',
  T4: 'MIE のキーワード／カテゴリ（5 個以上）',
  T5: 'Mermaid のコロン回避（図ラベルに : を含まない）',
  T6: 'サンプルが実在行か（捏造でないか）',
  T7: '設計根拠（理由／代替案／トレードオフ）',
  T8: 'AI 幻覚テスト（任意）',
}

// Plain-language meaning of each status, shown in the legend.
const STATUS_JA: { status: TrapResult['status']; label: string }[] = [
  { status: 'pass', label: '合格' },
  { status: 'skip', label: 'スキップ（実行せず）' },
  { status: 'warn', label: '警告' },
  { status: 'fail', label: '不合格（要修正）' },
]

function download(filename: string, contents: string) {
  const blob = new Blob([contents], { type: 'text/plain;charset=utf-8' })
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = filename
  a.click()
  URL.revokeObjectURL(url)
}

/**
 * Shows the materialized artifacts as download buttons + the trap validation
 * report + the human-gated Oxigraph ingest (Phase 5 #15). CSV-dependent traps
 * (T1/T6) report `skip` here because the materialize endpoint validates the
 * artifact bundle without source CSVs.
 */
export function MaterializePanel({
  result,
  csvFiles = [],
}: {
  result: MaterializeResult
  /** The CSVs used to design the schema — needed to run the substrate ingest. */
  csvFiles?: File[]
}) {
  const artifacts = Object.entries(result.artifacts).filter(([, v]) => v) as [string, string][]
  return (
    <section className="materialize-panel">
      <h3 className="section-h">生成物（{artifacts.length}）</h3>
      <div className="artifact-list">
        {Object.entries(result.artifacts).map(([name, contents]) => (
          <button
            key={name}
            type="button"
            className="artifact-btn"
            disabled={!contents}
            onClick={() => contents && download(name, contents)}
            title={contents ? `${name} をダウンロード` : `${name} は抽出されませんでした`}
          >
            ⤓ {name}
          </button>
        ))}
      </div>
      {result.warnings.length > 0 && (
        <ul className="materialize-warnings">
          {result.warnings.map((w, i) => (
            <li key={i}>{w}</li>
          ))}
        </ul>
      )}

      <h3 className="section-h">検証（8 つの罠）</h3>
      <div className="trap-legend">
        {STATUS_JA.map((s) => (
          <span key={s.status} className="trap-legend-item">
            <span className={`trap-glyph trap-${s.status}`}>{STATUS_GLYPH[s.status]}</span>
            {s.label}
          </span>
        ))}
      </div>
      <div className="trap-grid">
        {result.traps.map((t) => (
          <div
            key={t.id}
            className={`trap trap-${t.status}`}
            title={`${TRAP_JA[t.id] ?? t.name}${t.detail ? ` — ${t.detail}` : ''}`}
          >
            <span className="trap-glyph">{STATUS_GLYPH[t.status]}</span>
            <span className="trap-id">{t.id}</span>
            <span className="trap-name">{TRAP_JA[t.id] ?? t.name}</span>
          </div>
        ))}
      </div>
      <p className="trap-summary">
        {result.exit_code === 0 ? (
          <span className="trap-ok">ブロッキング失敗なし（exit 0＝保存 OK）</span>
        ) : (
          <span className="trap-bad">ブロッキング失敗あり（exit {result.exit_code}＝要修正）</span>
        )}
      </p>

      <IngestGate result={result} csvFiles={csvFiles} />
    </section>
  )
}

/**
 * The human gate (#15): approve the declarative RML and run it through the
 * Morph-KGC substrate into an *isolated draft graph*. Ask cites the canonical
 * graph by default, so draft data is not a citable fact until promoted.
 */
function IngestGate({ result, csvFiles }: { result: MaterializeResult; csvFiles: File[] }) {
  const rml = (result.artifacts['mapping.rml.ttl'] ?? '').trim()
  const datasetId = result.dataset?.id
  const [busy, setBusy] = useState(false)
  const [progress, setProgress] = useState<IngestProgress | null>(null)
  const [done, setDone] = useState<IngestResult | null>(null)
  const [err, setErr] = useState('')

  if (!rml) {
    return (
      <div className="ingest-gate">
        <h3 className="section-h">Oxigraph へ投入（人間ゲート）</h3>
        <p className="ingest-hint">
          この設計には宣言 RML マッピングが無いため投入できません。propose が §RML
          （宣言マッピング）を出すと、ここから安全に投入できるようになります。
        </p>
      </div>
    )
  }

  const canIngest = !!datasetId && csvFiles.length > 0 && !busy

  async function onIngest() {
    if (!datasetId) return
    setBusy(true)
    setErr('')
    setProgress(null)
    try {
      setDone(await ingestDataset(datasetId, csvFiles, setProgress))
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="ingest-gate">
      <h3 className="section-h">Oxigraph へ投入（人間ゲート）</h3>
      <p className="ingest-note">
        承認すると、この宣言 RML を Morph-KGC が実行し（生成コードは走らず、検証済みの
        Tier 0 関数だけ）、結果を<strong>隔離された draft グラフ</strong>に投入します。
        Ask の既定の引用面（canonical）は汚しません。
      </p>
      <details className="rml-preview">
        <summary>RML マッピングを確認（{rml.split('\n').length} 行）</summary>
        <pre className="rml-pre">{rml}</pre>
      </details>

      {done ? (
        <p className="ingest-ok">
          ✓ draft グラフに投入しました（{done.triple_count} triples）。
          <br />
          <code className="ingest-graph">{done.graph_iri}</code>
        </p>
      ) : (
        <>
          <button type="button" onClick={onIngest} disabled={!canIngest}>
            {busy ? '投入中…' : 'Oxigraph へ投入（承認）'}
          </button>
          {busy && <IngestProgressView progress={progress} />}
          {!datasetId && (
            <p className="ingest-hint">
              この設計はまだ保存されていません（dataset が見つかりません）。
            </p>
          )}
          {datasetId && csvFiles.length === 0 && (
            <p className="ingest-hint">
              投入には設計に使った CSV が必要です。データソース欄で CSV を選び直してください
              （リロード後は再選択が必要）。
            </p>
          )}
          {err && <p className="ingest-err">投入に失敗しました: {err}</p>}
        </>
      )}
    </div>
  )
}
