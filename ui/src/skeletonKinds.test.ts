// ADR `kind-splitting-and-consult-suggestions.md` §5 の「決定論の判定」。
// 同じキー・項目が重なる/分かれる の両方と、提案ブロックの反映（LLM 0）。

import { describe, expect, it } from 'vitest'
import type { MappingSkeleton, SkeletonMap } from './api'
import {
  applyIdentifiers,
  applyOwners,
  applySplits,
  assignColumnOwner,
  currentOwnerOf,
  keyColumnsOf,
  kindDisplayName,
  kindFieldValue,
  catalogLinkEdges,
  kindNamesByClass,
  kindLabelEdit,
  pendingLinkEdges,
  promoteColumnToKind,
  sameIdKind,
  sameIdSiblings,
  separateSharedKind,
  sharedKindGroups,
  slugMapName,
  twinKindNames,
} from './skeletonKinds'
import { mergeNodes } from './shapeGraph'

const SOURCE = 'xrd-card.csv'

function map(name: string, template: string, owns?: string[]): SkeletonMap {
  return {
    name,
    source: SOURCE,
    subject: { template, classes: [`xrr:${name}`] },
    ...(owns ? { owns } : {}),
  }
}

function skeleton(...maps: SkeletonMap[]): MappingSkeleton {
  return { version: 1, prefixes: { xrr: 'https://example.org/x#' }, maps }
}

describe('twinKindNames — D1 重複判定は項目の重なりまで見る', () => {
  it('同じキー・どちらも宣言なし = 本物の二重記録なので止める', () => {
    // 実測 2026-08-30: AI は放っておくと Sample と DiffractionPattern が同じ列で
    // 同じ 1 件を作る設計を出した。どちらも「残り全部」を持つので重なる。
    const s = skeleton(
      map('sample', 'xrr:sample/{No}'),
      map('pattern', 'xrr:pattern/{No}'),
    )
    expect([...twinKindNames(s)].sort()).toEqual(['pattern', 'sample'])
  })

  it('同じキーでも項目が分かれていれば何も言わない（カードと結晶）', () => {
    // G6 はキー共有を最初から許している（ONLY as a link/join key）。禁じている
    // のは同じ事実が 2 箇所にあること。
    const s = skeleton(
      map('card', 'xrr:card/{No}'),
      map('crystal', 'xrr:crystal/{No}', ['No', 'Cell', 'Volume', 'Z value']),
    )
    expect(twinKindNames(s).size).toBe(0)
  })

  it('両方が宣言していても、宣言が重なれば止める', () => {
    const s = skeleton(
      map('card', 'xrr:card/{No}', ['No', 'Name', 'Volume']),
      map('crystal', 'xrr:crystal/{No}', ['No', 'Cell', 'Volume']),
    )
    expect([...twinKindNames(s)].sort()).toEqual(['card', 'crystal'])
  })

  it('宣言がキー列だけの種類は、キーの共有だけなので重ならない', () => {
    // `sameIdKind` が足した直後の状態（`owns` = キー列）。ここで警告が出ると、
    // D4 の入口が「足した瞬間に怒られる」入口になってしまう。
    const s = skeleton(
      map('card', 'xrr:card/{No}'),
      map('crystal', 'xrr:crystal/{No}', ['No']),
    )
    expect(twinKindNames(s).size).toBe(0)
  })

  it('キーが違えばそもそも同じかたまりに入らない', () => {
    const s = skeleton(
      map('card', 'xrr:card/{No}'),
      map('peak', 'xrr:peak/{No}-{hkl}'),
    )
    expect(twinKindNames(s).size).toBe(0)
  })

  it('ソースが違えば別のかたまり', () => {
    const s = skeleton(map('a', 'xrr:a/{No}'), {
      ...map('b', 'xrr:b/{No}'),
      source: 'other.csv',
    })
    expect(twinKindNames(s).size).toBe(0)
  })

  it('3 つ並んでも、重なる 2 つだけを名指す', () => {
    const s = skeleton(
      map('card', 'xrr:card/{No}'),
      map('crystal', 'xrr:crystal/{No}', ['No', 'Cell']),
      map('ghost', 'xrr:ghost/{No}'),
    )
    expect([...twinKindNames(s)].sort()).toEqual(['card', 'ghost'])
  })
})

