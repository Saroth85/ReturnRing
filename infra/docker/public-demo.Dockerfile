# One CPU web service for Railway or another Docker host. Build from the repository root.
# Runtime secrets, the serving snapshot and database are supplied separately, never in layers.
FROM node:22.22.0-bookworm-slim AS ui
WORKDIR /ui
COPY apps/ui/package.json apps/ui/package-lock.json apps/ui/.npmrc ./
RUN npm ci --no-audit --no-fund
COPY apps/ui/ ./
ENV VITE_API_MODE=live
ENV VITE_READ_ONLY=true
RUN npm run build

FROM python:3.12-slim
WORKDIR /app
COPY requirements/base.txt requirements/public-demo.txt requirements/
RUN pip install --no-cache-dir -r requirements/public-demo.txt \
    && useradd --create-home app \
    && mkdir -p /app/state \
    && chown app:app /app/state
COPY policy policy
COPY llm llm
COPY contracts contracts
COPY pipeline pipeline
COPY ml ml
COPY apps apps
COPY scripts scripts
COPY --from=ui /ui/dist /app/public-ui
ENV PUBLIC_DEMO_UI_DIR=/app/public-ui
ENV PUBLIC_READ_ONLY=true
ENV DEVICE=cpu
ENV LLM_MODE=tokenfactory
ENV DEMO_MODE=live
ENV LLM_CONCURRENCY=1
ENV VLLM_TIMEOUT_S=90
USER app
EXPOSE 8080
CMD ["python", "-m", "scripts.public_demo_bootstrap"]
