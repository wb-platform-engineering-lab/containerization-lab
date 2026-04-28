# ─────────────────────────────────────────────────────────────────
# Phase 0 — a basic, working Dockerfile.
#
# This is intentionally a single-stage build using the full Python
# image. It works — and that's the point of Phase 0. In Phase 1
# we will measure the cost of these choices and fix them with
# multi-stage builds and a .dockerignore file.
# ─────────────────────────────────────────────────────────────────

FROM python:3.12

# Set a working directory inside the container
WORKDIR /app

# Copy the dependency list and install it
COPY requirements.txt .
RUN pip install -r requirements.txt

# Copy the application source code
COPY app.py .

# Tell Docker which port the app listens on (documentation only —
# does NOT publish the port on the host)
EXPOSE 5000

# The command to run when the container starts
CMD ["python", "app.py"]
