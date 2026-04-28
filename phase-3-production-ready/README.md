# Phase 3 — Production-Ready Images

> **Concepts introduced:** Non-root user, `HEALTHCHECK`, OCI image labels, CVE scanning with Trivy, distroless base images

---

## Concepts introduced

| Concept | What it is | Why it matters |
|---|---|---|
| **Non-root user** | Running the container process as a non-root UID | A container running as root that escapes its sandbox has full host access |
| **`HEALTHCHECK`** | A command Docker polls to determine container health | Enables `depends_on: service_healthy` in Compose; visible in `docker ps` |
| **OCI image labels** | Standardised metadata keys on the image manifest | Traceability — link any running container back to its source commit and build |
| **CVE scanning** | Matching image packages against a vulnerability database | Catches known vulnerabilities before they reach production |
| **Distroless** | Base images with no shell, no package manager, no OS utilities | Minimal attack surface — nothing to exploit beyond the app itself |

---

## The problem

> *Nexio — 10 engineers. Three months after Phase 2.*
>
> The platform team ran their first security scan. The results came back the next morning.
>
> ```
> nexio-api:0.1 — 47 vulnerabilities found
>   CRITICAL: 3
>   HIGH:     12
>   MEDIUM:   22
>   LOW:      10
> ```
>
> 47 CVEs. Three of them critical.
>
> None were in Flask or the application code. They were all in system packages inside `python:3.12-slim` — packages the app never called at runtime. `libssl`, `libc`, `libexpat`. Shipping them was unnecessary — and the security team's weekly scan would now flag every build until they were gone.
>
> A second finding: every container ran as root.
>
> *"If someone achieves RCE in one of our containers, they have root on the host. That's not acceptable."*
>
> The infosec team put a one-week hold on the next release. The platform team spent the week making images production-ready.

---

## Architecture

```
Phase 2 image
───────────────────────────────────────────────────
  Base: python:3.12-slim
  User: root (UID 0)
  Health: none (Docker doesn't know if the app works)
  Labels: none (no traceability to source commit)
  CVEs: 47 (packages in base image never used at runtime)


Phase 3 image
───────────────────────────────────────────────────
  Base: python:3.12-slim           ← same base, packages updated
  User: nexio (UID > 1000)         ← non-root
  Health: HEALTHCHECK every 30s   ← Docker monitors liveness
  Labels: OCI standard metadata   ← traceable to commit + timestamp
  CVEs: 0 critical/high (verified with Trivy after scan)

                     ┌──────────────────────────────┐
                     │  Dockerfile diff (additions)  │
                     │                               │
                     │  LABEL org.opencontainers...  │
                     │  RUN adduser nexio            │
                     │  USER nexio                   │
                     │  HEALTHCHECK --interval=30s   │
                     └──────────────────────────────┘
```

---

## Repository structure

```
phase-3-production-ready/
└── app/
    ├── Dockerfile        ← hardened: labels, non-root user, HEALTHCHECK
    ├── .dockerignore
    ├── app.py            ← same Flask API as Phase 1
    └── requirements.txt
```

The application code does not change. Everything in this phase is packaging.

---

## Challenge 1 — Scan the Phase 1 image with Trivy

Before hardening, establish what you are fixing.

### Step 1: Install Trivy

```bash
# macOS
brew install trivy

# Linux (apt)
sudo apt-get install trivy

# Or pull the Docker image directly (no install required)
docker pull aquasec/trivy:latest
```

### Step 2: Build the Phase 1 image (if not already present)

```bash
docker build -t nexio-api:phase1 phase-1-multistage-builds/app/
```

### Step 3: Run the vulnerability scan

```bash
trivy image nexio-api:phase1
```

Or without installing Trivy:

```bash
docker run --rm -v /var/run/docker.sock:/var/run/docker.sock \
  aquasec/trivy image nexio-api:phase1
```

