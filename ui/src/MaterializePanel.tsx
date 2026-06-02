import type { MaterializeResult, TrapResult } from './api'

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
 * Shows the 4 materialized artifacts as download buttons + the 8-trap
 * validation report. CSV-dependent traps (T1/T6) report `skip` here because
 * the materialize endpoint validates the artifact bundle without source CSVs.
 */
export function MaterializePanel({ result }: { result: MaterializeResult }) {
  const artifacts = Object.entries(result.artifacts).filter(([, v]) => v) as [string, string][]
  return (
    <section className="materialize-panel">
      <h3 className="section-h">生成物（{artifacts.length}/4）</h3>
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
    </section>
  )
}
