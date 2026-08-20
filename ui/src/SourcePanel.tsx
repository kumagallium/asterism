// The right-hand panel for the source of an AGGREGATE answer.
//
// An aggregate ("the 2θ range is 20.0°–80.0°") names no single record, so the
// provenance trace — which resolves ONE citation IRI — has nothing to resolve.
// Its source is the published dataset the number was read from and the vetted
// way it was read, and both belong in the same slot as the trace: checking where
// a number came from must not cost the reader their conversation (live
// 2026-08-20: 「画面遷移してしまうともとに戻るのが大変」). Opening the dataset
// itself stays a deliberate second click.
import { useTranslation } from 'react-i18next'

import type { CatalogDataset } from './galleryApi'
import { CloseIcon, LayersIcon } from './icons'

export function SourcePanel({
  datasetId,
  dataset,
  toolTitles,
  onClose,
  onOpenDataset,
}: {
  /** Registry id of the dataset the answer read (what the tool reported). */
  datasetId: string
  /** Its catalog row, when it is still there — absent means deleted or
   *  re-published under another id. */
  dataset?: CatalogDataset
  /** The vetted ways this answer read it, in the words of the tools. */
  toolTitles: string[]
  onClose: () => void
  /** Opens the dataset itself. Given only together with `dataset`. */
  onOpenDataset?: () => void
}) {
  const { t } = useTranslation()
  return (
    <aside className="ask-trace" aria-label={t('ask:sourcePanel.ariaLabel')}>
      <div className="trace-header">
        <button
          type="button"
          className="trace-close"
          onClick={onClose}
          aria-label={t('shared:trace.close')}
          title={t('shared:trace.close')}
        >
          <CloseIcon size={16} />
        </button>
        <div className="trace-eyebrow">{t('ask:sources.heading')}</div>
        <h3 className="trace-title">{dataset?.name ?? t('ask:sources.unknown')}</h3>
        <p className="trace-sub">{t('ask:sourcePanel.sub')}</p>
      </div>

      {dataset ? (
        <div className="source-panel-body">
          {/* What the dataset holds, in the catalog's own words — the same
              chips its card shows, so the reader recognises it without
              opening it. */}
          {dataset.counts.length > 0 && (
            <ul className="source-panel-counts">
              {dataset.counts.map((c, i) => (
                <li key={i}>
                  <span className="source-panel-count">{c.value}</span> {c.label}
                </li>
              ))}
            </ul>
          )}
          <h4 className="section-h">{t('ask:sourcePanel.howHeading')}</h4>
          <ul className="source-panel-tools">
            {toolTitles.map((title) => (
              <li key={title}>{title}</li>
            ))}
          </ul>
          <p className="trace-sub">{t('ask:sourcePanel.howNote')}</p>
          {onOpenDataset && (
            <button type="button" className="btn btn--ghost btn--sm" onClick={onOpenDataset}>
              <LayersIcon size={14} /> {t('ask:sourcePanel.open')}
            </button>
          )}
        </div>
      ) : (
        /* The id is machine notation and answers nothing a reader can check
           (K4) — it is kept for whoever needs to file a report, in the fold
           voice, not as the panel's subject. */
        <div className="source-panel-body">
          <p className="trace-sub">{t('ask:sourcePanel.gone')}</p>
          <details className="sparql-disclosure">
            <summary>{t('ask:techSummary')}</summary>
            <p className="sparql-disclosure-hint">{datasetId}</p>
          </details>
        </div>
      )}
    </aside>
  )
}
