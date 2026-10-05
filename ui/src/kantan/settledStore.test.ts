// ③「意味をつける」で決めた意味と「取り込まない」が、データセットの保管庫に
// 届く経路。
//
// 初回の流れでは、これを保管庫に写していたのが畳んだ「数の確認」の画面だった。
// 畳んだあとは誰も書かず、外した列は置いたブラウザの state にしか無かった
// （2026-10-05 に実機で確認: 別のブラウザで見直すと「取り込む」に戻る）。
// いまは、データセットを**作る**保存が運び、サーバが同じ一歩で書く。できた
// あとは③の「この意味を保存して戻る」だけが書く —「ためすに戻る」は保存しない。
import { describe, expect, it } from 'vitest'
import { materializeRequestBody } from '../api'
import { columnKey, storedExclusionKeys, withdrawnExclusions } from './settledStore'
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

  it('「ためすに戻る」は保存しない', () => {
    const label = source.indexOf("'kantan:meanings.backToTry'")
    expect(label).toBeGreaterThan(0)
    const button = source.slice(source.lastIndexOf('<button', label), label)
    expect(button).toContain('confirmMeanings()')
    expect(button).not.toMatch(/save\w*\(/)
  })

  it('データセットを作る保存は、どの呼び出しも設計の前に決めたことを渡す', () => {
    const calls = [...source.matchAll(/materializeSchema\(([^)]*)\)/g)].map((m) => m[1])
    expect(calls.length).toBeGreaterThan(0)
    for (const args of calls) expect(args).toMatch(/\bsettled\b/)
  })
})
