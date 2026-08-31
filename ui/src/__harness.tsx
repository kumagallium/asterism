// 確認用の使い捨てページ（コミットしない）。実物の SkeletonGate と ConsultDrawer を、
// 実 CSS・実 i18n・実データ（XRD 参考カードを実際の方言で通した annotate_skeleton
// の出力）で描くためだけのもの。LLM もデータベースも使わない。
//
//   npm --prefix ui run dev -- --port 5199
//   http://localhost:5199/__harness.html
//
// 触れること:
//   ①の帯   … 「分け方を AI に相談」「つながりを AI に相談」「＋ 同じ ID で種類を足す」
//   ⚠ の隣  … 「重複を AI に相談」（重複があるときだけ出る）
//   下のボタン… 相談の返事（提案ブロック入り）を差し込む。ドロワーを開くと候補が出る
//   最下部   … いまの骨格の JSON（操作の結果がそのまま見える）
import { StrictMode, useEffect, useState } from 'react'
import { createRoot } from 'react-dom/client'
import './index.css'
import './App.css'
import './i18n'
import type { MappingSkeleton, SkeletonAnnotations } from './api'
import { SkeletonGate } from './SkeletonGate'
import { ConsultDrawer } from './consult/ConsultDrawer'
import { SettingsProvider } from './settings/SettingsContext'
import { setConsultContext } from './consult/consultContext'
import { resolveConsultAnswer, startConsultThread } from './consult/consultThreads'
import { registerSuggestionApplier } from './consult/consultApply'
import { applyIdentifiers, applyOwners, applySplits } from './skeletonKinds'
import { detectDatasetNamespace } from './datasetNamespace'
import fixture from './__harness_fixture.json'

/** 相談チャットが返してくる形の返事（D3 の 3 型入り）。`Space Group` は理由が
 *  無いので、パースの時点で捨てられる — 件数が 4 でなく 3 になるのが正しい。 */
const REPLY = `カードと結晶は別のものです。

\`\`\`asterism-suggestions
{"splits": [{"from": "pattern", "name": "Crystal", "columns": ["Cell", "Volume", "Z value"]}],
 "owners": [{"column": "Radiation", "map": "sample"}],
 "identifiers": [{"column": "Chemical Formula", "reason": "外のデータと同じ名前で呼ばれる"},
                 {"column": "Space Group"}]}
\`\`\`
`

function Harness() {
  const [skeleton, setSkeleton] = useState<MappingSkeleton>(
    fixture.skeleton as unknown as MappingSkeleton,
  )
  const annotations = fixture.annotations as unknown as SkeletonAnnotations

  // S4（KantanWizard）が張っているのと同じ文脈と applier を、この確認ページでも張る。
  useEffect(() => {
    const ns = detectDatasetNamespace(skeleton) ?? annotations.dataset_namespace ?? null
    setConsultContext({
      step: 'ID のつけかた',
      kinds: skeleton.maps.map((m) => ({
        map: m.name,
        source: m.source,
        keyColumns: [...(m.subject.template ?? '').matchAll(/\{([^{}]+)\}/g)].map((x) => x[1]),
        kindName: (m.subject.classes ?? [])[0],
        columns: (m.owns ?? []).length
          ? (m.owns as string[])
          : (annotations.maps?.[m.name]?.entity_preview?.all_values ?? []).map((v) => v.column),
      })),
    })
    return registerSuggestionApplier(({ splits, owners, identifiers }) => {
      let next = skeleton
      let applied = 0
      let skipped = 0
      for (const step of [
        () => applySplits(next, splits, ns),
        () => applyOwners(next, owners, (n) => annotations.maps?.[n]?.collapse_kind),
        () => applyIdentifiers(next, identifiers, skeleton.maps[0].name),
      ]) {
        const out = step()
        next = out.skeleton
        applied += out.applied
        skipped += out.skipped
      }
      if (applied > 0) setSkeleton(next)
      return { applied, skipped }
    })
  }, [skeleton, annotations])

  return (
    <div className="app-shell" style={{ height: '100vh' }}>
      <div className="app-main" style={{ padding: '1.5rem' }}>
        <SkeletonGate
          skeleton={skeleton}
          annotations={annotations}
          annotationsBusy={false}
          canRevalidate
          busy={false}
          plain
          onChange={setSkeleton}
          onContinue={() => {}}
          onDiscard={() => {}}
          onRethink={() => {}}
          titleKey="kantan:s4.gateTitle"
          hintKey="kantan:s4.gateHint"
          continueKey="kantan:s4.continue"
          discardKey="kantan:s4.discard"
          discardConfirmKey="kantan:s4.discardConfirm"
        />
        <div className="kz-actions" style={{ marginTop: '2rem' }}>
          <button
            type="button"
            className="btn btn--sm"
            id="harness-seed-reply"
            onClick={() => {
              const started = startConsultThread('この表の項目を、どんな種類に分けるとよいか教えてください')
              resolveConsultAnswer(started.thread.id, started.assistantTurnId, REPLY)
              window.location.reload()
            }}
          >
            相談の返事を差し込む（確認用・再読み込みします）
          </button>
          <button
            type="button"
            className="btn btn--ghost btn--sm"
            onClick={() => setSkeleton(fixture.skeleton as unknown as MappingSkeleton)}
          >
            骨格を最初に戻す
          </button>
        </div>
        <pre
          id="harness-skeleton"
          style={{ marginTop: '1rem', fontSize: 11, whiteSpace: 'pre-wrap' }}
        >
          {JSON.stringify(skeleton.maps, null, 1)}
        </pre>
      </div>
      <ConsultDrawer />
    </div>
  )
}

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <SettingsProvider>
      <Harness />
    </SettingsProvider>
  </StrictMode>,
)
