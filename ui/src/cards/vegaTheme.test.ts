import { describe, expect, it } from 'vitest'
import { HOUSE_TOKENS, houseConfig, withHouseStyle } from './vegaTheme'
import type { VegaLiteSpec } from './viewSpec'

describe('withHouseStyle', () => {
  it('config を合成する（house に無いキーは spec 側を残す）', () => {
    const spec: VegaLiteSpec = {
      mark: 'line',
      config: { legend: { orient: 'bottom' }, customKey: { keep: true } },
    }
    const out = withHouseStyle(spec)
    const config = out.config as Record<string, unknown>
    expect((config.legend as Record<string, unknown>).orient).toBe('bottom')
    expect(config.customKey).toEqual({ keep: true })
    // house が足したキーも入っている。
    expect(config.range).toEqual(houseConfig().range)
  })

  it('spec 側の config の色を house が上書きする', () => {
    const spec: VegaLiteSpec = {
      mark: 'bar',
      config: {
        axis: { labelColor: '#ff00ff', titleColor: '#00ff00' },
        bar: { fill: '#ff00ff' },
        background: '#000000',
      },
    }
    const out = withHouseStyle(spec)
    const config = out.config as Record<string, unknown>
    expect((config.axis as Record<string, unknown>).labelColor).toBe(HOUSE_TOKENS.muted)
    expect((config.axis as Record<string, unknown>).titleColor).toBe(HOUSE_TOKENS.fg)
    expect((config.bar as Record<string, unknown>).fill).toBe(HOUSE_TOKENS.activity)
    expect(config.background).toBe(HOUSE_TOKENS.surface)
  })

  it('生の色コードが house の外から出てこない（category パレットは決まった順）', () => {
    const out = withHouseStyle({ mark: 'point' })
    const config = out.config as Record<string, unknown>
    expect((config.range as Record<string, unknown>).category).toEqual([
      HOUSE_TOKENS.activity,
      HOUSE_TOKENS.entity,
      HOUSE_TOKENS.accent,
      HOUSE_TOKENS.muted,
      HOUSE_TOKENS.faint,
    ])
  })

  it('入力を変更しない（純関数）', () => {
    const spec: VegaLiteSpec = {
      mark: 'line',
      config: { bar: { fill: '#123456' } },
    }
    const before = JSON.parse(JSON.stringify(spec)) as unknown
    withHouseStyle(spec)
    expect(spec).toEqual(before)
  })

  it('$schema が無ければ v6 を足すが、あれば触らない', () => {
    const withoutSchema = withHouseStyle({ mark: 'line' })
    expect(withoutSchema.$schema).toBe('https://vega.github.io/schema/vega-lite/v6.json')

    const withSchema = withHouseStyle({ mark: 'line', $schema: 'https://example.org/custom.json' })
    expect(withSchema.$schema).toBe('https://example.org/custom.json')
  })

  it('autosize と width/height を container 追従に補完する（カードの箱からはみ出さない）', () => {
    const out = withHouseStyle({ mark: 'line', width: 400, height: 200, autosize: 'none' })
    expect(out.autosize).toEqual({ type: 'fit', contains: 'padding' })
    expect(out.width).toBe('container')
    expect(out.height).toBe('container')
  })

  it('凡例は下・横並び（右に出るとカードから縦にはみ出すため）', () => {
    const out = withHouseStyle({ mark: 'line' })
    const config = out.config as Record<string, unknown>
    const legend = config.legend as Record<string, unknown>
    expect(legend.orient).toBe('bottom')
    expect(legend.direction).toBe('horizontal')
  })

  it('同じ入力に対して同じ出力を返す（決定論）', () => {
    const spec: VegaLiteSpec = { mark: 'bar', config: { view: { stroke: '#abcdef' } } }
    expect(withHouseStyle(spec)).toEqual(withHouseStyle(spec))
  })
})

describe('houseConfig', () => {
  it('mark 系の色は entity/activity の 2 色だけを使う（house の外の色を持たない）', () => {
    const config = houseConfig()
    expect((config.line as Record<string, unknown>).stroke).toBe(HOUSE_TOKENS.activity)
    expect((config.point as Record<string, unknown>).fill).toBe(HOUSE_TOKENS.activity)
    expect((config.bar as Record<string, unknown>).fill).toBe(HOUSE_TOKENS.activity)
    expect((config.area as Record<string, unknown>).fill).toBe(HOUSE_TOKENS.activitySoft)
  })

  it('軸ラベルは長すぎると省略する（内訳の y 軸が図の幅を押し広げない）', () => {
    const config = houseConfig()
    expect((config.axis as Record<string, unknown>).labelLimit).toBe(160)
  })

  it('y 軸タイトルとラベルの間を空ける（単一バーでタイトルと重ならない）', () => {
    const config = houseConfig()
    expect((config.axisY as Record<string, unknown>).titlePadding).toBeGreaterThan(0)
  })
})
