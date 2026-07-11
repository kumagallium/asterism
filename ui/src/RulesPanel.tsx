/**
 * 取り込みルール（生成物）の透明化パネル — カタログ「設計」タブの中身。
 *
 * The citable-facts promise disclosed the IRIs and the SPARQL behind every
 * answer, but the TRANSFORMATION that minted those facts stayed a black box.
 * This panel closes that gap in three layers, all read-only:
 *
 *  1. わかりやすい表示 — a deterministic, LLM-free projection of the persisted
 *     RML ("this column → this property, via this function"), rendered from
 *     GET /api/datasets/{id}/rules.
 *  2. 生成物ファイル — every persisted artifact (RML / Mapping IR / MIE /
 *     model / diagram / legacy ingester / proposal) opens in a raw viewer with
 *     copy + download. The contents were always served by the detail endpoint;
 *     the UI just never showed them.
 *  3. 変更履歴 — redesign snapshots with server-side unified diffs, so "what
 *     did this redesign change?" has an answer.
 */
import { useEffect, useMemo, useState } from 'react'
import { useTranslation } from 'react-i18next'
import {
  type CatalogDataset,
  type DatasetHistoryEntry,
  type DatasetHistorySnapshot,
  type DatasetRules,
  type RuleProperty,
  type RuleTerm,
  getDatasetArtifactContents,
  getDatasetHistory,
  getDatasetHistorySnapshot,
  getDatasetRules,
} from './galleryApi'

// ---------------------------------------------------------------------------
// small helpers
// ---------------------------------------------------------------------------

/** A model.yaml label for a term, matched by full IRI first, local name second.
 *  Returns undefined when it would only repeat the CURIE's own tail (noise). */
function labelFor(
  labels: Record<string, string>,
  fullIri: string | undefined,
  shown: string,
): string | undefined {
  const tail = shown.split(':').pop()
  let label: string | undefined
  if (fullIri && labels[fullIri]) label = labels[fullIri]
  else {
    const local = (fullIri ?? shown).split(/[#/:]/).pop()
    if (local) {
      for (const [iri, l] of Object.entries(labels)) {
        if (iri.endsWith(`#${local}`) || iri.endsWith(`/${local}`)) {
          label = l
          break
        }
      }
    }
  }
  return label && label !== tail ? label : undefined
}

/** Render an IRI/literal template with its {placeholder} column refs highlighted. */
function TemplateText({ template }: { template: string }) {
  const parts = template.split(/(\{[^{}]+\})/g)
  return (
    <code className="rules-code-inline">
      {parts.map((p, i) =>
        p.startsWith('{') && p.endsWith('}') ? (
          <span key={i} className="rules-ph">
            {p}
          </span>
        ) : (
          <span key={i}>{p}</span>
        ),
      )}
    </code>
  )
}

/** One term-map value ("値の作り方") — shared by subjects, objects and function args. */
function TermValue({ term, compact }: { term: RuleTerm; compact?: boolean }) {
  const { t } = useTranslation()
  switch (term.kind) {
    case 'reference':
      return (
        <span className="rules-val">
          {!compact && <span className="rules-kind-chip">{t('gallery:rules.kind.reference')}</span>}
          <code className="rules-code-inline">{term.reference}</code>
        </span>
      )
    case 'template':
      return (
        <span className="rules-val">
          {!compact && <span className="rules-kind-chip">{t('gallery:rules.kind.template')}</span>}
          <TemplateText template={term.template ?? ''} />
        </span>
      )
    case 'constant':
      return (
        <span className="rules-val">
          {!compact && <span className="rules-kind-chip">{t('gallery:rules.kind.constant')}</span>}
          <code className="rules-code-inline">{term.constant}</code>
        </span>
      )
    case 'function':
      return (
        <span className="rules-val">
          <span className="rules-kind-chip rules-kind-chip--fn" title={term.function_iri}>
            {t('gallery:rules.kind.function')}
          </span>
          <code className="rules-code-inline">{term.function}</code>
          <span className="rules-fn-args">
            (
            {(term.args ?? []).map((a, i) => (
              <span key={a.param} className="rules-fn-arg">
                {i > 0 && ', '}
                {a.param}=<TermValue term={a} compact />
              </span>
            ))}
            )
          </span>
        </span>
      )
    case 'join':
      return (
        <span className="rules-val">
          <span className="rules-kind-chip">{t('gallery:rules.kind.join')}</span>
          <span>
            {t('gallery:rules.joinTo', { parent: term.parent_map ?? '?' })}
            {(term.conditions ?? []).map((c) => (
              <code key={`${c.child}=${c.parent}`} className="rules-code-inline rules-join-cond">
                {c.child} = {c.parent}
              </code>
            ))}
          </span>
        </span>
      )
    default:
      return <span className="rules-val rules-val--unknown">{t('gallery:rules.kind.unknown')}</span>
  }
}

/** The 型 column: datatype / IRI / language, whichever the rule declares. */
function typeText(p: RuleProperty): string {
  const parts: string[] = []
  if (p.datatype) parts.push(p.datatype)
  if (p.term_type === 'IRI' || p.kind === 'join') parts.push('IRI')
  if (p.language) parts.push(`@${p.language}`)
  return parts.join(' · ')
}

function downloadText(name: string, content: string) {
  const url = URL.createObjectURL(new Blob([content], { type: 'text/plain;charset=utf-8' }))
  const a = document.createElement('a')
  a.href = url
  a.download = name
  a.click()
  URL.revokeObjectURL(url)
}

async function copyText(content: string): Promise<boolean> {
  try {
    await navigator.clipboard.writeText(content)
    return true
  } catch {
    // http:// origins have no async clipboard — fall back to a temp textarea.
    try {
      const ta = document.createElement('textarea')
      ta.value = content
      ta.style.position = 'fixed'
      ta.style.opacity = '0'
      document.body.appendChild(ta)
      ta.select()
      const ok = document.execCommand('copy')
      ta.remove()
      return ok
    } catch {
      return false
    }
  }
}

// ---------------------------------------------------------------------------
// modal shell
// ---------------------------------------------------------------------------

function RulesModal({
  title,
  subtitle,
  onClose,
  children,
}: {
  title: string
  subtitle?: string
  onClose: () => void
  children: React.ReactNode
}) {
  const { t } = useTranslation()
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose()
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onClose])
  return (
    <div className="rules-overlay" onClick={onClose}>
      <div
        className="rules-modal"
        role="dialog"
        aria-modal="true"
        aria-label={title}
        onClick={(e) => e.stopPropagation()}
      >
        <header className="rules-modal-head">
          <div>
            <h3 className="rules-modal-title">{title}</h3>
            {subtitle && <p className="rules-modal-sub">{subtitle}</p>}
          </div>
          <button
            type="button"
            className="rules-modal-close"
            aria-label={t('gallery:rules.viewer.close')}
            onClick={onClose}
          >
            ×
          </button>
        </header>
        <div className="rules-modal-body">{children}</div>
      </div>
    </div>
  )
}

