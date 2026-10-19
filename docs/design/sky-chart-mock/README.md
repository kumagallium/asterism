# 星図ホームの参考実装（モック・見本データ）

ADR [`../../architecture/sky-chart-two-entries.md`](../../architecture/sky-chart-two-entries.md) の見た目の正。
`index.html` をブラウザで開くと動く（サーバ不要・依存なし）。`skychart.js` は決定論配置（乱数も時刻も使わない）の canvas 2D エンジン、`data.js` は見本のデータ（件数は仮）。
実装（`ui/src/sky/`）はこのエンジンを TypeScript に移植する。見た目を変えるときは、まずここで試してから実装に写す。
