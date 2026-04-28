# syntax=docker/dockerfile:1
# ─────────────────────────────────────────────────────────────────
# The syntax directive MUST be the first line. It pins the BuildKit
# frontend version, unlocking RUN --mount and other extended syntax.
# ─────────────────────────────────────────────────────────────────

# Stage 1 — builder
FROM python:3.12-slim AS builder

ARG APP_VERSION=dev
ARG BUILD_DATE=unknown

WORKDIR /app

COPY requirements.txt .

# --mount=type=cache persists pip's HTTP cache between builds on the
# same host. The cache directory is never written into an image layer —
# it exists only on the BuildKit cache storage.
RUN --mount=type=cache,target=/root/.cache/pip \
    pip install --prefix=/install -r requirements.txt


# Stage 2 — runtime
FROM python:3.12-slim

# ARGs must be redeclared after each FROM — they do not cross stage boundaries
ARG APP_VERSION=dev
ARG BUILD_DATE=unknown

LABEL org.opencontainers.image.title="nexio-api" \
      org.opencontainers.image.description="Nexio real-time event processing API" \
      org.opencontainers.image.version="${APP_VERSION}" \
      org.opencontainers.image.created="${BUILD_DATE}" \
      org.opencontainers.image.source="https://github.com/wb-platform-engineering-lab/containerization-lab" \
      org.opencontainers.image.licenses="MIT"

WORKDIR /app

COPY --from=builder /install /usr/local
COPY app.py .

RUN addgroup --system nexio \
    && adduser --system --ingroup nexio --no-create-home nexio \
    && chown -R nexio:nexio /app

USER nexio

EXPOSE 5000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:5000/health')" \
  || exit 1

CMD ["python", "app.py"]
