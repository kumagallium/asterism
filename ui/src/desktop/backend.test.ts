// getBackendState() の最小テスト。
//
// backend.ts は Tauri 依存の import を持つが、vitest（node 環境）でも import 自体は
// 通る（`@tauri-apps/*` への import は dynamic import か型のみなので、モジュール読み込み
// 時点では評価されない）。ただし状態を書き換える `tick()`/`setState` は非公開で、
// `startBackendWatch()` も `isTauri()` が false（このテスト環境）だと何もしないため、
// down への遷移を外から駆動して検証することはできない — できるのは「モジュール初期値を
// 正しく読めること」の確認まで。down への遷移は backendState.test.ts の純粋関数テストが
// 別途カバーしている。
import { test, assert } from 'vitest'
import { getBackendState } from './backend'

test('getBackendState はモジュールの現在値を返す（初期値は ok）', () => {
  assert.equal(getBackendState(), 'ok')
})