describe('実データの形（XRD 参考カード・2026-08-30 の実測）', () => {
  // 利用者の registry にある同じファイルで一周した結果を、形だけ固定する
  // （データそのものはリポジトリに入れない）。サーバ側の裏取りは
  // `annotate_skeleton` + `ensure_same_source_links` で別途確認済み:
  // 分けた後も pattern が親のままで、peak → crystal の参照が containment 経路
  // （`dcterms:isPartOf` → `xrdr:crystal/{No}`）で張られる。
  const CARD = map('pattern', 'xrdr:pattern/{No}')
  const PEAK = map('peak', 'xrdr:peak/{No}/{(hkl)}')

  it('AI が出した二重記録（pattern と sample が同じ {No}）は止まる', () => {
    const s = skeleton(CARD, map('sample', 'xrdr:sample/{No}'), PEAK)
    expect([...twinKindNames(s)].sort()).toEqual(['pattern', 'sample'])
  })

  it('カードと結晶に分けた後は、何も言わない', () => {
    const crystal = map('crystal', 'xrdr:crystal/{No}', [
      'No',
      'Cell',
      'Volume',
      'Z value',
      'Space Group',
      'Crystal System',
    ])
    expect(twinKindNames(skeleton(CARD, crystal, PEAK)).size).toBe(0)
  })
})

describe('slugMapName — 骨格スキーマの map 名（^[A-Za-z][\\w-]*$）に収める', () => {
  it('英字の名前はそのまま落とし込む', () => {
    expect(slugMapName('Chemical Formula', new Set())).toBe('chemical_formula')
  })

  it('数字始まりは先頭を落とす（スキーマが英字始まりを要求する）', () => {
    expect(slugMapName('2theta', new Set())).toBe('theta')
  })

  it('ASCII に落ちない名前は fallback に逃がす', () => {
    expect(slugMapName('結晶', new Set())).toBe('kind')
  })

  it('すでにある名前とはぶつからない', () => {
    expect(slugMapName('結晶', new Set(['kind']))).toBe('kind2')
  })
})

describe('sameIdKind — D4「同じ ID で種類を足す」', () => {
  it('親と同じ鍵で数え、住所は親の下ではなく根に置く', () => {
    const parent = map('card', 'xrr:card/{No}')
    const added = sameIdKind(parent, 'crystal', ['xrr:結晶'])
    expect(added.subject.template).toBe('xrr:crystal/{No}')
    expect(keyColumnsOf(added)).toEqual(['No'])
    expect(added.source).toBe(SOURCE)
  })

  it('複数列のキーもそのまま引き継ぐ', () => {
    const parent = map('peak', 'https://ex.org/resource/peak/{No}-{hkl}')
    const added = sameIdKind(parent, 'crystal', [])
    expect(added.subject.template).toBe('https://ex.org/resource/crystal/{No}-{hkl}')
  })

  it('足した直後に二重記録の警告が出ない', () => {
    const parent = map('card', 'xrr:card/{No}')
    const s = skeleton(parent, sameIdKind(parent, 'crystal', []))
    expect(twinKindNames(s).size).toBe(0)
  })
})

describe('sameIdSiblings — 「載せる種類」の行き先', () => {
  it('カードと同じ鍵で数える種類を全部返す（2 つあっても落とさない）', () => {
    // 実機 2026-08-31: これを「列 → 種類」の対応表に混ぜていたため、同じ鍵の
    // 兄弟が 2 つあると後勝ちで片方が消え、D4 で足した種類に項目を移せなかった。
    const s = skeleton(
      map('card', 'xrr:card/{No}'),
      map('crystal', 'xrr:crystal/{No}', ['No']),
      map('sample', 'xrr:sample/{No}'),
      map('peak', 'xrr:peak/{No}-{hkl}'),
    )
    expect(sameIdSiblings(s, 'card').sort()).toEqual(['crystal', 'sample'])
  })

  it('鍵が違う種類・別ソースの種類は入らない', () => {
    const s = skeleton(map('card', 'xrr:card/{No}'), map('csd', 'xrr:csd/{CSD}'), {
      ...map('other', 'xrr:other/{No}'),
      source: 'other.csv',
    })
    expect(sameIdSiblings(s, 'card')).toEqual([])
  })

  it('知らない種類には何も返さない', () => {
    expect(sameIdSiblings(skeleton(map('card', 'xrr:card/{No}')), 'nope')).toEqual([])
  })
})

