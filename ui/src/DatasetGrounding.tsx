import { useState } from 'react'
import { DatasetGroundingPanel } from './DatasetGroundingPanel'
import type { CatalogDataset } from './galleryApi'

/**
 * 外部の標準に合わせる — データセットの詳細 › 中身（元の場所）。「ことば」画面の「線」の節へ
 * 移していたものを、節ごと「決めたこと」へ移したのに合わせて、ここへ戻した（UI 再設計 PR 3）。
 * 決めた結果は「決めたこと」で一覧・取り消しができる。props と呼び手（GalleryView）は変えない。
 */
export function DatasetGrounding({ dataset }: { dataset: CatalogDataset }) {
  // 合わせたら一覧を読み直す（DatasetGroundingPanel が親の版を見て読み直す）。
  const [version, setVersion] = useState(0)
  return (
    <div className="ds-grounding">
      <DatasetGroundingPanel
        dataset={dataset}
        reloadKey={version}
        onChanged={() => setVersion((v) => v + 1)}
      />
    </div>
  )
}
