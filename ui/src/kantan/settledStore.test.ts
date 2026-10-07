// ③「意味をつける」で決めた意味と「取り込まない」が、データセットの保管庫に
// 届く経路。
//
// 初回の流れでは、これを保管庫に写していたのが畳んだ「数の確認」の画面だった。
// 畳んだあとは誰も書かず、外した列は置いたブラウザの state にしか無かった
// （2026-10-05 に実機で確認: 別のブラウザで見直すと「取り込む」に戻る）。
// いまは、データセットを**作る**保存が運び、サーバが同じ一歩で書く。できた
// あとは③の「この意味を保存して戻る」だけが書く —「ためすに戻る」は保存しない。
// 保存しないとは「捨てる」: 書きかけを state に残して出ると、⑤からのやり直しが
// それを送って、保存していない「取り込まない」が設計に効いた（実機 2026-10-05）。
import { describe, expect, it } from 'vitest'
import { materializeRequestBody } from '../api'
import {
  columnKey,
  meaningsScreenOpened,
  storedExclusionKeys,
  unsavedDraftDiscarded,
  withStoredDecisions,
  withStoredMeanings,
  withdrawnExclusions,
} from './settledStore'
import source from './KantanWizard.tsx?raw'

const settled = {
  columnMeanings: [{ source: 'stock.csv', column: 'amount', label: '使う量' }],
  columnDecisions: [{ source: 'stock.csv', column: 'unit', action: 'exclude' as const }],
}

describe('データセットを作る保存が、設計の前に決めたことを運ぶ', () => {
  it('新しいデータセット（id なし）のときは、意味と「取り込まない」を添える', () => {
    const body = materializeRequestBody('# md', 'stock', undefined, 'st-1', [], settled)
    expect(body.column_meanings).toEqual(settled.columnMeanings)
    expect(body.column_decisions).toEqual(settled.columnDecisions)
  })

  it('すでにあるデータセットへの保存には添えない（③で書きかけたものを書かない）', () => {
    const body = materializeRequestBody('# md', 'stock', 'stock-1a2b3c4d', null, [], settled)
    expect(body.dataset_id).toBe('stock-1a2b3c4d')
    expect(body).not.toHaveProperty('column_meanings')
    expect(body).not.toHaveProperty('column_decisions')
  })

  it('何も決めていなければ、欄そのものを送らない', () => {
    const body = materializeRequestBody('# md', 'stock', undefined, null, undefined, {
      columnMeanings: [],
      columnDecisions: [],
    })
    expect(body).toEqual({ proposal_md: '# md', dataset_name: 'stock' })
  })
})

describe('保管庫の「取り込まない」と、③の表', () => {
  it('読み戻すのは「取り込まない」だけ（取り込む・持ち主の判断は別の画面のもの）', () => {
    expect(
      storedExclusionKeys([
        { source: 'stock.csv', column: 'unit', action: 'exclude' },
        { source: 'stock.csv', column: 'memo', action: 'include', map: 'record', label: 'メモ' },
        { source: 'stock.csv', column: 'no', action: 'own', map: 'record' },
      ]),
    ).toEqual([columnKey('stock.csv', 'unit')])
  })

  it('「取り込む」に戻した列だけが取り下げになる', () => {
    const stored = [columnKey('stock.csv', 'unit'), columnKey('stock.csv', 'memo')]
    // unit は戻した・memo は外したまま・amount は新しく外した（取り下げではない）
    const current = [columnKey('stock.csv', 'memo'), columnKey('stock.csv', 'amount')]
    expect(withdrawnExclusions(stored, current)).toEqual([{ source: 'stock.csv', column: 'unit' }])
  })

  it('何も戻していなければ、取り下げは無い', () => {
    const stored = [columnKey('stock.csv', 'unit')]
    expect(withdrawnExclusions(stored, stored)).toEqual([])
    expect(withdrawnExclusions([], [columnKey('stock.csv', 'unit')])).toEqual([])
  })
})

