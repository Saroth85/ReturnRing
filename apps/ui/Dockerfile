# ReturnRing UI: build the static bundle with Node, then serve it with nginx.
# Build context: apps/ui (infra/docker-compose.yml). The bundle is built in live mode: it calls
# /api, /healthz and /readyz on its own origin, which the Caddy proxy routes to the API
# (infra/Caddyfile). No contract example is bundled (scripts/check-bundle.mjs fails the build
# otherwise), and src/api/*.gen.ts are committed, so the build needs nothing outside apps/ui.

FROM node:22.22.0-bookworm-slim AS build
WORKDIR /app
COPY package.json package-lock.json .npmrc ./
RUN npm ci --no-audit --no-fund
COPY . .
ENV VITE_API_MODE=live
RUN npm run build

FROM nginx:1.27-alpine
COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/dist /usr/share/nginx/html
EXPOSE 80
