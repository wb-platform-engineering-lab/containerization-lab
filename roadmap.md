# Roadmap

## Phase 0 — Your First Container
**Nexio problem:** The app only runs on one laptop.  
**What you learn:** `FROM`, `WORKDIR`, `COPY`, `RUN`, `EXPOSE`, `CMD`. `docker build`, `docker run`, `docker exec`, `docker logs`. Port mapping, environment variables.  
**Intentional limitations:** Single-stage build, full `python:3.12` image, runs as root. These are addressed in later phases.

---

## Phase 1 — Multi-Stage Builds & Image Optimization
**Nexio problem:** Images are 1 GB — the CI pipeline takes 18 minutes and Artifact Registry costs are rising.  
**What you learn:** Multi-stage builds, `python:3.12-slim` vs full images, `.dockerignore`, image layer analysis with `docker history` and `dive`, layer caching strategy (copy deps before source).  
**Outcome:** Image drops from ~1 GB to ~120 MB. Build time cut by 70%.

---

## Phase 2 — Multi-Container Apps with Docker Compose
**Nexio problem:** Running the full stack locally requires 6 manual `docker run` commands in the right order, with the right flags, that only one person has memorised.  
**What you learn:** `docker-compose.yml`, services, named networks, named volumes, `depends_on`, `healthcheck`, environment variable files (`.env`). `docker compose up/down/logs/exec`.  
**Stack:** Python API + Node.js worker + Redis + PostgreSQL.

---

## Phase 3 — Production-Ready Images
**Nexio problem:** A routine security scan flagged 47 CVEs in the base image, including 3 critical ones. The infosec team put a hold on the next release.  
**What you learn:** `python:3.12-slim` vs `gcr.io/distroless/python3`, non-root user (`USER`), read-only root filesystem, `HEALTHCHECK` instruction, OCI image labels (`org.opencontainers.*`), `docker scout` / Trivy basics.  
**Outcome:** CVE count drops from 47 to 0. Image is non-root, labelled, and health-checked.

---

## Phase 4 — BuildKit & Advanced Build Patterns
**Nexio problem:** BuildKit cache isn't shared across CI runners — every build re-downloads all pip/npm dependencies from scratch. A 4-minute build takes 4 minutes every single time.  
**What you learn:** `DOCKER_BUILDKIT=1`, `--mount=type=cache` (pip, npm, apt), `--mount=type=secret` (no secrets in image layers), `--build-arg`, multi-platform builds (`--platform linux/amd64,linux/arm64`), `docker buildx`.  
**Outcome:** Cached builds drop to under 30 seconds.

---

## Phase 5 — Container Security Scanning & Signing
**Nexio problem:** A customer's security team asked for a software bill of materials (SBOM) and proof that the image they're running hasn't been tampered with since it left Nexio's CI pipeline.  
**What you learn:** Trivy (vuln scanning + SBOM generation), Cosign + Sigstore (keyless image signing), `docker scout`, attestation with `--attest type=sbom,provenance=true` via `docker buildx build`.  
**Outcome:** Every image push produces a signed manifest + SBOM attestation verifiable by anyone.

---

## Phase 6 — Registry & Image Lifecycle Management
**Nexio problem:** The team has been pushing images to GitHub Container Registry (GHCR) for 6 months with no cleanup policy. 4,000+ untagged images, $300/month in storage.  
**What you learn:** GHCR, ECR, and Artifact Registry basics, tagging strategies (`:latest` vs `:sha-abc123` vs `:v1.2.3`), multi-arch manifest lists, image retention policies, `crane` and `skopeo` for registry operations.

---

## Phase 7 — Runtime Security & Hardening
**Nexio problem:** An external pentest found that Nexio's containers run as root, with full Linux capabilities, writable root filesystems, and no syscall filtering. One container escape = full host access.  
**What you learn:** Linux capabilities (`--cap-drop ALL`, `--cap-add`), seccomp profiles, AppArmor, read-only root filesystem (`--read-only`), rootless Docker/Podman, CIS Docker Benchmark, Falco for runtime anomaly detection.

---

## Phase 8 — Advanced Compose Patterns
**Nexio problem:** The local dev environment keeps drifting from production. Engineers override settings manually, forget to revert them, and bugs appear in CI that can't be reproduced locally.  
**What you learn:** Compose override files (`docker-compose.override.yml`), Compose profiles (`--profile dev`), `watch` mode for hot-reload without volume mounts, Compose secrets, scaling (`--scale`), multi-compose file merging.

---

## Phase 9 — Container-Native Application Design
**Nexio problem:** Config (DB connection strings, feature flags, API keys) is baked into images at build time. Promoting the same artifact from staging to production requires a rebuild — which defeats the purpose of immutable images.  
**What you learn:** 12-factor app principles applied to containers, config via environment variables and mounted config files, init container pattern, sidecar pattern, graceful shutdown (SIGTERM handling), liveness vs readiness concepts (pre-Kubernetes).

---

## Phase 10 — Capstone: Full Production Pipeline
**Nexio problem:** Every piece learned in isolation. Now wire it all together into a single GitHub Actions workflow.  
**What you build:** A complete CI/CD pipeline that:
1. Builds a multi-platform image with BuildKit + remote cache
2. Runs Trivy vulnerability scan (fail on CRITICAL)
3. Generates and attaches an SBOM
4. Signs the image with Cosign (keyless, OIDC)
5. Pushes to GHCR with commit-SHA tag + semver tag on release
6. Attaches provenance attestation
7. Posts a build summary to the PR

**Outcome:** Any engineer on the team (or an external auditor) can verify the exact source commit, build environment, dependencies, and signing chain for any image ever shipped by Nexio.