describe('③を保存せずに出ると、書きかけは捨てられる', () => {
  // 開いたときの画面: AI の下書きの意味と、置いたブラウザに残っていた「取り込まない」。
  const onScreen = {
    meanings: [{ source: 'stock.csv', column: 'amount', label: '量' }],
    excluded: [columnKey('stock.csv', 'memo')],
  }
  const stored = {
    meanings: [{ source: 'stock.csv', column: 'amount', label: '使う量', unit: 'g' }],
    decisions: [{ source: 'stock.csv', column: 'unit', action: 'exclude' as const }],
  }

  it('保管庫が読めたら、戻す先は保管庫の内容（意味も「取り込まない」も）', () => {
    let opened = meaningsScreenOpened('stock-1a2b3c4d', onScreen.meanings, onScreen.excluded)
    opened = withStoredMeanings(opened, stored.meanings)
    opened = withStoredDecisions(opened, stored.decisions)
    // ここで人が書きかける: 意味を直し、amount を外す — state だけが変わる。
    expect(unsavedDraftDiscarded(opened, 'stock-1a2b3c4d')).toEqual({
      meanings: stored.meanings,
      excluded: [columnKey('stock.csv', 'unit')],
    })
    // 取り下げの計算に使う控えも、保管庫から読めたもの。
    expect(opened.storedExclusions).toEqual([columnKey('stock.csv', 'unit')])
  })

  it('保管庫が読めていない欄は、開いたときの state に戻す（取り下げは計算しない）', () => {
    const opened = meaningsScreenOpened('stock-1a2b3c4d', onScreen.meanings, onScreen.excluded)
    expect(unsavedDraftDiscarded(opened, 'stock-1a2b3c4d')).toEqual(onScreen)
    expect(opened.storedExclusions).toBeNull()
  })

  it('保管庫の意味が空なら画面の意味を残し、判断が空なら「外した列は無い」が勝つ', () => {
    let opened = meaningsScreenOpened('stock-1a2b3c4d', onScreen.meanings, onScreen.excluded)
    opened = withStoredMeanings(opened, [])
    opened = withStoredDecisions(opened, [])
    expect(unsavedDraftDiscarded(opened, 'stock-1a2b3c4d')).toEqual({
      meanings: onScreen.meanings,
      excluded: [],
    })
    expect(opened.storedExclusions).toEqual([])
  })

  it('控えが無い・別のデータセットの控えなら、state に触らない', () => {
    const opened = meaningsScreenOpened('stock-1a2b3c4d', onScreen.meanings, onScreen.excluded)
    expect(unsavedDraftDiscarded(null, 'stock-1a2b3c4d')).toBeNull()
    expect(unsavedDraftDiscarded(opened, 'other-9f8e7d6c')).toBeNull()
    expect(unsavedDraftDiscarded(opened, null)).toBeNull()
  })
})

