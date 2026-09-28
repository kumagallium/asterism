import { describe, expect, it } from 'vitest'
import { fieldLabel, isBuiltinTool, withFieldLabels } from './builtinFields'
import type { ItemSpec } from './viewSpec'

// 架空の 2 分野（図書館の貸出／気象観測）のツール名・キーで確かめる。判定は
// ツール名の所属（組み込みか宣言か）だけで、キーやツール名に分野語は無い。

const translations: Record<string, string> = {
  'cards:builtin.fields.property': '項目',
  'cards:builtin.fields.value': '値',
  'cards:builtin.fields.category': '分類',
  'cards:builtin.fields.count': '件数',
}

function t(key: string): string {
  return translations[key] ?? key
}

describe('isBuiltinTool', () => {
  it('契約メモの 6 つの組み込みツール名を builtin と判定する', () => {
    for (const tool of ['subject_facts', 'subject_sources', 'subject_flow', 'set_members', 'set_breakdown', 'set_count']) {
      expect(isBuiltinTool(tool)).toBe(true)
    }
  })

  it('宣言ツール（<dataset_id>/<tool_name> の形）は builtin ではない', () => {
    expect(isBuiltinTool('library-checkouts/monthly_totals')).toBe(false)
    expect(isBuiltinTool('weather-observations/station_summary')).toBe(false)
  })
})

describe('fieldLabel', () => {
  it('組み込みツールの既知キーは訳を返す', () => {
    expect(fieldLabel('subject_facts', 'property', t)).toBe('項目')
    expect(fieldLabel('subject_facts', 'value', t)).toBe('値')
    expect(fieldLabel('set_breakdown', 'category', t)).toBe('分類')
    expect(fieldLabel('set_breakdown', 'count', t)).toBe('件数')
  })

  it('宣言ツールは undefined（呼び出し側はキーの人間化にフォールバック）', () => {
    expect(fieldLabel('library-checkouts/monthly_totals', 'property', t)).toBeUndefined()
  })

  it('組み込みツールでも訳が無いキーは undefined', () => {
    expect(fieldLabel('subject_facts', 'unknown_key', t)).toBeUndefined()
  })
})

describe('withFieldLabels', () => {
  it('組み込みツールの item に label を焼き込む（入力を書き換えない）', () => {
    const item: Record<string, ItemSpec> = {
      property: { var: 'property' },
      value: { var: 'value', number: true },
    }
    const before = JSON.parse(JSON.stringify(item)) as unknown
    const out = withFieldLabels('subject_facts', item, t)
    expect(out.property.label).toBe('項目')
    expect(out.value.label).toBe('値')
    expect(item).toEqual(before)
  })

  it('宣言ツールはそのまま返す（label を足さない）', () => {
    const item: Record<string, ItemSpec> = { property: { var: 'property' } }
    const out = withFieldLabels('library-checkouts/monthly_totals', item, t)
    expect(out.property.label).toBeUndefined()
  })

  it('既に label が付いている列はそのまま使う（上書きしない）', () => {
    const item: Record<string, ItemSpec> = { property: { var: 'property', label: '既存の表示名' } }
    const out = withFieldLabels('subject_facts', item, t)
    expect(out.property.label).toBe('既存の表示名')
  })

  it('同じ入力には同じ出力を返す（決定論）', () => {
    const item: Record<string, ItemSpec> = { category: { var: 'category', role: 'category' } }
    expect(withFieldLabels('set_breakdown', item, t)).toEqual(withFieldLabels('set_breakdown', item, t))
  })
})
