# Phase 6b — Self-Hosted Registry with Harbor

> **Concepts introduced:** Harbor architecture, projects & RBAC, automatic Trivy scanning on push, vulnerability thresholds, retention policies, proxy cache, registry replication

---

## Concepts introduced

| Concept | What it is | Why it matters |
|---|---|---|
| **Harbor** | CNCF-graduated self-hosted OCI registry | Full control over where images are stored — required for air-gapped, on-prem, or regulated environments |
| **Project** | A namespace in Harbor that groups images and policies | Isolate teams, apply different scan thresholds and RBAC per project |
| **RBAC** | Role-based access control at the project level | Guest (pull only), Developer (push), Maintainer (manage), Project Admin — per project |
| **Auto-scan** | Harbor triggers Trivy automatically when an image is pushed | No separate CI scan step needed — the registry itself enforces the security gate |
| **Vulnerability threshold** | Block pulls of images above a configured severity level | An image with CRITICAL CVEs cannot be pulled — even if someone tries to use it |
| **Proxy cache** | Harbor as a pull-through cache for Docker Hub, GHCR, or ECR | Eliminates Docker Hub rate limits; mirrors dependencies into your own infrastructure |
| **Replication** | Automatically push images from Harbor to another registry | Harbor → GHCR, Harbor → ECR, or Harbor → Harbor for multi-region distribution |

---

## The problem

> *Nexio — three weeks after the enterprise deal closed.*
>
> The compliance review arrived. The customer's security team had a single question about the GHCR images Nexio was shipping: *"Where are these images stored, and who can access them?"*
>
> GHCR. GitHub's servers. In the US. Accessible to anyone with the right token.
>
> The customer was a French healthcare company. GDPR. Health data. Their legal team required that container images used to process patient data be stored in infrastructure they controlled — or at minimum, infrastructure with a verifiable data residency.
>
> GHCR was not an option.
>
> The platform team evaluated the options: AWS ECR (still US-based), Google Artifact Registry (configurable but third-party), self-hosted Harbor (full control, on-prem, zero external dependency).
>
> They chose Harbor. Three hours to install. That afternoon, images were flowing through an internal registry with built-in scanning, project-level RBAC, and a proxy cache that eliminated Docker Hub rate limits across the entire engineering org.

---

## Architecture

```
Harbor component stack (started by the install script)
────────────────────────────────────────────────────────────────────
  harbor-nginx       ← reverse proxy  :8080 → internal services
  harbor-portal      ← web UI (React)
  harbor-core        ← REST API, RBAC, webhook engine, policy enforcement
  harbor-db          ← PostgreSQL (projects, users, tags, policies)
  harbor-redis       ← job queue + session cache
  harbor-jobservice  ← async workers (replication, GC, retention, scan trigger)
  harbor-registry    ← OCI distribution (actual layer storage)
  harbor-registryctl ← registry controller (GC coordination)
  trivy-adapter      ← Trivy scanner, called by harbor-core on push


Push flow
────────────────────────────────────────────────────────────────────
  docker push localhost:8080/nexio/nexio-api:sha-a1b2c3d
  │
  ├── harbor-nginx routes → harbor-registry (stores layers)
  ├── harbor-core: validate auth, check project RBAC
  └── trivy-adapter: scan image automatically (if auto-scan on)
       ├── CLEAN    → image status: "No vulnerabilities"
       └── CRITICAL → image pull blocked (if threshold configured)


Proxy cache flow
────────────────────────────────────────────────────────────────────
  docker pull localhost:8080/dockerhub-proxy/library/python:3.12-slim
  │
  ├── Harbor checks local cache
  ├── MISS: pulls from Docker Hub → stores in Harbor → returns to client
  └── HIT:  serves from Harbor cache (no Docker Hub request, no rate limit)


Replication flow
────────────────────────────────────────────────────────────────────
  Push nexio-api:sha-a1b2c3d to Harbor
  └── replication rule fires (push-based, immediate)
      └── harbor-jobservice pushes to GHCR (or any OCI registry)
          ghcr.io/your-org/nexio-api:sha-a1b2c3d  ← external mirror
```

---

## Repository structure