describe('assignColumnOwner / currentOwnerOf — 載せる種類', () => {
  it('宣言の無い列は、そのソースのカードが持っている', () => {
    const s = skeleton(map('card', 'xrr:card/{No}'), map('crystal', 'xrr:crystal/{No}', ['No']))
    expect(currentOwnerOf(s, SOURCE, 'Cell')).toBe('card')
  })

  it('載せ替えると、ほかの宣言からは外れる（G6: 属性は 1 箇所だけ）', () => {
    const s = skeleton(
      map('card', 'xrr:card/{No}', ['Cell']),
      map('crystal', 'xrr:crystal/{No}', ['No']),
    )
    const next = assignColumnOwner(s, SOURCE, 'Cell', 'crystal')
    expect(next.maps[0].owns).toBeUndefined()
    expect(next.maps[1].owns).toEqual(['No', 'Cell'])
  })
})

describe('applySplits — D3 splits', () => {
  it('同じキーの新しい種類を作り、指定された列だけを移す', () => {
    const s = skeleton(map('card', 'xrr:card/{No}'), map('peak', 'xrr:peak/{No}-{hkl}'))
    const out = applySplits(s, [
      { from: 'card', name: 'Crystal', columns: ['Cell', 'Volume', 'Z value'] },
    ])
    expect(out.applied).toBe(1)
    const added = out.skeleton.maps.find((m) => m.name === 'crystal')!
    expect(added.subject.classes).toEqual(['Crystal'])
    expect(added.subject.template).toBe('xrr:crystal/{No}')
    expect(added.owns).toEqual(['No', 'Cell', 'Volume', 'Z value'])
    // 分けた結果に、二重記録の警告は出ない（完了条件 1）。
    expect(twinKindNames(out.skeleton).size).toBe(0)
  })

  it('キー列は移さない（ID の作り方は変えない）', () => {
    const s = skeleton(map('card', 'xrr:card/{No}'))
    const out = applySplits(s, [{ from: 'card', name: 'Crystal', columns: ['No', 'Cell'] }])
    expect(out.skeleton.maps.find((m) => m.name === 'crystal')!.owns).toEqual(['No', 'Cell'])
    // `No` が入っているのは兄弟のキーとしてであって、`card` の ID は変わらない。
    expect(out.skeleton.maps[0].subject.template).toBe('xrr:card/{No}')
  })

  it('知らない map 名・空の列は何もしない', () => {
    const s = skeleton(map('card', 'xrr:card/{No}'))
    const out = applySplits(s, [
      { from: 'nope', name: '結晶', columns: ['Cell'] },
      { from: 'card', name: '結晶', columns: [] },
    ])
    expect(out.applied).toBe(0)
    expect(out.skipped).toBe(2)
    expect(out.skeleton).toEqual(s)
  })
})

describe('applyOwners — D3 owners（同じ分散クラス内に限る）', () => {
  const classOf = (name: string) =>
    ({ card: 'singleton', crystal: 'singleton', peak: 'unique' })[name]

  it('同じ分散クラスなら載せ替える', () => {
    const s = skeleton(
      map('card', 'xrr:card/{No}'),
      map('crystal', 'xrr:crystal/{No}', ['No']),
    )
    const out = applyOwners(s, [{ column: 'Cell', map: 'crystal' }], classOf)
    expect(out.applied).toBe(1)
    expect(out.skeleton.maps[1].owns).toEqual(['No', 'Cell'])
  })

  it('ファイル全体の値を行の種類へは移さない（G6 違反になる）', () => {
    const s = skeleton(map('card', 'xrr:card/{No}'), map('peak', 'xrr:peak/{No}-{hkl}'))
    const out = applyOwners(s, [{ column: 'Radiation', map: 'peak' }], classOf)
    expect(out.applied).toBe(0)
    expect(out.skipped).toBe(1)
    expect(out.skeleton).toEqual(s)
  })

  it('すでにそこに載っている列は数えない', () => {
    const s = skeleton(
      map('card', 'xrr:card/{No}'),
      map('crystal', 'xrr:crystal/{No}', ['No', 'Cell']),
    )
    const out = applyOwners(s, [{ column: 'Cell', map: 'crystal' }], classOf)
    expect(out.applied).toBe(0)
    expect(out.skipped).toBe(1)
  })

  it('分散クラスが分からない map へは動かさない', () => {
    const s = skeleton(map('card', 'xrr:card/{No}'), map('other', 'xrr:other/{No}', ['No']))
    const out = applyOwners(s, [{ column: 'Cell', map: 'other' }], classOf)
    expect(out.applied).toBe(0)
  })
})

