// `asciiNames`: 英字にできない名前から一意な識別子を作る道具（契約メモ a・R1/R4）。
//
// 5 つの見本の期待値は Python 版 `step0/tests/test_ascii_names.py` と同じ
// 値を固定してある（正本は段1の報告 interfaces）。片方だけ直った実装を
// 見逃さないための同一見本テスト。

import { describe, expect, it } from 'vitest'
import { classNameFromLabel, losesWords, nameTag, needsTag } from './asciiNames'

// 契約メモ a・R4 が指定する5つの見本。
const SAMPLES_NAME_TAG: Record<string, string> = {
  食材の名前: '389a00',
  店の名前: '98874d',
  '温度 (K)': '174149',
  価格表: '165085',
  'Shelf Item': '3a8921',
}

describe('nameTag', () => {
  it('5つの見本で Python 版と同じ値を返す', () => {
    for (const [text, expected] of Object.entries(SAMPLES_NAME_TAG)) {
      expect(nameTag(text)).toBe(expected)
    }
  })

  it('対になっていない代用符号位置でも落ちず、Python 版と同じ値になる', () => {
    // TextEncoder は U+FFFD に置き換える。Python 版も同じ置き換えをする。
    expect(nameTag('\ud83d')).toBe('03479c')
    expect(nameTag('\ud83d')).toBe(nameTag('\ufffd'))
  })

  it('同じ入力はいつも同じ符号', () => {
    expect(nameTag('食材の名前')).toBe(nameTag('食材の名前'))
  })

  it('違う日本語の名前は違う符号', () => {
    expect(nameTag('食材の名前')).not.toBe(nameTag('店の名前'))
  })

  it('6桁の16進', () => {
    const tag = nameTag('何でもいい文字列')
    expect(tag).toHaveLength(6)
    expect(() => Number.parseInt(tag, 16)).not.toThrow()
  })
})

describe('losesWords', () => {
  it('漢字・かなを含めば真', () => {
    expect(losesWords('食材の名前')).toBe(true)
    expect(losesWords('温度 (K)')).toBe(true)
  })

  it('英字・ギリシャ文字は偽（大文字小文字の区別を持つ）', () => {
    expect(losesWords('Resistivity (μΩ·cm)')).toBe(false)
    expect(losesWords('Shelf Item')).toBe(false)
  })
})

describe('classNameFromLabel', () => {
  it('ASCII の名前は今までどおり（1文字も変わらない）', () => {
    expect(classNameFromLabel('Resistivity(Ohm m)')).toBe('ResistivityOhmM')
    expect(classNameFromLabel('xrd_peaks')).toBe('XrdPeaks')
    expect(classNameFromLabel('Shelf Item')).toBe('ShelfItem')
  })

  it('ギリシャ文字混じりの ASCII 名も変わらない', () => {
    expect(classNameFromLabel('Resistivity (μΩ·cm)')).toBe('ResistivityCm')
  })

  it('英字にできない名前は fallback + 符号で一意になる（5つの見本）', () => {
    expect(classNameFromLabel('食材の名前')).toBe('Record_389a00')
    expect(classNameFromLabel('店の名前')).toBe('Record_98874d')
    expect(classNameFromLabel('温度 (K)')).toBe('K_174149')
    expect(classNameFromLabel('価格表')).toBe('Record_165085')
  })

  it('違う日本語の名前は違う識別子になる', () => {
    expect(classNameFromLabel('食材の名前')).not.toBe(classNameFromLabel('店の名前'))
  })

  it('同じ名前からはいつも同じ識別子', () => {
    expect(classNameFromLabel('食材の名前')).toBe(classNameFromLabel('食材の名前'))
  })
})

describe('空白の集合は Python 版と同じ（BOM は除く・NEL と制御文字は除かない）', () => {
  it('Python 版 test_ascii_names.py と同じ見本', () => {
    expect(nameTag('\ufeff温度')).toBe('703a58')
    expect(nameTag('温度')).toBe('703a58')
    expect(nameTag('温度\u0085')).toBe('ad4161')
    expect(nameTag('x\x1c')).toBe('f562e4')
  })
})

describe('英字・数字・記号だけの名前は符号を付けない', () => {
  it('needsTag は ASCII だけなら偽', () => {
    for (const text of ['2024', '1', '0.5', '(1)', '', '  ', '---', '(', '_']) {
      expect(needsTag(text, '')).toBe(false)
    }
    expect(classNameFromLabel('---')).toBe('Value')
    expect(classNameFromLabel('2024')).toBe('V2024')
  })
})
