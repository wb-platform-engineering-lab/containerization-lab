# syntax=docker/dockerfile:1

# Stage 1 — builder
FROM python:3.12-slim AS builder

WORKDIR /app

COPY requirements.txt .
RUN --mount=type=cache,target=/root/.cache/pip \
    pip install --prefix=/install -r requirements.txt


# Stage 2 — runtime
FROM python:3.12-slim

LABEL org.opencontainers.image.title="nexio-api" \
      org.opencontainers.image.source="https://github.com/wb-platform-engineering-lab/containerization-lab"

WORKDIR /app

COPY --from=builder /install /usr/local
COPY app.py .

RUN addgroup --system nexio \
    && adduser --system --ingroup nexio --no-create-home nexio \
    && chown -R nexio:nexio /app

USER nexio

EXPOSE 5000

# Liveness probe: is the process up?
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:5000/health/live')" \
  || exit 1

CMD ["python", "app.py"]
