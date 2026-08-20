import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// https://vite.dev/config/
// In dev, proxy /api/* to the FastAPI app (asterism-api) so the browser talks to
// a single origin. In prod the SPA is served by FastAPI (M0c), so no proxy.
//
// The target is overridable via VITE_API_PROXY so you can point the dev UI at a
// local api on any port (e.g. a substrate-enabled api on :8085) without editing
// this file. 127.0.0.1 (not localhost): on macOS `localhost` may resolve to IPv6
// ::1 while the API listens on IPv4, which silently breaks the proxy.
const API_TARGET = process.env.VITE_API_PROXY ?? 'http://127.0.0.1:8080'

export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      '/api': API_TARGET,
      // /jobs is the ingest-history endpoint (no /api prefix). Proxy it too so
      // the Jobs view reaches the API in dev.
      '/jobs': API_TARGET,
      // /describe is where a citation ID lands (the shared, human-facing page
      // the API renders). Without this the dev server answers with the SPA
      // shell, so a citation link looks broken only in dev.
      '/describe': API_TARGET,
      // /demo/* is the Ask agent's surface. In production caddy reverse-proxies
      // it, and `asterism-local` relays it in-process, so a live-mode build asks
      // its OWN origin for it (VITE_DEMO_AGENT_URL=/). Without this line the dev
      // server answers those calls with the SPA shell — HTML where JSON is
      // expected, so the schema comes back null and Ask looks empty, only in dev
      // (2026-08-20: the dev stack was serving demo fixtures for exactly this
      // reason, one layer up).
      '/demo': API_TARGET,
    },
  },
})
