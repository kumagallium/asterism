import { Component, type ErrorInfo, type ReactNode } from 'react'

interface State {
  error: Error | null
}

/**
 * App-level error boundary.
 *
 * Without this, an uncaught error during React's render/commit phase blanks the
 * whole page (white screen). The most common trigger in this bilingual (JA/EN)
 * UI is the browser's *page translation* (or a DOM-mutating extension): it wraps
 * text nodes, so when React next removes a subtree it throws
 * "Failed to execute 'removeChild' on 'Node'". React itself points at error
 * boundaries for this class of error. We catch it and show a recoverable message
 * (with a reload action) instead of a dead white screen — any work already saved
 * server-side (e.g. a materialized dataset) is unaffected and visible after reload.
 */
export class ErrorBoundary extends Component<{ children: ReactNode }, State> {
  state: State = { error: null }

  static getDerivedStateFromError(error: Error): State {
    return { error }
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    // Keep a console trace for debugging; the UI shows the friendly fallback.
    console.error('Unhandled UI error (caught by ErrorBoundary):', error, info)
  }

  render() {
    if (!this.state.error) return this.props.children
    const isRemoveChild = /removeChild|insertBefore|not a child/i.test(
      this.state.error.message,
    )
    return (
      <div className="app-error-boundary" role="alert">
        <div className="app-error-card">
          <h1 className="app-error-title">表示の更新でエラーが発生しました</h1>
          {isRemoveChild ? (
            <p className="app-error-body">
              ブラウザの<strong>ページ翻訳</strong>や一部の拡張機能が画面の文字を書き換えると、
              この画面の更新と衝突してこのエラーになります。
              <br />
              この画面の<strong>翻訳をオフ</strong>にしてから再読み込みしてください。
            </p>
          ) : (
            <p className="app-error-body">
              予期しないエラーが発生しました。再読み込みすると復帰できます。
            </p>
          )}
          <p className="app-error-note">
            直前の保存はサーバに反映されている場合があります（再読み込み後にカタログをご確認ください）。
          </p>
          <button type="button" className="app-error-btn" onClick={() => window.location.reload()}>
            再読み込み
          </button>
          <details className="app-error-details">
            <summary>エラーの詳細</summary>
            <pre className="app-error-pre">{this.state.error.message}</pre>
          </details>
        </div>
      </div>
    )
  }
}