/** Raw artifact viewer: monospace content + copy / download. */
function ArtifactViewer({
  name,
  subtitle,
  content,
  onClose,
}: {
  name: string
  subtitle?: string
  content: string
  onClose: () => void
}) {
  const { t } = useTranslation()
  const [copied, setCopied] = useState(false)
  return (
    <RulesModal title={name} subtitle={subtitle} onClose={onClose}>
      <div className="rules-viewer-actions">
        <button
          type="button"
          className="btn btn--ghost btn--sm"
          onClick={async () => {
            if (await copyText(content)) {
              setCopied(true)
              window.setTimeout(() => setCopied(false), 1600)
            }
          }}
        >
          {copied ? t('gallery:rules.viewer.copied') : t('gallery:rules.viewer.copy')}
        </button>
        <button
          type="button"
          className="btn btn--ghost btn--sm"
          onClick={() => downloadText(name, content)}
        >
          {t('gallery:rules.viewer.download')}
        </button>
      </div>
      {content.trim() ? (
        <pre className="rules-code-block">{content}</pre>
      ) : (
        <p className="ds-empty-note">{t('gallery:rules.viewer.empty')}</p>
      )}
    </RulesModal>
  )
}

/** Colorized unified diff (server-computed; snapshot = −, current = ＋). */
function DiffBlock({ diff }: { diff: string }) {
  return (
    <pre className="rules-code-block rules-diff">
      {diff.split('\n').map((line, i) => {
        const cls = line.startsWith('+++') || line.startsWith('---')
          ? 'rules-diff-file'
          : line.startsWith('@@')
            ? 'rules-diff-hunk'
            : line.startsWith('+')
              ? 'rules-diff-add'
              : line.startsWith('-')
                ? 'rules-diff-del'
                : ''
        // One block element per line: a colored line's background spans the
        // row without inline-block/newline interplay breaking the layout.
        return (
          <div key={i} className={`rules-diff-line ${cls}`}>
            {line || ' '}
          </div>
        )
      })}
    </pre>
  )
}

// ---------------------------------------------------------------------------
// the section
// ---------------------------------------------------------------------------

