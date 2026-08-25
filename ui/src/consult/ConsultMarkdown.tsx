import ReactMarkdown, { type Components } from 'react-markdown'
import remarkGfm from 'remark-gfm'

// Assistant-reply Markdown rendering, structurally ported from Graphium's
// `buildMarkdownComponents` (`~/Graphium/src/features/ai-assistant/panel.tsx`
// 1282-1322): restrained spacing so a short chat reply doesn't read like a
// full document, GFM tables/strikethrough enabled. Styling is Asterism's own
// CSS classes (`ConsultDrawer.css` `.consult-md-*`), not Tailwind — user
// turns stay plain text (rendered directly in ConsultDrawer, no Markdown).

const components: Components = {
  h1: ({ children }) => <h1 className="consult-md-h1">{children}</h1>,
  h2: ({ children }) => <h2 className="consult-md-h2">{children}</h2>,
  h3: ({ children }) => <h3 className="consult-md-h3">{children}</h3>,
  h4: ({ children }) => <h4 className="consult-md-h3">{children}</h4>,
  h5: ({ children }) => <h5 className="consult-md-h3">{children}</h5>,
  h6: ({ children }) => <h6 className="consult-md-h3">{children}</h6>,
  p: ({ children }) => <p className="consult-md-p">{children}</p>,
  ul: ({ children }) => <ul className="consult-md-ul">{children}</ul>,
  ol: ({ children }) => <ol className="consult-md-ol">{children}</ol>,
  li: ({ children }) => <li>{children}</li>,
  strong: ({ children }) => <strong>{children}</strong>,
  em: ({ children }) => <em>{children}</em>,
  hr: () => <hr className="consult-md-hr" />,
  blockquote: ({ children }) => <blockquote className="consult-md-quote">{children}</blockquote>,
  code: ({ children, className }) => {
    // インラインコードのみここで装飾する。pre 内の code は pre 側に任せる。
    if (className) return <code className={className}>{children}</code>
    return <code className="consult-md-code">{children}</code>
  },
  pre: ({ children }) => <pre className="consult-md-pre">{children}</pre>,
  a: ({ children, href }) => (
    <a href={href} target="_blank" rel="noopener noreferrer" className="consult-md-link">
      {children}
    </a>
  ),
  table: ({ children }) => (
    <div className="consult-md-tablewrap">
      <table className="consult-md-table">{children}</table>
    </div>
  ),
  th: ({ children }) => <th className="consult-md-th">{children}</th>,
  td: ({ children }) => <td className="consult-md-td">{children}</td>,
}

export function ConsultMarkdown({ text }: { text: string }) {
  return (
    <ReactMarkdown remarkPlugins={[remarkGfm]} components={components}>
      {text}
    </ReactMarkdown>
  )
}
