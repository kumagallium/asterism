import { renderToStaticMarkup } from 'react-dom/server'
import { describe, expect, it } from 'vitest'
import '../i18n'
import { ScopeBar } from './ScopeBar'

describe('ScopeBar', () => {
  it('「〈名前〉について」と外す ×', () => {
    const h = renderToStaticMarkup(<ScopeBar name="熱伝導率" onClear={() => undefined} />)
    expect(h).toContain('data-testid="scope-chip"')
    expect(h).toContain('熱伝導率 について')
    expect(h).toContain('data-testid="scope-clear"')
  })
})