describe('ウィザードが保管庫に書く場所', () => {
  /** `name(` の呼び出しごとに、それを囲む関数（コンポーネント直下の宣言）の名前。 */
  function callers(src: string, name: string): string[] {
    const out: string[] = []
    for (const m of src.matchAll(new RegExp(`\\b${name}\\(`, 'g'))) {
      const before = src.slice(0, m.index)
      const owners = [...before.matchAll(/^ {2}(?:async )?function (\w+)\(/gm)]
      const effects = [...before.matchAll(/^ {2}useEffect\(/gm)]
      const owner = owners[owners.length - 1]
      const effect = effects[effects.length - 1]
      // 直前にあるのが effect なら、その中からの呼び出し。
      out.push(effect && (!owner || effect.index > owner.index) ? 'useEffect' : (owner?.[1] ?? '?'))
    }
    return out
  }

  it('意味を保管庫に書くのは、③の保存ボタンだけ', () => {
    // 画面の遷移（「ためす」に着いた・step が変わった）を起点に書く effect を
    // 足すと、③で書きかけて「ためすに戻る」で出たものまで保管庫に入る。
    expect(callers(source, 'saveColumnMeanings')).toEqual(['saveMeaningsAndReturn'])
  })

  it('列の判断を保管庫に書くのは、③の保存ボタンと「持ち主」の判断だけ', () => {
    expect(callers(source, 'saveColumnDecisions').sort()).toEqual([
      'applyColumnOwners',
      'saveMeaningsAndReturn',
    ])
  })

  it('「ためすに戻る」は保存せず、書きかけを捨ててから出る', () => {
    const label = source.indexOf("'kantan:meanings.backToTry'")
    expect(label).toBeGreaterThan(0)
    const button = source.slice(source.lastIndexOf('<button', label), label)
    expect(button).not.toMatch(/save\w*\(/)
    // 捨てたあとの「取り込まない」を「ためす」の読み込みに渡す — この render の
    // state はまだ書きかけなので、渡さないと「まだ取り込まれていない列」の判断に
    // 書きかけが重なる（loadS6）。
    expect(button).toContain('confirmMeanings(undefined, discardMeaningsDraft())')
  })

  it('控えは③を開く effect が作り、保管庫が読めた欄をその内容に置き換える', () => {
    // 控えの代入を消しても「ためすに戻る」のテストは通ってしまう — 配線を見る。
    const start = source.indexOf('const openedMeaningsRef = useRef<MeaningsScreenOpened | null>(null)')
    expect(start).toBeGreaterThan(0)
    const effect = source.slice(start, source.indexOf('}, [step, kzDatasetId])', start))
    expect(effect).toContain(
      'openedMeaningsRef.current = meaningsScreenOpened(kzDatasetId, settledMeanings, excludedColumns)',
    )
    expect(effect).toContain('withStoredMeanings(openedMeaningsRef.current, stored)')
    expect(effect).toContain('withStoredDecisions(openedMeaningsRef.current, stored)')
    // 読めた内容は控えだけでなく画面にも届く（戻す先と見えているものが同じ）。
    expect(effect).toContain('setSettledMeanings(opened.meanings)')
    expect(effect).toContain('setExcludedColumns(opened.excluded)')
  })

  it('書きかけを捨てる先は、③を開いたときの控え（意味と「取り込まない」の両方）', () => {
    const start = source.indexOf('function discardMeaningsDraft(')
    expect(start).toBeGreaterThan(0)
    const body = source.slice(start, source.indexOf('\n  }\n', start))
    expect(body).toContain('unsavedDraftDiscarded(openedMeaningsRef.current, kzDatasetId)')
    expect(body).toContain('setSettledMeanings(restored.meanings)')
    expect(body).toContain('setExcludedColumns(restored.excluded)')
  })

  it('データセットがあるとき③から「ためす」へ出る道は、保存して出るか、捨てて出るかだけ', () => {
    // ⑤「この形で進む」と④の組み立てが送るのは画面の state で、保管庫を読み直さ
    // ない。state が保管庫と違ってよいのは③を開いているあいだだけ — 出口は保存
    // （state を保管庫へ）か捨てる（保管庫を state へ）の 2 つで、素の
    // `confirmMeanings()` で出る道を③に足すと、書きかけがそのまま設計に効く。
    const screen = source.slice(
      source.indexOf("step === 10 ? ("),
      source.indexOf("step === 11 ? ("),
    )
    const toTry = [...screen.matchAll(/confirmMeanings\((?:[^()]|\([^()]*\))*\)/g)].map(
      (m) => m[0],
    )
    expect(toTry).toEqual(['confirmMeanings(undefined, discardMeaningsDraft())'])
    expect(screen).toContain('void saveMeaningsAndReturn()')
  })

  it('データセットを作る保存は、どの呼び出しも設計の前に決めたことを渡す', () => {
    const calls = [...source.matchAll(/materializeSchema\(([^)]*)\)/g)].map((m) => m[1])
    expect(calls.length).toBeGreaterThan(0)
    for (const args of calls) expect(args).toMatch(/\bsettled\b/)
  })
})
