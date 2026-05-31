import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// https://vite.dev/config/
// In dev, proxy /api/* to the FastAPI app (csv2rdf-api) so the browser talks to
// a single origin. In prod the SPA is served by FastAPI (M0c), so no proxy.
export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      '/api': 'http://localhost:8080',
    },
  },
})
