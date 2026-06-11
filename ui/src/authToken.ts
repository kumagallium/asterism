// Shared write-auth token for the mutating + raw-SPARQL api routes.
//
// The backend gates those routes fail-closed: when the operator sets
// ASTERISM_API_TOKEN server-side, a request without a matching token is rejected
// (503/401). Configure the matching token here so the workbench can drive writes:
//   - build-time:  VITE_API_TOKEN
//   - at runtime:  sessionStorage['asterism.apiToken'] (e.g. set from a settings field)
// When no token is configured this is a no-op (read-only catalog still works, and
// a deployment with no server-side token keeps writes fail-closed).

const STORAGE_KEY = 'asterism.apiToken'

export function getApiToken(): string {
  const fromEnv = (import.meta.env.VITE_API_TOKEN as string | undefined) ?? ''
  if (fromEnv) return fromEnv
  try {
    return sessionStorage.getItem(STORAGE_KEY) ?? ''
  } catch {
    return ''
  }
}

export function setApiToken(token: string): void {
  try {
    if (token) sessionStorage.setItem(STORAGE_KEY, token)
    else sessionStorage.removeItem(STORAGE_KEY)
  } catch {
    /* ignore storage failures (e.g. private mode) */
  }
}

/** Headers carrying the write-auth token, or `{}` when none is configured. */
export function authHeaders(): Record<string, string> {
  const token = getApiToken()
  return token ? { 'X-Asterism-Token': token } : {}
}
