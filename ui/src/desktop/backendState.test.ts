// nextBackendState の状態遷移テスト。Tauri API にも window/fetch にも依存しない
// 純粋関数なので、DOM 環境を用意せずそのまま検証できる。
//
// 実行: npm test（vitest run）

import { test, assert } from 'vitest'
import { nextBackendState } from './backendState'

test('ok 中の 1 回の失敗だけでは down にしない', () => {
  const r = nextBackendState('ok', 0, false)
  assert.equal(r.state, 'ok')
  assert.equal(r.consecutiveFailures, 1)
})

test('ok 中に 2 回連続で失敗すると down になる', () => {
  const r1 = nextBackendState('ok', 0, false)
  const r2 = nextBackendState(r1.state, r1.consecutiveFailures, false)
  assert.equal(r1.state, 'ok')
  assert.equal(r2.state, 'down')
  assert.equal(r2.consecutiveFailures, 2)
})

test('down 中に生き返ると ok に戻り、カウントも 0 に戻る', () => {
  const r = nextBackendState('down', 5, true)
  assert.equal(r.state, 'ok')
  assert.equal(r.consecutiveFailures, 0)
})

test('down 中に失敗が続いても down のまま', () => {
  const r = nextBackendState('down', 2, false)
  assert.equal(r.state, 'down')
  assert.equal(r.consecutiveFailures, 3)
})

test('ok 中に成功すると失敗カウントが 0 にリセットされる', () => {
  const afterOneFail = nextBackendState('ok', 0, false)
  const afterRecover = nextBackendState(afterOneFail.state, afterOneFail.consecutiveFailures, true)
  assert.equal(afterRecover.state, 'ok')
  assert.equal(afterRecover.consecutiveFailures, 0)
})