```
phase-6b-harbor/
├── harbor/
│   └── harbor.yml     ← pre-configured for local HTTP dev
└── app/
    ├── Dockerfile     ← same as Phase 6
    ├── .dockerignore
    ├── app.py
    └── requirements.txt
```

---

## Prerequisites

- Docker and Docker Compose installed
- At least 4 GB of free RAM (Harbor runs ~9 containers)
- Ports 8080 available on your machine
- Internet access (the online installer pulls images from Docker Hub)

---

## Challenge 1 — Install and start Harbor

### Step 1: Download the Harbor online installer

Find the latest release at https://github.com/goharbor/harbor/releases. The online installer is ~800 KB — it pulls container images at install time.

```bash
# Set the version (check for the latest at the link above)
HARBOR_VERSION=v2.11.0

curl -LO https://github.com/goharbor/harbor/releases/download/${HARBOR_VERSION}/harbor-online-installer-${HARBOR_VERSION}.tgz

tar xzvf harbor-online-installer-${HARBOR_VERSION}.tgz
# Creates: harbor/
```

### Step 2: Use the pre-configured harbor.yml

```bash
cp phase-6b-harbor/harbor/harbor.yml harbor/harbor.yml
```

Review the key settings:

```bash
grep -E "^(hostname|http:|  port:|harbor_admin)" harbor/harbor.yml
```

```
hostname: localhost
http:
  port: 8080
harbor_admin_password: Harbor12345
```

### Step 3: Run the installer

```bash
cd harbor
sudo ./install.sh --with-trivy
```

`--with-trivy` enables the built-in vulnerability scanner. The installer:
1. Validates the configuration
2. Generates `docker-compose.yml` (do not edit this — it is generated)
3. Pulls the Harbor images from Docker Hub
4. Starts the stack

Expected final output:
```
✔ ----Harbor has been installed and started successfully.----
```

### Step 4: Verify all containers are running

```bash
docker compose ps
```

Expected (9 containers, all `Up`):
```
NAME                    STATUS
harbor-core             Up (healthy)
harbor-db               Up (healthy)
harbor-jobservice       Up (healthy)
harbor-log              Up
harbor-nginx            Up (healthy)
harbor-portal           Up (healthy)
harbor-redis            Up (healthy)
harbor-registry         Up (healthy)
harbor-registryctl      Up (healthy)
trivy-adapter           Up (healthy)
```

### Step 5: Open the Harbor UI

Navigate to **http://localhost:8080** in your browser.

- Username: `admin`
- Password: `Harbor12345`

You will see the Harbor dashboard with the default `library` project.

---

## Challenge 2 — Create a project and push an image

### Step 1: Create a project in the UI

1. Click **Projects → New Project**
2. Name: `nexio`
3. Access level: Private
4. Storage quota: `-1` (unlimited for this lab)
5. Click **OK**

Alternatively, use the Harbor API:

```bash
curl -s -u admin:Harbor12345 \
  -X POST http://localhost:8080/api/v2.0/projects \
  -H "Content-Type: application/json" \
  -d '{"project_name": "nexio", "public": false, "metadata": {"public": "false"}}'
```

### Step 2: Build the nexio-api image

```bash
SHORT_SHA=$(git rev-parse --short HEAD 2>/dev/null || echo "dev")

docker build \
  --build-arg APP_VERSION=$SHORT_SHA \
  --build-arg BUILD_DATE=$(date -u +%Y-%m-%dT%H:%M:%SZ) \
  -t localhost:8080/nexio/nexio-api:sha-$SHORT_SHA \
  phase-6b-harbor/app/
```

### Step 3: Log in to Harbor

```bash
docker login localhost:8080 -u admin -p Harbor12345
```

### Step 4: Push the image

```bash
docker push localhost:8080/nexio/nexio-api:sha-$SHORT_SHA
```

### Step 5: Verify in the UI

Go to **Projects → nexio → Repositories → nexio/nexio-api**. You will see the tag, its size, and shortly after — its vulnerability scan status.

### Step 6: Pull the image from Harbor

```bash
# Remove the local copy
docker rmi localhost:8080/nexio/nexio-api:sha-$SHORT_SHA

# Pull from Harbor
docker pull localhost:8080/nexio/nexio-api:sha-$SHORT_SHA
```

