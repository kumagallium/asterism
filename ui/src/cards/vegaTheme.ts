// Vega-Lite の見た目を house style に固定する。値は `index.css` の `:root` から
// 写している ── **`index.css` を変えたらここも同時に更新する**。
//
// 目的: 描画器（VegaLiteView）が house 以外の色を出さないこと。ライブラリの
// 既定色や、`custom: true`（Phase 2・LLM が書いた仕様）が持ち込む色を
// `withHouseStyle` が必ず上書きする ── spec 側の `config` は「データであって
// 実行しない」ので、色の最終決定権は常にこのファイルが持つ。

import type { VegaLiteSpec } from './viewSpec'

/** `index.css` の `:root` から写した値そのもの（生成しない・計算しない）。 */
export const HOUSE_TOKENS = {
  entity: '#3a7a4c',
  activity: '#356794',
  // `area.fill` に使う柔らかい版。`--activity-soft` も `index.css` の値そのまま。
  activitySoft: '#e4eef6',
  accent: '#b3722b',
  muted: '#54695b',
  faint: '#728579',
  border: '#dde6da',
  borderStrong: '#c7d4c4',
  fg: '#16241a',
  body: '#33453a',
  surface: '#ffffff',
  fontUi:
    "'Hanken Grotesk', 'Zen Kaku Gothic New', 'Noto Sans JP', system-ui, Avenir, Helvetica, Arial, sans-serif",
  fontMono: "'IBM Plex Mono', ui-monospace, SFMono-Regular, Menlo, monospace",
} as const

/** vitest（node 環境）でも読める、house 固定の Vega-Lite `config`。 */
export function houseConfig(): Record<string, unknown> {
  return {
    background: HOUSE_TOKENS.surface,
    font: HOUSE_TOKENS.fontUi,
    axis: {
      labelColor: HOUSE_TOKENS.muted,
      titleColor: HOUSE_TOKENS.fg,
      gridColor: HOUSE_TOKENS.border,
      domainColor: HOUSE_TOKENS.borderStrong,
      tickColor: HOUSE_TOKENS.borderStrong,
      labelFont: HOUSE_TOKENS.fontUi,
      titleFont: HOUSE_TOKENS.fontUi,
      // 内訳（breakdown）の y 軸ラベル（分類名）が長いカードの幅を超えて図を
      // 押し広げないよう省略する（Vega-Lite が自動で「…」を付ける）。
      labelLimit: 160,
    },
    // y 軸タイトルとラベルの間を空ける — 内訳が 1 本のバーだけのとき、タイトル
    // とラベルが重なって読めなくなるのを防ぐ。
    axisY: {
      titlePadding: 12,
    },
    // カード内の限られた高さでも軸タイトルまで収まるよう、凡例は右ではなく
    // 下に横並びで小さく出す（右に出すと図が縦に伸びてカードからはみ出す）。
    legend: {
      orient: 'bottom',
      direction: 'horizontal',
      labelColor: HOUSE_TOKENS.muted,
      titleColor: HOUSE_TOKENS.fg,
      labelFont: HOUSE_TOKENS.fontUi,
      titleFont: HOUSE_TOKENS.fontUi,
      labelFontSize: 10,
      titleFontSize: 10,
    },
    view: { stroke: HOUSE_TOKENS.border },
    // 系列の色。activity（処理・つながり）を先頭にして、量の主役 entity が続く。
    range: {
      category: [
        HOUSE_TOKENS.activity,
        HOUSE_TOKENS.entity,
        HOUSE_TOKENS.accent,
        HOUSE_TOKENS.muted,
        HOUSE_TOKENS.faint,
      ],
    },
    line: { stroke: HOUSE_TOKENS.activity },
    point: { fill: HOUSE_TOKENS.activity },
    bar: { fill: HOUSE_TOKENS.activity },
    area: { fill: HOUSE_TOKENS.activitySoft },
  }
}

const VEGA_LITE_SCHEMA = 'https://vega.github.io/schema/vega-lite/v6.json'

function isPlainObject(v: unknown): v is Record<string, unknown> {
  return typeof v === 'object' && v !== null && !Array.isArray(v)
}

/** `override` を `base` に深く合成する。両方にオブジェクトがあれば再帰、
 *  それ以外（配列・プリミティブ）は `override` が勝つ。どちらの入力も
 *  書き換えない（新しいオブジェクトだけを組み立てる）。 */
function deepMerge(
  base: Record<string, unknown>,
  override: Record<string, unknown>,
): Record<string, unknown> {
  const out: Record<string, unknown> = { ...base }
  for (const key of Object.keys(override)) {
    const b = base[key]
    const o = override[key]
    out[key] = isPlainObject(b) && isPlainObject(o) ? deepMerge(b, o) : o
  }
  return out
}

/**
 * 仕様のコピーに house の見た目を焼き込む。**純関数**（入力を書き換えない）。
 * - `config`: spec 側にあっても house が勝つ（深い合成・葉は house 優先）。
 *   `custom: true`（LLM が書いた仕様）が色を持ち込んでも、ここで必ず上書きされる。
 * - `$schema`: 無ければ Vega-Lite v6 を足す（あれば触らない）。
 * - `autosize` / `width` / `height`: カードの箱（`VegaLiteView` の div）に必ず
 *   収まるよう、常にこの値にする。`height` が無いと Vega-Lite は既定 200px で
 *   描き、凡例・軸タイトルの分だけカードの下端からはみ出す。
 */
export function withHouseStyle(spec: VegaLiteSpec): VegaLiteSpec {
  const existingConfig = isPlainObject(spec.config) ? spec.config : {}
  const out: VegaLiteSpec = {
    ...spec,
    config: deepMerge(existingConfig, houseConfig()),
    autosize: { type: 'fit', contains: 'padding' },
    width: 'container',
    height: 'container',
  }
  if (out.$schema === undefined) {
    out.$schema = VEGA_LITE_SCHEMA
  }
  return out
}
