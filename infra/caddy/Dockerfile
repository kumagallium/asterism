# Asterism production front: build the React SPA, then bake it into a Caddy image
# that terminates TLS, gates the site, and reverse-proxies the backends.
#
# Build context MUST be the repo root (the ui/ sources live there):
#   docker build -f infra/caddy/Dockerfile -t asterism-caddy .
# In the prod stack this is driven by compose.prod.yaml (service: caddy).

# ---- stage 1: build the SPA -------------------------------------------------
# vite 8 / TS 6 require a recent Node. Install from the lockfile first so the
# dependency layer is cached independently of source edits.
FROM node:22-alpine AS ui-build
WORKDIR /ui
COPY ui/package.json ui/package-lock.json ./
RUN npm ci
COPY ui/ ./
# tsc -b && vite build -> /ui/dist (the SPA is served at the site root, base "/").
RUN npm run build

# ---- stage 2: the runtime front ---------------------------------------------
FROM caddy:2-alpine
COPY infra/caddy/Caddyfile /etc/caddy/Caddyfile
COPY --from=ui-build /ui/dist /srv
