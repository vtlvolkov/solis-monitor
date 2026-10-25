# --- build the frontend (unit tests must pass) ---------------------------------
FROM --platform=$BUILDPLATFORM node:22-alpine AS frontend
WORKDIR /src
COPY frontend/package.json frontend/package-lock.json ./
# Retry slow downloads and keep npm's cache between builds (slow links / small hosts).
RUN --mount=type=cache,target=/root/.npm \
    npm config set fetch-retries 5 && \
    npm config set fetch-retry-mintimeout 20000 && \
    npm config set fetch-retry-maxtimeout 120000 && \
    npm ci --no-audit --no-fund --prefer-offline
COPY frontend/ ./
RUN npm test && npm run build

# --- backend tests: the image only builds if they pass ------------------------------
FROM --platform=$BUILDPLATFORM python:3.12-slim AS backend-test
WORKDIR /app
COPY backend/requirements.txt backend/requirements-dev.txt ./
RUN pip install --no-cache-dir -r requirements-dev.txt
COPY backend/ ./
RUN python -m pytest -q -p no:cacheprovider && touch /tested

# --- runtime: FastAPI serves the API and the built files -----------------------
FROM python:3.12-slim
WORKDIR /app
COPY backend/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY backend/app ./app
COPY --from=frontend /src/dist ./static
COPY --from=backend-test /tested /tmp/tested
ENV PYTHONUNBUFFERED=1 STATIC_DIR=/app/static
EXPOSE 8088
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8088/healthz', timeout=4).status == 200 else 1)"
CMD ["uvicorn", "app.main:app_factory", "--factory", "--host", "0.0.0.0", "--port", "8088", "--proxy-headers", "--no-access-log"]
