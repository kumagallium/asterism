// Thin client for the csv2rdf-api surface. M0b only needs /api/inspect.

/**
 * POST the given CSV files to /api/inspect and return the inspection Markdown.
 * `fks` are optional foreign-key hint columns (e.g. ["SID"]).
 */
export async function inspectCsvs(files: File[], fks: string[]): Promise<string> {
  const form = new FormData()
  for (const file of files) {
    form.append('files', file)
  }
  const params = new URLSearchParams()
  for (const fk of fks) {
    params.append('fk', fk)
  }
  const query = params.toString()
  const url = query ? `/api/inspect?${query}` : '/api/inspect'

  const res = await fetch(url, { method: 'POST', body: form })
  if (!res.ok) {
    const detail = await res.text().catch(() => '')
    throw new Error(`inspect failed (HTTP ${res.status})${detail ? `: ${detail}` : ''}`)
  }
  return res.text()
}