describe('applyIdentifiers / promoteColumnToKind — D3 identifiers', () => {
  it('その列そのものをキーにした種類を作る（①のチェックと同じ経路）', () => {
    const s = skeleton(map('card', 'xrr:card/{No}'))
    const out = applyIdentifiers(
      s,
      [{ column: 'Chemical Formula', reason: '外のデータと同じ名前で呼ばれる' }],
      'card',
    )
    expect(out.applied).toBe(1)
    const added = out.skeleton.maps.find((m) => m.name === 'chemical_formula')!
    expect(added.subject.template).toBe('xrr:chemical_formula/{Chemical Formula}')
    expect(added.owns).toEqual(['Chemical Formula'])
    expect(added.subject.classes).toEqual(['xrr:ChemicalFormula'])
  })

  it('すでにその列で数える種類があれば作らない', () => {
    const s = promoteColumnToKind(skeleton(map('card', 'xrr:card/{No}')), 'card', 'CSD')
    const out = applyIdentifiers(s, [{ column: 'CSD', reason: 'カード番号' }], 'card')
    expect(out.applied).toBe(0)
    expect(out.skipped).toBe(1)
  })

  it('カード自身のキー列は昇格しない', () => {
    const s = skeleton(map('card', 'xrr:card/{No}'))
    const out = applyIdentifiers(s, [{ column: 'No', reason: 'カード番号' }], 'card')
    expect(out.applied).toBe(0)
  })
})

describe('kindLabelEdit — 契約メモ a・R4「名前・ID を直す」欄（かんたんモード）', () => {
  const NS = { ontology_prefix: 'xrr' }

  it('日本語を打つと label と classes（1 つ・符号つき）が両方入る', () => {
    const patch = kindLabelEdit('食材の名前', NS)
    expect(patch.label).toBe('食材の名前')
    expect(patch.classes).toEqual(['xrr:Record_389a00'])
  })

  it('英字を打つと classes も英字のまま作られる（Python の _class_name と同じ規則）', () => {
    const patch = kindLabelEdit('Shelf Item', NS)
    expect(patch.label).toBe('Shelf Item')
    expect(patch.classes).toEqual(['xrr:ShelfItem'])
  })

  it('打った直後の空白は保つ（1 文字ずつ打てる）。classes は空白を落として作る', () => {
    const patch = kindLabelEdit('Shelf ', NS)
    expect(patch.label).toBe('Shelf ')
    expect(patch.classes).toEqual(['xrr:Shelf'])
    const wrapped = kindLabelEdit('  店の名前  ', NS)
    expect(wrapped.label).toBe('  店の名前  ')
    expect(wrapped.classes).toEqual(['xrr:Record_98874d'])
  })

  it('空にすると label も機械が作った classes も外れ、欄は空欄のまま', () => {
    const patch = kindLabelEdit('   ', NS)
    expect(patch).toEqual({ label: undefined, classes: [] })
    const emptied = { template: 'xrr:a/{id}', ...patch }
    expect(kindFieldValue(emptied, NS)).toBe('')
    expect(kindDisplayName(emptied, NS)).toBe('')
  })

  it('同じ文字列からはいつも同じ classes になる', () => {
    expect(kindLabelEdit('温度 (K)', NS).classes).toEqual(kindLabelEdit('温度 (K)', NS).classes)
  })
})