Harbor served it from its own storage — no Docker Hub involved.

---

## Challenge 3 — Automatic vulnerability scanning on push

Harbor's Trivy adapter scans every pushed image automatically. No CI step required.

### Step 1: Enable auto-scan at the project level

In the UI: **Projects → nexio → Configuration → Automatically scan images on push** → toggle ON → Save.

Via API:
```bash
curl -s -u admin:Harbor12345 \
  -X PUT http://localhost:8080/api/v2.0/projects/nexio \
  -H "Content-Type: application/json" \
  -d '{"metadata": {"auto_scan": "true"}}'
```

### Step 2: Push a new tag and watch the scan trigger

```bash
docker tag localhost:8080/nexio/nexio-api:sha-$SHORT_SHA \
           localhost:8080/nexio/nexio-api:latest
docker push localhost:8080/nexio/nexio-api:latest
```

In the UI, the tag immediately shows **Scanning** status. After 30–60 seconds it updates to:
- A green shield: **No vulnerabilities** (or a count of low/medium)
- A red shield: **X Critical** (if the base image has unpatched CVEs)

### Step 3: View the scan report

Click the tag name → **Scan Report**. You will see the same output Trivy produces in Phase 5, but rendered as a searchable table with CVE links, affected packages, and fix versions.

### Step 4: Configure a vulnerability threshold to block pulls

Set a project-level policy that blocks pulling images above a severity threshold.

In the UI: **Projects → nexio → Configuration → Prevent vulnerable images from running** → set threshold to **Critical** → Save.

Via API:
```bash
curl -s -u admin:Harbor12345 \
  -X PUT http://localhost:8080/api/v2.0/projects/nexio \
  -H "Content-Type: application/json" \
  -d '{"metadata": {"prevent_vul": "true", "severity": "critical"}}'
```

### Step 5: Simulate a blocked pull

Build and push an image with a vulnerable base:

```bash
cat <<'EOF' > /tmp/Dockerfile.vuln
FROM python:3.9
RUN pip install flask==2.0.0
COPY phase-6b-harbor/app/app.py /app/app.py
WORKDIR /app
CMD ["python", "app.py"]
EOF

docker build -t localhost:8080/nexio/nexio-api:vulnerable -f /tmp/Dockerfile.vuln .
docker push localhost:8080/nexio/nexio-api:vulnerable

# Wait for the scan to complete (~60s), then attempt to pull:
docker rmi localhost:8080/nexio/nexio-api:vulnerable
docker pull localhost:8080/nexio/nexio-api:vulnerable
```

Expected:
```
Error response from daemon: unknown: current image with 47 vulnerabilities
cannot be pulled due to configured prevention rules
```

Harbor blocked the pull at the registry level — no Kubernetes admission controller, no CI policy, no human intervention required. The image exists in Harbor but cannot be used.

---

## Challenge 4 — Retention policies

Without retention policies, every pushed tag accumulates forever. Harbor's retention engine runs on a schedule and deletes tags based on configurable rules.

### Step 1: Create a retention policy in the UI

1. Go to **Projects → nexio → Policy → Tag Retention**
2. Click **Add Rule**
3. Configure:
   - **Repositories matching:** `**` (all repositories)
   - **Tags matching:** `sha-**` (SHA-tagged images)
   - **Retain:** the most recently pushed **10** artifacts
4. Add a second rule:
   - **Tags matching:** `v**` (semver releases)
   - **Action:** Always retain (exclude from deletion)
5. Click **Save**

### Step 2: Run a dry run

Click **Run Now** → **Dry Run**. Harbor shows which tags *would* be deleted without actually deleting them. Review the output before scheduling real runs.

### Step 3: Set a schedule

1. Click **Edit** on the retention policy
2. Set **Schedule** to `Daily` at `00:00`
3. Save

Harbor will now automatically prune SHA tags beyond the last 10 every midnight, while always keeping semver releases.

### Step 4: Configure untagged artifact deletion via Garbage Collection

Retention handles tags. GC handles untagged manifests (orphaned layers after a tag is overwritten).

**System Admin → Garbage Collection → Set Schedule** → Weekly → Save.

GC runs weekly, removes unreferenced blobs, and reclaims storage.

