// POST /api/design/consult (ADR design-consult-chat.md D3): a stateless,
// tool-free, non-streaming LLM turn for the design-consult drawer. Mirrors
// `demoApi.ask()`'s fetch shape but carries no history server-side — the
// caller passes the whole transcript every time.

import { llmHeaders, type LlmCredentials } from '../settings/store'
import type { ConsultContextState } from './consultContext'

export interface ConsultMessage {
  role: 'user' | 'assistant'
  content: string
}

/** Ask the design-consult chat one turn. `context` (D4) is sent as-is — the
 *  server treats every field as optional. `signal` aborts the wait (the stop
 *  button); the server's own call is not cancelled, the caller just stops
 *  listening. */
export async function consult(
  messages: ConsultMessage[],
  creds: LlmCredentials | null,
  context?: ConsultContextState | null,
  signal?: AbortSignal,
): Promise<string> {
  const res = await fetch('/api/design/consult', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', ...llmHeaders(creds) },
    body: JSON.stringify({
      messages,
      context: context
        ? {
            step: context.step || undefined,
            dataset: context.dataset || undefined,
            skeleton_summary: context.skeletonSummary || undefined,
            focus_column: context.focusColumn
              ? { name: context.focusColumn.name, samples: context.focusColumn.samples }
              : undefined,
            pending_columns: context.pendingColumns?.length
              ? context.pendingColumns.map((c) => ({ name: c.name, samples: c.samples }))
              : undefined,
            columns: context.columns?.length
              ? context.columns.map((c) => ({
                  name: c.name,
                  meaning: c.meaning,
                  unit: c.unit,
                  samples: c.samples,
                }))
              : undefined,
            kinds: context.kinds?.length
              ? context.kinds.map((k) => ({
                  map: k.map,
                  source: k.source,
                  key_columns: k.keyColumns,
                  kind_name: k.kindName,
                  columns: k.columns,
                  column_values: k.columnValues,
                }))
              : undefined,
          }
        : undefined,
    }),
    signal,
  })
  if (!res.ok) {
    const detail = await res.text().catch(() => '')
    throw new Error(`consult failed (HTTP ${res.status})${detail ? `: ${detail}` : ''}`)
  }
  const data = (await res.json()) as { reply?: unknown }
  return typeof data.reply === 'string' ? data.reply : ''
}