describe('kindFieldValue / kindDisplayName / kindNamesByClass — 表示名を先に読む（R3）', () => {
  const NS = { ontology_prefix: 'xrr' }

  it('欄は表示名を打ったまま（末尾の空白つき）、無ければ公開名を短くしたもの', () => {
    expect(kindFieldValue({ label: 'Shelf ', classes: ['xrr:Shelf'] }, NS)).toBe('Shelf ')
    expect(kindFieldValue({ classes: ['xrr:Sample'] }, NS)).toBe('Sample')
    expect(kindFieldValue({ label: '温度 (K)', classes: ['xrr:K_174149'] }, NS)).toBe('温度 (K)')
  })

  it('文言は表示名（前後の空白なし）が先。符号つきの公開名は出ない', () => {
    const subject = { label: ' 食材の名前 ', classes: ['xrr:Record_389a00'] }
    expect(kindDisplayName(subject, NS)).toBe('食材の名前')
    expect(kindDisplayName({ classes: ['xrr:Sample'] }, NS)).toBe('Sample')
  })

  it('クラス名の文字列しか持たない場所（証拠カード）も表示名を引く', () => {
    const s = skeleton(
      { ...map('a', 'xrr:a/{id}'), subject: { template: 'xrr:a/{id}', classes: ['xrr:Record_389a00'], label: '食材の名前' } },
      map('b', 'xrr:b/{id}'),
    )
    const nameOf = kindNamesByClass(s, NS)
    expect(nameOf('xrr:Record_389a00')).toBe('食材の名前')
    expect(nameOf('xrr:Other')).toBe('Other')
  })

  it('同じ名前を 2 つの種類に付けても Record_ が出ない', () => {
    const s = skeleton(
      { ...map('a', 'xrr:a/{id}'), subject: { template: 'xrr:a/{id}', classes: ['xrr:Record_389a00'], label: '店の名前' } },
      { ...map('b', 'xrr:b/{id}'), subject: { template: 'xrr:b/{id}', classes: ['xrr:Record_389a00'], label: '店の名前' } },
    )
    expect(s.maps.map((m) => kindDisplayName(m.subject, NS)).join('、')).not.toMatch(/Record_/)
  })

  it('sameIdKind は表示名を持てる', () => {
    const parent = map('card', 'xrr:card/{No}')
    const added = sameIdKind(parent, 'record_389a00', ['xrr:Record_389a00'], '食材の名前')
    expect(added.subject.label).toBe('食材の名前')
    expect(sameIdKind(parent, 'x', []).subject.label).toBeUndefined()
  })
})

describe('pendingLinkEdges — ⑤の点線は「その列を元々持っていた種類」から（K58）', () => {
  const columnKinds = new Map<string, string[]>([
    ['category', ['category']],
    ['brand', ['brand']],
  ])

  it('前置きの列の受け口はカードから、行ごとに変わる列の受け口は行の種類から', () => {
    expect(
      pendingLinkEdges({
        hostName: 'card',
        rowMap: 'record',
        varyingColumns: ['food', 'amount', 'brand'],
        columnKinds,
      }),
    ).toEqual([
      ['card', 'category'],
      ['record', 'brand'],
    ])
  })

  it('行の種類が無ければ全部カードから（今までどおり）', () => {
    expect(
      pendingLinkEdges({
        hostName: 'card',
        rowMap: undefined,
        varyingColumns: ['brand'],
        columnKinds,
      }),
    ).toEqual([
      ['card', 'category'],
      ['card', 'brand'],
    ])
  })

  it('同じ列の受け口が 2 つあれば 2 本、同じ線は 1 本、自分への線は引かない', () => {
    expect(
      pendingLinkEdges({
        hostName: 'card',
        rowMap: 'record',
        varyingColumns: [],
        columnKinds: new Map([
          ['Space Group', ['crystal_structure', 'space_group']],
          ['Again', ['space_group']],
          ['Self', ['card']],
        ]),
      }),
    ).toEqual([
      ['card', 'crystal_structure'],
      ['card', 'space_group'],
    ])
  })
})