Expected output (truncated):
```
nexio-api:phase1 (debian 12.x)
================================
Total: 47 (UNKNOWN: 0, LOW: 10, MEDIUM: 22, HIGH: 12, CRITICAL: 3)

CRITICAL: libssl3 - CVE-2024-xxxxx
CRITICAL: libc-bin - CVE-2024-xxxxx
...
```

### Step 4: Understand what Trivy is scanning

Trivy reads the image manifest, extracts the OS package list and language-level package list (pip packages in `site-packages/`), and cross-references them against the CVE database. The critical findings here are all OS packages — not Flask or the application code.

> **Why does `python:3.12-slim` still have CVEs?** `slim` removes optional packages but retains the core Debian OS. CVEs are regularly discovered in `glibc`, `openssl`, and other core libraries. The fix is to either update to a newer base image (which includes patched packages) or switch to a distroless base that removes these packages entirely (Challenge 6).

---

## Challenge 2 — Add OCI image labels

Labels bake metadata into the image at build time. When you inspect a container running in production — or a container that has been running for three months — the labels tell you exactly where the image came from.

### Step 1: Review the Dockerfile labels

```bash
cat phase-3-production-ready/app/Dockerfile
```

```dockerfile
LABEL org.opencontainers.image.title="nexio-api" \
      org.opencontainers.image.description="Nexio real-time event processing API" \
      org.opencontainers.image.version="0.1.0" \
      org.opencontainers.image.source="https://github.com/..." \
      org.opencontainers.image.licenses="MIT"
```

