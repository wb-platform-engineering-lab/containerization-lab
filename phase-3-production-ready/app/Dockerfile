# Stage 1 — builder
FROM python:3.12-slim AS builder

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt


# Stage 2 — runtime
FROM python:3.12-slim

# OCI standard image labels — traceability from image back to source
LABEL org.opencontainers.image.title="nexio-api" \
      org.opencontainers.image.description="Nexio real-time event processing API" \
      org.opencontainers.image.version="0.1.0" \
      org.opencontainers.image.source="https://github.com/wb-platform-engineering-lab/containerization-lab" \
      org.opencontainers.image.licenses="MIT"

WORKDIR /app

COPY --from=builder /install /usr/local
COPY app.py .

# Non-root user — create group and user, then transfer ownership of /app
RUN addgroup --system nexio \
    && adduser --system --ingroup nexio --no-create-home nexio \
    && chown -R nexio:nexio /app

USER nexio

EXPOSE 5000

# HEALTHCHECK — Docker polls this command and marks the container
# healthy/unhealthy. Compose's depends_on (condition: service_healthy)
# waits for this check to pass before starting dependent services.
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:5000/health')" \
  || exit 1

CMD ["python", "app.py"]