describe('catalogLinkEdges — ⑤の点線は全ファイルの受け口に引く', () => {
  const at = (name: string, source: string, template: string, owns?: string[]): SkeletonMap => ({
    name,
    source,
    subject: { template, classes: [] },
    ...(owns ? { owns } : {}),
  })
  // 実機 2026-09-30 の形: いちばん大きい表（curves）がゾーン、samples は別ファイル。
  const maps = [
    at('curve', 'curves.csv', 'xr:curve/{sample_id}/{figure_id}'),
    at('composition', 'curves.csv', 'xr:composition/{composition}', ['composition']),
    at('sample_info', 'samples.csv', 'xr:sample_info/{SID}'),
    at('sample', 'samples.csv', 'xr:sample/{SID}/{sample_id}'),
    at('composition2', 'samples.csv', 'xr:composition2/{composition}', ['composition']),
  ]
  const isCatalog = (m: SkeletonMap) =>
    (m.owns ?? []).length === 1 && m.subject.template?.endsWith(`{${m.owns![0]}}`) === true

  it('ゾーンの外のファイルの受け口にも、サーバが言う元の種類から線が引かれる', () => {
    expect(
      catalogLinkEdges({
        maps,
        isCatalog,
        homeOf: (n) => ({ composition: 'curve', composition2: 'sample' })[n],
        local: [['curve', 'composition']],
      }),
    ).toEqual([
      ['curve', 'composition'],
      ['sample', 'composition2'],
    ])
  })

  it('サーバの注釈（組み立てと同じ規則）が画面の計算より先に勝つ', () => {
    const withRow = [...maps, at('card', 'curves.csv', 'xr:card/{figure_id}')]
    expect(
      catalogLinkEdges({
        maps: withRow,
        isCatalog,
        homeOf: (n) => (n === 'composition' ? 'curve' : undefined),
        local: [['card', 'composition']],
      }),
    ).toContainEqual(['curve', 'composition'])
    expect(
      catalogLinkEdges({
        maps: withRow,
        isCatalog,
        homeOf: (n) => (n === 'composition' ? 'curve' : undefined),
        local: [['card', 'composition']],
      }),
    ).not.toContainEqual(['card', 'composition'])
  })

  it('注釈が追いつく前（☑ した直後）は画面の計算で描く', () => {
    expect(
      catalogLinkEdges({
        maps,
        isCatalog,
        homeOf: () => undefined,
        local: [['curve', 'composition']],
      })[0],
    ).toEqual(['curve', 'composition'])
  })

  it('注釈が無いときは同じファイルの受け口でない最初の種類から', () => {
    expect(catalogLinkEdges({ maps, isCatalog, homeOf: () => undefined })).toEqual([
      ['curve', 'composition'],
      ['sample_info', 'composition2'],
    ])
  })

  it('別のファイルや受け口を指す注釈は信じない', () => {
    expect(
      catalogLinkEdges({
        maps,
        isCatalog,
        homeOf: (n) => ({ composition: 'sample', composition2: 'composition' })[n],
      }),
    ).toEqual([
      ['curve', 'composition'],
      ['sample_info', 'composition2'],
    ])
  })
})

describe('sharedKindGroups / mergeSharedKinds / separateSharedKind — 同じ意味の受け口は 1 つの種類（K63）', () => {
  const at = (name: string, source: string, template: string, owns?: string[]): SkeletonMap => ({
    name,
    source,
    subject: { template, classes: [`xo:${template.split(':')[1].split('/')[0]}`] },
    ...(owns ? { owns } : {}),
  })
  const maps = [
    at('record', 'curves.csv', 'xr:record/{sample_id}'),
    at('composition', 'curves.csv', 'xr:composition/{composition}', ['composition']),
    at('record2', 'samples.csv', 'xr:record2/{SID}'),
    at('composition2', 'samples.csv', 'xr:composition/{composition}', ['composition']),
  ]
  const isCatalog = (m: SkeletonMap) => (m.owns ?? []).length === 1

  it('ID の頭と種類名が同じ・ファイルが違う受け口を 1 つのかたまりにする', () => {
    expect(sharedKindGroups(maps, isCatalog)).toEqual([['composition', 'composition2']])
  })

  it('同じファイルの中の重なりは双子の領分なので、かたまりにしない', () => {
    const same = maps.map((m) => (m.name === 'composition2' ? { ...m, source: 'curves.csv' } : m))
    expect(sharedKindGroups(same, isCatalog)).toEqual([])
  })

  it('図では先頭の箱に畳み、ほかのメンバーへの線を付け替える', () => {
    const merged = mergeNodes(
      {
        nodes: maps.map((m) => ({ id: m.name, label: m.name, tone: 'record' as const })),
        edges: [
          { from: 'record', to: 'composition', pending: true },
          { from: 'record2', to: 'composition2', pending: true },
        ],
      },
      [['composition', 'composition2']],
    )
    expect(merged.nodes.map((n) => n.id)).toEqual(['record', 'composition', 'record2'])
    expect(merged.edges).toEqual([
      { from: 'record', to: 'composition', pending: true },
      { from: 'record2', to: 'composition', pending: true },
    ])
  })

  it('「別々の種類にする」で、自分の map 名から ID の頭と種類名を作り直す', () => {
    const next = separateSharedKind({ version: 1, prefixes: {}, maps }, 'composition2')
    const m = next.maps.find((x) => x.name === 'composition2')!
    expect(m.subject.template).toBe('xr:composition2/{composition}')
    expect(m.subject.classes).toEqual(['xo:Composition2'])
    expect(sharedKindGroups(next.maps, isCatalog)).toEqual([])
  })
})
