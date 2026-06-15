import { Component, type ErrorInfo, type ReactNode } from 'react'
import { Trans } from 'react-i18next'
import i18n from './i18n'

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
          <h1 className="app-error-title">{i18n.t('misc:error.title')}</h1>
          {isRemoveChild ? (
            <p className="app-error-body">
              <Trans
                i18n={i18n}
                i18nKey="misc:error.removeChildBody"
                components={{ 1: <strong />, 3: <br />, 5: <strong /> }}
              />
            </p>
          ) : (
            <p className="app-error-body">{i18n.t('misc:error.genericBody')}</p>
          )}
          <p className="app-error-note">{i18n.t('misc:error.note')}</p>
          <button type="button" className="app-error-btn" onClick={() => window.location.reload()}>
            {i18n.t('misc:error.reload')}
          </button>
          <details className="app-error-details">
            <summary>{i18n.t('misc:error.detailsSummary')}</summary>
            <pre className="app-error-pre">{this.state.error.message}</pre>
          </details>
        </div>
      </div>
    )
  }
}
