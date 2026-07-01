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
# Prefer the reproducible `npm ci`; fall back to `npm install` when the committed
# lockfile drifts from package.json (e.g. platform-specific optional deps like
# @emnapi/* that npm prunes per-platform). `npm install` reconciles in-container
# on linux/amd64. Pre-existing lockfile drift on main is worth fixing separately.
RUN npm ci || npm install
COPY ui/ ./
# Production build talks to the REAL backends:
#   VITE_DEMO_MODE=live      -> Ask/demo uses the live demo-agent, not mock fixtures.
#   VITE_DEMO_AGENT_URL="/"   -> the code trims trailing slashes, so "/" becomes ""
#                               => relative /demo/* (caddy proxies it to the demo-agent,
#                               same origin). Works for ANY domain without a rebuild
#                               (vs the dev default http://localhost:8090). "/" (not "")
#                               so vite reliably exposes the non-empty value.
ENV VITE_DEMO_MODE=live
ENV VITE_DEMO_AGENT_URL="/"
# tsc -b && vite build -> /ui/dist (the SPA is served at the site root, base "/").
RUN npm run build

# ---- stage 2: the runtime front ---------------------------------------------
FROM caddy:2-alpine
COPY infra/caddy/Caddyfile /etc/caddy/Caddyfile
COPY --from=ui-build /ui/dist /srv