---

## Challenge 5 — Configure Harbor as a proxy cache for Docker Hub

Docker Hub enforces rate limits: 100 pulls per 6 hours for anonymous users, 200 for authenticated. For a team of 40 engineers, this limit is hit daily. Harbor's proxy cache pulls from Docker Hub once and serves subsequent requests from its own storage.

### Step 1: Create a proxy cache endpoint

1. **System Admin → Registries → New Endpoint**
2. Provider: **Docker Hub**
3. Name: `dockerhub`
4. Endpoint URL: `https://hub.docker.com`
5. Access credentials: your Docker Hub username and password (optional — for authenticated pulls)
6. Click **Test Connection** → **OK**

### Step 2: Create a proxy cache project

1. **Projects → New Project**
2. Name: `dockerhub-proxy`
3. Toggle **Proxy Cache** ON
4. Registry: select `dockerhub`
5. Click **OK**

### Step 3: Pull through the proxy

Instead of `docker pull python:3.12-slim`, pull via Harbor:

```bash
docker pull localhost:8080/dockerhub-proxy/library/python:3.12-slim
```

First pull: Harbor fetches from Docker Hub and caches it. Logs show the upstream request.

```bash
# Pull again immediately
docker rmi localhost:8080/dockerhub-proxy/library/python:3.12-slim
docker pull localhost:8080/dockerhub-proxy/library/python:3.12-slim
```

Second pull: served from Harbor's local cache. No Docker Hub request. No rate limit consumed.

### Step 4: Verify what was cached

In the UI: **Projects → dockerhub-proxy → Repositories**. You will see `library/python` with the `3.12-slim` tag — stored in Harbor.

### Step 5: Update the Dockerfile to pull from the proxy

In production, point your Dockerfiles at the Harbor proxy instead of Docker Hub directly:

```dockerfile
# Before: FROM python:3.12-slim
# After:
FROM your-harbor.company.com/dockerhub-proxy/library/python:3.12-slim AS builder
...
FROM your-harbor.company.com/dockerhub-proxy/library/python:3.12-slim
```

All engineers and CI runners now pull through Harbor. Docker Hub rate limits are a solved problem.

---

## Challenge 6 — Set up replication: Harbor → GHCR

Replication lets Harbor push images to an external registry automatically. Common use cases: multi-region mirroring, disaster recovery, publishing to a public registry after passing internal quality gates.

### Step 1: Create a GHCR replication endpoint

1. **System Admin → Registries → New Endpoint**
2. Provider: **GitHub GHCR**
3. Name: `ghcr`
4. Endpoint URL: `https://ghcr.io`
5. Access ID: your GitHub username
6. Access Secret: a GitHub Personal Access Token with `write:packages` scope
7. Click **Test Connection** → **OK**

### Step 2: Create a replication rule

1. **System Admin → Replications → New Replication Rule**
2. Name: `nexio-to-ghcr`
3. Replication mode: **Push-based**
4. Source: `nexio/**` (all images in the nexio project)
5. Destination registry: `ghcr`
6. Destination namespace: `your-github-username`
7. Trigger: **Event Based** (triggers on push)
8. Override: checked (overwrite if tag exists at destination)
9. Click **Save**

### Step 3: Push an image and watch it replicate

```bash
docker push localhost:8080/nexio/nexio-api:sha-$SHORT_SHA
```

In the UI: **System Admin → Replications → nexio-to-ghcr → Executions**. You will see a replication job running. After ~30 seconds:

```bash
crane ls ghcr.io/your-username/nexio-api
# sha-a1b2c3d   ← replicated from Harbor automatically
```

The image is now in both Harbor and GHCR. Harbor is the source of truth; GHCR is the external mirror.

### Step 4: Verify replication with filtering

Add a filter to replicate only semver tags (not SHA tags):

1. Edit the replication rule
2. Source artifact filter: **Tag** → `v**`
3. Save

Now only `v1.0.0`, `v1.2.3` etc. are replicated. SHA tags stay internal.

---

## Command reference

