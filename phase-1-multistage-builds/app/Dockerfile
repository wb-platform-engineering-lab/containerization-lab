# ─────────────────────────────────────────────────────────────────
# Phase 1 — multi-stage, optimized Dockerfile
#
# Stage 1 (builder): installs Python dependencies into an isolated
# prefix directory. This stage includes pip, build tools, and any
# compile-time packages — none of which belong in the final image.
#
# Stage 2 (runtime): starts from a clean slim base and copies only
# the installed packages and application source. No pip, no build
# tools, no cache files.
# ─────────────────────────────────────────────────────────────────

# Stage 1 — builder
FROM python:3.12-slim AS builder

WORKDIR /app

COPY requirements.txt .

# --no-cache-dir : don't write pip's HTTP cache to the layer
# --prefix=/install : isolates installed packages so Stage 2 can
#                     copy them cleanly without copying pip itself
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt


# Stage 2 — runtime
FROM python:3.12-slim

WORKDIR /app

# Bring in only the installed packages from the builder stage
COPY --from=builder /install /usr/local

# Bring in only the application source
COPY app.py .

EXPOSE 5000

CMD ["python", "app.py"]
