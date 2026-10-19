import { useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { getSchema, type SchemaSummary } from './demoApi'
import { type CatalogDataset, getCatalogDatasets } from './galleryApi'
import { VocabMapSection } from './VocabMapSection'

function goTo(hash: string): void {
  if (window.location.hash !== hash) window.location.hash = hash
}

/**
 * 「ほかのデータセットとの関係の図」。データセットの詳細（中身）タブの末尾に、1 行の見出しだけで畳んで置く
 * （UI 再設計 PR 5: 「知りたいこと」の下に常時あった「用語の地図」の引っ越し先）。開いたときに読む。
 */
export function RelationMapFold() {
  const { t } = useTranslation()
  const [open, setOpen] = useState(false)
  const [datasets, setDatasets] = useState<CatalogDataset[]>([])
  const [schema, setSchema] = useState<SchemaSummary | null>(null)
  const [ready, setReady] = useState(false)

  useEffect(() => {
    if (!open) return
    let off = false
    Promise.allSettled([getCatalogDatasets(), getSchema()]).then(([ds, sc]) => {
      if (off) return
      if (ds.status === 'fulfilled') setDatasets(ds.value)
      if (sc.status === 'fulfilled') setSchema(sc.value)
      setReady(true)
    })
    return () => {
      off = true
    }
  }, [open])

  return (
    <details className="ds-advisory-raw" onToggle={(e) => setOpen((e.currentTarget as HTMLDetailsElement).open)}>
      <summary>{t('gallery:relationMap')}</summary>
      {open && !ready && (
        <p className="loading-row">
          <span className="spinner" />
          {t('gallery:loading')}
        </p>
      )}
      {open && ready && (
        <VocabMapSection
          datasets={datasets}
          schema={schema}
          onOpenDataset={(id) => goTo(`#/datasets/${encodeURIComponent(id)}`)}
          onOpenKind={(iri) => goTo(`#/cards/k/${encodeURIComponent(iri)}`)}
          onOpenCrosswalk={() => goTo('#/crosswalk')}
        />
      )}
    </details>
  )
}