| Command / Action | What it does |
|---|---|
| `sudo ./install.sh --with-trivy` | Install Harbor with the built-in Trivy scanner |
| `docker compose ps` (in harbor/) | Show Harbor container status |
| `docker compose down` (in harbor/) | Stop Harbor |
| `docker compose up -d` (in harbor/) | Restart Harbor |
| `docker login localhost:8080` | Authenticate to local Harbor |
| `curl -u admin:pass http://localhost:8080/api/v2.0/...` | Harbor REST API |
| Dry run (UI) | Preview retention policy deletions without acting |
| GC (UI) | Garbage collect unreferenced layers |

### Harbor REST API reference

```bash
# List projects
curl -s -u admin:Harbor12345 http://localhost:8080/api/v2.0/projects | jq '.[].name'

# List repositories in a project
curl -s -u admin:Harbor12345 \
  http://localhost:8080/api/v2.0/projects/nexio/repositories | jq '.[].name'

# List tags for a repository
curl -s -u admin:Harbor12345 \
  "http://localhost:8080/api/v2.0/projects/nexio/repositories/nexio-api/artifacts" \
  | jq '.[].tags[].name'

# Get vulnerability report for a tag
curl -s -u admin:Harbor12345 \
  "http://localhost:8080/api/v2.0/projects/nexio/repositories/nexio-api/artifacts/sha-${SHORT_SHA}/additions/vulnerabilities" \
  | jq '.["application/vnd.security.vulnerability.report; version=1.1"].summary'

# Manually trigger a scan
curl -s -u admin:Harbor12345 \
  -X POST \
  "http://localhost:8080/api/v2.0/projects/nexio/repositories/nexio-api/artifacts/sha-${SHORT_SHA}/scan"
```

---

## Production considerations

### 1. HTTPS is non-negotiable in production
The lab config uses HTTP for simplicity. In production, Harbor requires TLS — Docker will refuse to push to an HTTP registry unless it is explicitly listed as an `insecure-registry` in the daemon config (which is itself a security problem). Use a real certificate (Let's Encrypt via cert-manager, or an internal CA) and set the `https` block in `harbor.yml`. Never deploy HTTP Harbor outside of localhost.

### 2. Run Harbor on dedicated infrastructure
Harbor's database (PostgreSQL), job service, and registry storage should not share a VM with your application workloads. A common production topology: Harbor on a dedicated VM or a small Kubernetes cluster, with registry storage backed by an object store (S3, GCS, Azure Blob) instead of local disk. Configure `storage_service` in `harbor.yml` to point to object storage.

### 3. Back up the Harbor database
The Harbor PostgreSQL database stores all project metadata, users, RBAC assignments, replication rules, and scan results. The registry layers are stored separately (in `data_volume` or object storage). Back up both. A Harbor DB restore without the matching layer data (or vice versa) leaves you with broken references.

### 4. The proxy cache is not a CDN
The proxy cache stores pulled images and serves subsequent requests locally. It does not pre-warm — it only caches what has actually been requested. For build environments that need `python:3.12-slim` on the very first build after a cache flush, the first pull is still a Docker Hub request. Pre-pull critical base images after installing Harbor to warm the cache.

### 5. Replication lag is not zero
Event-based replication triggers quickly (seconds to minutes) but is not synchronous with the push. If a CI job pushes to Harbor and immediately tries to pull from GHCR (the replication target), the image may not be there yet. Add a health check in CI or use a scheduled replication rule with a pull-based strategy if strict consistency is required.

### 6. Use Harbor projects to enforce team boundaries
One project per team, with its own RBAC, scan thresholds, and retention policy. A developer on the payments team should not be able to push to the auth team's project. Project-level isolation in Harbor maps directly to namespace-level isolation in Kubernetes — design them in parallel.

---

## Outcome

A fully self-hosted OCI registry running locally. Images are pushed to Harbor, scanned by Trivy automatically on arrival, and blocked from being pulled if they exceed the configured CVE threshold — no CI step required, no human gate. Retention policies keep storage costs bounded. The proxy cache eliminates Docker Hub rate limits for the entire engineering team. Replication pushes verified, clean images to GHCR as a public mirror — Harbor is the internal gate, GHCR is the external distribution point.

---

[Back to Phase 6](../phase-6-registry/README.md) | [Next: Phase 7 — Runtime Security & Hardening →](../phase-7-runtime-security/README.md)