export function RulesSection({ dataset }: { dataset: CatalogDataset }) {
  const { t, i18n } = useTranslation()
  const meta = dataset.live?.meta
  const datasetId = meta?.id

  const [rules, setRules] = useState<DatasetRules | null>(null)
  const [history, setHistory] = useState<DatasetHistoryEntry[]>([])
  const [loadErr, setLoadErr] = useState('')
  const [contents, setContents] = useState<Record<string, string> | null>(null)
  const [viewer, setViewer] = useState<{ name: string; subtitle?: string; content: string } | null>(null)
  const [snapshot, setSnapshot] = useState<(DatasetHistorySnapshot & { openErr?: string }) | null>(null)
  const [busy, setBusy] = useState('')

  // No state resets here: DatasetDetail is keyed by the selected dataset id,
  // so this section remounts (fresh state) whenever the dataset changes.
  useEffect(() => {
    if (!datasetId) return
    let alive = true
    // Both fetches are read-only enrichment: a failure degrades to the plain
    // artifact list (never blocks the tab).
    getDatasetRules(datasetId)
      .then((r) => alive && setRules(r))
      .catch((e) => alive && setLoadErr(e instanceof Error ? e.message : String(e)))
    getDatasetHistory(datasetId)
      .then((h) => alive && setHistory(h))
      .catch(() => undefined)
    return () => {
      alive = false
    }
  }, [datasetId])

  const labels = useMemo(() => rules?.labels ?? {}, [rules])

  async function openArtifact(name: string) {
    if (!datasetId) return
    setBusy(name)
    try {
      const map = contents ?? (await getDatasetArtifactContents(datasetId))
      setContents(map)
      setViewer({ name, content: map[name] ?? '' })
    } catch (e) {
      setLoadErr(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy('')
    }
  }

  async function openSnapshot(id: string) {
    if (!datasetId) return
    setBusy(id)
    try {
      setSnapshot(await getDatasetHistorySnapshot(datasetId, id))
    } catch (e) {
      setLoadErr(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy('')
    }
  }

  function fmtWhen(iso: string, fallback: string): string {
    if (!iso) return fallback
    try {
      return new Date(iso).toLocaleString(i18n.language === 'ja' ? 'ja-JP' : 'en-US', {
        dateStyle: 'medium',
        timeStyle: 'short',
      })
    } catch {
      return fallback
    }
  }

  const maps = rules?.maps ?? []

  return (
    <>
      <div className="ds-section-head">
        <span className="ds-section-title">{t('gallery:rules.title')}</span>
      </div>

      {/* 1. わかりやすい表示 — the deterministic projection of the real mapping. */}
      {maps.length > 0 && (
        <div className="rules-projection">
          <p className="rules-intro">{t('gallery:rules.projectionIntro')}</p>
          {maps.map((m) => (
            <div className="rules-map" key={m.id}>
              <div className="rules-map-head">
                <span className="rules-map-classes">
                  {(m.subject.classes?.length ? m.subject.classes : [m.id]).map((c) => {
                    const label = labelFor(labels, undefined, c)
                    return (
                      <span key={c} className="rules-class-chip" title={label}>
                        {c}
                        {label && <span className="rules-term-label">{label}</span>}
                      </span>
                    )
                  })}
                </span>
                {m.source && (
                  <span className="rules-src-chip">
                    {m.source}
                    {m.formulation ? ` · ${m.formulation}` : ''}
                  </span>
                )}
              </div>
              {m.iterator && (
                <div className="rules-subject">
                  <span className="rules-row-label">{t('gallery:rules.iterator')}</span>
                  <code className="rules-code-inline">{m.iterator}</code>
                </div>
              )}
              {m.subject.kind && (
                <div className="rules-subject">
                  <span className="rules-row-label">{t('gallery:rules.subject')}</span>
                  <TermValue term={m.subject} compact />
                </div>
              )}
              {m.properties.length > 0 && (
                <div className="rules-table-wrap">
                  <table className="rules-table">
                    <thead>
                      <tr>
                        <th>{t('gallery:rules.colProperty')}</th>
                        <th>{t('gallery:rules.colValue')}</th>
                        <th>{t('gallery:rules.colType')}</th>
                      </tr>
                    </thead>
                    <tbody>
                      {m.properties.map((p, i) => {
                        const label = labelFor(labels, p.predicate_iri, p.predicate)
                        return (
                          <tr key={`${p.predicate}-${i}`}>
                            <td>
                              <code className="rules-code-inline" title={p.predicate_iri}>
                                {p.predicate}
                              </code>
                              {label && <span className="rules-term-label">{label}</span>}
                            </td>
                            <td>
                              <TermValue term={p} />
                            </td>
                            <td className="rules-type-cell">{typeText(p)}</td>
                          </tr>
                        )
                      })}
                    </tbody>
                  </table>
                </div>
              )}
            </div>
          ))}
        </div>
      )}

      {(rules?.warnings?.length ?? 0) > 0 && (
        <div className="rules-warnings">
          <div className="ds-subhead">{t('gallery:rules.warningsHead')}</div>
          <ul>
            {rules!.warnings.map((w, i) => (
              <li key={i}>{w}</li>
            ))}
          </ul>
        </div>
      )}

      {loadErr && <p className="ds-card-err">{t('gallery:rules.loadError', { message: loadErr })}</p>}

      {/* 2. 生成物ファイル — every persisted artifact opens in the raw viewer. */}
      {dataset.artifacts.length > 0 ? (
        <>
          {maps.length > 0 && <div className="ds-subhead">{t('gallery:rules.artifactsHead')}</div>}
          <div className="ds-artifacts">
            {dataset.artifacts.map((a) => (
              <button
                key={a.name}
                type="button"
                className={`ds-artifact${datasetId ? ' ds-artifact--openable' : ''}`}
                disabled={!datasetId || busy === a.name}
                onClick={() => openArtifact(a.name)}
                title={datasetId ? t('gallery:rules.open') : undefined}
              >
                <span className="ds-artifact-kind">{a.kind}</span>
                <code className="ds-artifact-name">{a.name}</code>
                {a.name === 'ingester.py' && (
                  <span className="rules-legacy-chip" title={t('gallery:rules.legacyNote')}>
                    {t('gallery:rules.legacyChip')}
                  </span>
                )}
                <span className="ds-artifact-detail">{t(a.detailKey, a.detailParams)}</span>
                {datasetId && <span className="rules-open-hint">{t('gallery:rules.open')}</span>}
              </button>
            ))}
          </div>
        </>
      ) : (
        <p className="ds-empty-note">{t('gallery:rules.empty')}</p>
      )}

      {/* 3. 変更履歴 — redesign snapshots + diffs vs current. */}
      {history.length > 0 && (
        <>
          <div className="ds-subhead">{t('gallery:rules.history.head')}</div>
          <div className="rules-history">
            {history.map((h) => (
              <div className="rules-history-item" key={h.id}>
                <span className="rules-history-when">
                  {t('gallery:rules.history.item', { when: fmtWhen(h.saved_at, h.id) })}
                </span>
                <span className="rules-history-files">
                  {t('gallery:rules.history.files', { count: h.artifacts.length })}
                </span>
                <button
                  type="button"
                  className="btn btn--ghost btn--sm"
                  disabled={busy === h.id}
                  onClick={() => openSnapshot(h.id)}
                >
                  {t('gallery:rules.history.diff')}
                </button>
              </div>
            ))}
          </div>
        </>
      )}

      {snapshot && (
        <RulesModal
          title={t('gallery:rules.history.diffTitle')}
          subtitle={t('gallery:rules.history.diffIntro', {
            when: fmtWhen(snapshot.snapshot.saved_at, snapshot.snapshot.id),
          })}
          onClose={() => setSnapshot(null)}
        >
          {Object.keys(snapshot.diffs).length === 0 && (
            <p className="ds-empty-note">{t('gallery:rules.history.noChanges')}</p>
          )}
          {Object.entries(snapshot.diffs).map(([name, diff], i) => (
            <details key={name} open={i === 0} className="rules-diff-file-block">
              <summary>
                <code className="rules-code-inline">{name}</code>
                {snapshot.snapshot.artifacts[name] != null && (
                  <button
                    type="button"
                    className="btn btn--ghost btn--sm rules-fulltext-btn"
                    onClick={(e) => {
                      e.preventDefault()
                      setViewer({
                        name,
                        subtitle: t('gallery:rules.history.fullTextSub', {
                          when: fmtWhen(snapshot.snapshot.saved_at, snapshot.snapshot.id),
                        }),
                        content: snapshot.snapshot.artifacts[name],
                      })
                    }}
                  >
                    {t('gallery:rules.history.fullText')}
                  </button>
                )}
              </summary>
              <DiffBlock diff={diff} />
            </details>
          ))}
          <p className="rules-diff-note">{t('gallery:rules.history.unchangedNote')}</p>
        </RulesModal>
      )}

      {viewer && (
        <ArtifactViewer
          name={viewer.name}
          subtitle={viewer.subtitle}
          content={viewer.content}
          onClose={() => setViewer(null)}
        />
      )}
    </>
  )
}
