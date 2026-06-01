import type { ComponentPropsWithoutRef } from 'react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { Mermaid } from './Mermaid'

/**
 * Render the LLM proposal Markdown, swapping ```mermaid fenced blocks for
 * live-rendered diagrams. Everything else (tables, headings, code) renders as
 * normal GFM Markdown.
 */
export function ProposalView({ markdown }: { markdown: string }) {
  return (
    <ReactMarkdown
      remarkPlugins={[remarkGfm]}
      components={{
        code({ className, children, ...props }: ComponentPropsWithoutRef<'code'>) {
          const text = String(children ?? '')
          if (className?.includes('language-mermaid')) {
            return <Mermaid chart={text.replace(/\n$/, '')} />
          }
          return (
            <code className={className} {...props}>
              {children}
            </code>
          )
        },
      }}
    >
      {markdown}
    </ReactMarkdown>
  )
}