The `org.opencontainers.image.*` keys are the [OCI Image Spec](https://specs.opencontainers.org/image-spec/annotations/) standard. Any tooling that understands OCI (Kubernetes, container registries, scanners, audit tools) can read these keys without configuration.

### Step 2: Build and inspect the labels

```bash
docker build -t nexio-api:0.2 phase-3-production-ready/app/
docker inspect nexio-api:0.2 | jq '.[0].Config.Labels'
```

Expected:
```json
{
  "org.opencontainers.image.description": "Nexio real-time event processing API",
  "org.opencontainers.image.licenses": "MIT",
  "org.opencontainers.image.source": "https://github.com/...",
  "org.opencontainers.image.title": "nexio-api",
  "org.opencontainers.image.version": "0.1.0"
}
```

> **In CI, pass `version` and `created` as build args.** Baking a static version into the Dockerfile means every commit produces an identically-labelled image. In Phase 4, we pass `APP_VERSION` and `BUILD_DATE` as `--build-arg` so each build carries the exact commit SHA and timestamp.

---

## Challenge 3 — Run as a non-root user

By default, Docker containers run as root (UID 0). A process running as root inside a container has full root permissions within that container's filesystem — and in many kernel exploit scenarios, that means full root on the host.

### Step 1: Verify the Phase 1 image runs as root

```bash
docker run --rm nexio-api:phase1 whoami
# root
```

### Step 2: Review the user creation in the Dockerfile

```bash
cat phase-3-production-ready/app/Dockerfile
```

The relevant lines:

```dockerfile
RUN addgroup --system nexio \
    && adduser --system --ingroup nexio --no-create-home nexio \
    && chown -R nexio:nexio /app

USER nexio
```

| Flag | Meaning |
|---|---|
| `--system` | Creates a system account (no shell, no home directory, UID in the system range) |
| `--ingroup nexio` | Assigns the user to the group created in the same command |
| `--no-create-home` | No `/home/nexio` directory — nothing writes to home at runtime |
| `chown -R nexio:nexio /app` | Transfers ownership before the `USER` switch — otherwise the new user can't read its own files |
| `USER nexio` | All subsequent instructions and the final CMD run as this user |

### Step 3: Build and verify

```bash
docker build -t nexio-api:0.2 phase-3-production-ready/app/

docker run --rm nexio-api:0.2 whoami
# nexio

docker run --rm nexio-api:0.2 id
# uid=999(nexio) gid=999(nexio) groups=999(nexio)
```

### Step 4: Verify the app still works

```bash
docker run -d --name nexio -p 5000:5000 nexio-api:0.2
curl http://localhost:5000/health
# {"service": "nexio-api", "status": "healthy"}
docker rm -f nexio
```

Port 5000 is above 1024, so a non-root process can bind to it without elevated privileges.

> **Why can't a non-root user bind to port 80?** On Linux, ports below 1024 are "privileged ports" — only root (or a process with `CAP_NET_BIND_SERVICE`) can bind to them. If your app listens on port 80, either run a reverse proxy in front of it (nginx on 80 → app on 8080) or grant the specific capability: `--cap-add NET_BIND_SERVICE`. Never run the whole container as root for this reason.

---

## Challenge 4 — Add a HEALTHCHECK instruction

`HEALTHCHECK` tells Docker how to determine if the application inside the container is actually working — not just that the process is running, but that it's responding to requests.

### Step 1: Review the HEALTHCHECK in the Dockerfile

```dockerfile
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:5000/health')" \
  || exit 1
```

| Parameter | Value | Meaning |
|---|---|---|
| `--interval` | `30s` | Run the check every 30 seconds |
| `--timeout` | `5s` | If the check takes longer than 5s, treat as failure |
| `--start-period` | `10s` | Grace period after startup — failures during this window don't count |
| `--retries` | `3` | Mark unhealthy only after 3 consecutive failures |

The check hits the `/health` endpoint using Python's stdlib `urllib` — no external tool required inside the container.

### Step 2: Build and observe health status

```bash
docker build -t nexio-api:0.2 phase-3-production-ready/app/
docker run -d --name nexio -p 5000:5000 nexio-api:0.2

# Watch the health status transition
watch docker ps
```

After `--start-period` (10s) + first `--interval` (30s), you will see `STATUS` change from `Up (health: starting)` to `Up (healthy)`.

### Step 3: Simulate an unhealthy container

```bash
# Kill the Flask process inside the container without stopping the container itself
docker exec nexio pkill -f "python app.py"

# Watch it transition to unhealthy
watch docker ps
# After 3 failed checks: Up (unhealthy)
```

`docker ps` now shows `(unhealthy)`. An orchestrator (Compose with `depends_on`, Kubernetes) uses this signal to decide whether to route traffic to or restart the container.

```bash
docker rm -f nexio
```

---

## Challenge 5 — Scan the hardened image

### Step 1: Scan the Phase 3 image

```bash
trivy image nexio-api:0.2
```

### Step 2: Filter to critical and high severity only

```bash
trivy image --severity CRITICAL,HIGH nexio-api:0.2
```

Expected:
```
Total: 0 (HIGH: 0, CRITICAL: 0)
```

If any CRITICAL or HIGH CVEs remain, they are likely in the `python:3.12-slim` base itself. Update the base image to the latest digest:

```bash
docker pull python:3.12-slim
docker build --no-cache -t nexio-api:0.2 phase-3-production-ready/app/
trivy image --severity CRITICAL,HIGH nexio-api:0.2
```

### Step 3: Check for secrets accidentally baked into the image

Trivy also scans for exposed secrets (API keys, tokens, private keys) embedded in image layers:

```bash
trivy image --scanners secret nexio-api:0.2
```

This is a safeguard against the common mistake of accidentally `COPY`ing a `.env` file or credentials file into the image. A `.dockerignore` (Phase 1) is the preventive measure; this scan is the safety net.

---

## Challenge 6 — (Advanced) Explore distroless base images

Distroless images (from `gcr.io/distroless`) contain only the language runtime and its dependencies — no shell, no package manager, no system utilities. The attack surface is minimal: there is nothing to exploit beyond the application itself.

### Step 1: Build a distroless variant

Create a one-off Dockerfile to test:

```bash
cat <<'EOF' > /tmp/Dockerfile.distroless
FROM python:3.12-slim AS builder
WORKDIR /app
COPY phase-3-production-ready/app/requirements.txt .
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt

FROM gcr.io/distroless/python3-debian12
WORKDIR /app
COPY --from=builder /install/lib/python3.12/site-packages /usr/lib/python3/dist-packages
COPY phase-3-production-ready/app/app.py .
CMD ["app.py"]
EOF

docker build -t nexio-api:distroless -f /tmp/Dockerfile.distroless .
```

### Step 2: Compare

```bash
docker images nexio-api
```

```
REPOSITORY   TAG           SIZE
nexio-api    0.2           125MB    ← python:3.12-slim
nexio-api    distroless    55MB     ← distroless
```

### Step 3: Verify there is no shell

```bash
docker run --rm nexio-api:distroless sh
# docker: Error response from daemon: failed to create task for container:
# failed to create shim task: OCI runtime exec failed: exec: "sh": executable file not found in $PATH
```

No shell. No `exec` for debugging. This is the trade-off: minimal attack surface at the cost of runtime debuggability. In production this is often the right choice — debugging happens with log shipping and remote profilers, not `docker exec`.

> **When distroless is not the right choice:** If your app dynamically loads shared libraries at runtime, or if you rely on `docker exec` for operational tasks, distroless will cause problems. Evaluate the trade-off deliberately.

---

## Command reference

| Command | What it does |
|---|---|
| `trivy image name:tag` | Scan an image for CVEs |
| `trivy image --severity CRITICAL,HIGH name:tag` | Filter to high/critical only |
| `trivy image --scanners secret name:tag` | Scan for exposed secrets |
| `docker inspect name:tag \| jq '.[0].Config.Labels'` | Read OCI labels |
| `docker run --rm name:tag whoami` | Check which user the container runs as |
| `docker run --rm name:tag id` | Check UID/GID of the container process |

---

## Production considerations

### 1. Automate scanning in CI — fail on CRITICAL
A scan you run manually once is not a security control. Every image build in CI should run `trivy image --severity CRITICAL,HIGH --exit-code 1`. A non-zero exit code fails the pipeline. This is covered in Phase 5 with a GitHub Actions workflow.

### 2. Never run as root — enforce it at the platform level
In Kubernetes, `PodSecurityAdmission` (or a policy tool like Kyverno) can block pods that run as root, even if the Dockerfile specifies `USER root`. Defence in depth: the Dockerfile enforces non-root, and the cluster also enforces it independently.

### 3. Set `USER` before `HEALTHCHECK`
`HEALTHCHECK CMD` runs as whatever user is active when the `HEALTHCHECK` instruction is evaluated. If you define `HEALTHCHECK` before `USER`, it runs as root. Define `HEALTHCHECK` after `USER nexio` to ensure it runs as the app user — this also validates that the app user has the permissions needed for the health check command.

### 4. OCI labels are static — use build args for dynamic values
The `version` and `created` labels in this phase are hardcoded. In a real pipeline, pass them as `--build-arg APP_VERSION=$(git rev-parse --short HEAD)` and `--build-arg BUILD_DATE=$(date -u +%Y-%m-%dT%H:%M:%SZ)`. This is addressed in Phase 4.

### 5. Update base images on a schedule, not ad hoc
New CVEs are published continuously. A clean scan today does not mean a clean scan next week. Use Renovate or Dependabot to open PRs when a new `python:3.12-slim` digest is published. Treat base image updates as dependency updates — reviewed, tested, and merged regularly.

---

## Outcome

The image runs as a non-root user, reports its own health status to Docker, carries OCI-standard metadata traceable to its source, and scans clean for CRITICAL and HIGH CVEs. It is now fit for a production security audit.

The build metadata (version, build date) is still static. That is addressed in Phase 4.

---

[Back to Phase 2](../phase-2-compose/README.md) | [Next: Phase 4 — BuildKit & Advanced Build Patterns →](../phase-4-buildkit/README.md)
