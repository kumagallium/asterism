import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// https://vite.dev/config/
// In dev, proxy /api/* to the FastAPI app (asterism-api) so the browser talks to
// a single origin. In prod the SPA is served by FastAPI (M0c), so no proxy.
export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      // 127.0.0.1 (not localhost): on macOS `localhost` may resolve to IPv6
      // ::1 while the API listens on IPv4, which silently breaks the proxy.
      '/api': 'http://127.0.0.1:8080',
      // /jobs is the ingest-history endpoint (no /api prefix). Proxy it too so
      // the Jobs view reaches the API in dev.
      '/jobs': 'http://127.0.0.1:8080',
    },
  },
})
