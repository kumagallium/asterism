import { useTranslation } from 'react-i18next'
import type { CatalogDataset } from './galleryApi'

/**
 * 外部の標準に合わせる — 本体は「ことば」画面の「線」の節（標準の語へ）へ移った
 * （ADR upper-structure-shared-terms.md §2.5.1 (5)・§3.1 (b)。中身は DatasetGroundingPanel）。
 * ここに残るのは導線の 1 行だけ。props と呼び手（GalleryView）は変えない。
 * `#/vocab?scope=standard&dataset=<id>` でその節の標準の語へ・このデータセットを開く。
 */
export function DatasetGrounding({ dataset }: { dataset: CatalogDataset }) {
  const { t } = useTranslation()
  return (
    <div className="ds-grounding">
      <p className="ds-empty-note">
        <button
          type="button"
          className="link-btn"
          onClick={() => {
            window.location.hash = `#/vocab?scope=standard&dataset=${encodeURIComponent(dataset.id)}`
          }}
        >
          {t('grounding:toVocab')}
        </button>
      </p>
    </div>
  )
}
