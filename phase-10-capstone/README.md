# Phase 10 — Capstone: Production Pipeline

> **This phase ties together every technique from Phases 1–9 into a single, production-grade GitLab CI pipeline.**

---

## What this phase builds

A complete CI/CD pipeline that runs on every push to `main` and on every semver release tag. It integrates:

| Phase | Technique | Where it appears |
|---|---|---|
| 1 | Multi-stage Dockerfile, `.dockerignore` | `context: phase-10-capstone/app` |
| 3 | Non-root user, HEALTHCHECK, OCI labels | Dockerfile |
| 4 | BuildKit cache mounts, `--build-arg`, multi-platform, registry cache | `docker buildx build` with `cache-from/to: type=registry` |
| 5 | Trivy CVE scan, Cosign keyless signing, SBOM + provenance | `scan` and `sign` jobs |
| 6 | SHA + semver tagging, GitLab Container Registry push | tagging logic in `build` job |
| 9 | Structured logs, liveness/readiness probes | Application design |

---

## The problem

> *Nexio — final phase.*
>
> The platform team had built each piece in isolation. BuildKit cache mounts. Trivy scans. Cosign signing. Retention policies. Graceful shutdown.
>
> Each was a separate script, a separate job, a separate repo. Nobody owned the whole. When a new service was added, engineers assembled the pipeline from memory — or from an old job that was "almost right". CVE scanning was sometimes skipped. Signing sometimes happened before scanning. The SBOM was generated but never attached.
>
> *"We need one workflow that every service can copy. Build once, scan always, sign only after a clean scan, push with correct tags. In that order. Every time."*
>
> This is that workflow.

---

## Architecture

```
git push to main (or tag v*.*.*)
│
└── Job 1: build
    ├── docker buildx create            ← Phase 4: BuildKit + QEMU
    ├── tagging logic                   ← Phase 6: sha + semver tags
    ├── docker buildx build --push      ← Phase 4: multi-platform, registry cache
    │    ├── platforms: linux/amd64,linux/arm64
    │    ├── cache-from/to: type=registry,mode=max
    │    ├── build-args: APP_VERSION, BUILD_DATE
    │    ├── --attest type=provenance   ← Phase 5: provenance attestation
    │    └── --attest type=sbom        ← Phase 5: SBOM attestation
    └── digest captured via --metadata-file → passed via dotenv artifact
         │
         ▼
    Job 2: scan  (needs: build)
    ├── trivy image                     ← Phase 5: CVE scan
    │    ├── severity: CRITICAL,HIGH
    │    ├── exit-code: 1              ← fail pipeline on findings
    │    └── format: gitlab            ← GitLab Security dashboard report
    └── ✓ scan passes
         │
         ▼
    Job 3: sign  (needs: build, scan)
    ├── id_tokens: SIGSTORE_ID_TOKEN    ← Phase 5: GitLab OIDC for keyless signing
    ├── cosign sign --yes image@digest ← signs by digest, not tag
    └── cosign verify ...              ← self-verify before the job ends
         │
         ▼
    Job 4: summary  (always runs)
    └── echo to job log                ← build report with verify command
```

---

## Repository structure

```
phase-10-capstone/
├── .gitlab-ci.yml                    ← the complete pipeline
└── app/
    ├── Dockerfile                    ← all best practices from Phases 1–9
    ├── .dockerignore
    ├── app.py
    └── requirements.txt
```

---

## Challenge 1 — Review the full pipeline

### Step 1: Read the pipeline file

```bash
cat phase-10-capstone/.gitlab-ci.yml
```

Walk through each job and match it to the phase that introduced the technique.

### Step 2: Understand the job dependency graph

```yaml
build:   # no needs — runs first
scan:    # needs: [build]       — runs after build
sign:    # needs: [build, scan] — runs only after clean scan
summary: # needs: all, when: always — always runs
```

The ordering is the security guarantee: an image is **never signed before it has passed a CVE scan**. If Trivy finds a CRITICAL vulnerability, the `scan` job fails, the `sign` job is skipped (because its `needs` dependency failed), and the unsigned, vulnerable image stays in the registry unendorsed.

### Step 3: Understand the `rules` conditions

```yaml
# scan job — runs on main pushes and MRs, not on tag-only pipelines
rules:
  - if: $CI_COMMIT_BRANCH == "main"
  - if: $CI_PIPELINE_SOURCE == "merge_request_event"

# sign job — only signs on main or semver tags
rules:
  - if: $CI_COMMIT_BRANCH == "main"
  - if: $CI_COMMIT_TAG =~ /^v\d+\.\d+\.\d+$/
```

On merge requests: build runs (validates the Dockerfile), scan runs (catches CVEs in MRs), sign does NOT run (MRs are not deployed). On pushes to `main`: all four jobs run. On semver tags: all four jobs run.

---

## Challenge 2 — Set up the repository and run the pipeline

### Step 1: Copy the pipeline file to the repository root

GitLab CI requires `.gitlab-ci.yml` at the root of the repository.

```bash
cp phase-10-capstone/.gitlab-ci.yml .gitlab-ci.yml
```

Adjust the `phase-10-capstone/app/` build context path if your Dockerfile lives elsewhere.

### Step 2: Verify registry access

No secrets to configure. GitLab CI provides `$CI_REGISTRY`, `$CI_REGISTRY_USER`, and `$CI_REGISTRY_PASSWORD` automatically for every job. The `id_tokens:` block in the sign job requests the OIDC token for Cosign keyless signing — also automatic.

### Step 3: Push to main and watch the pipeline

```bash
git add .gitlab-ci.yml
git commit -m "feat: add production pipeline"
git push origin main
```

Navigate to **CI/CD → Pipelines** in your GitLab project. You will see the four stages running in sequence.

### Step 4: Inspect the build summary

After the pipeline completes, click into the `summary` job. The log prints the image reference and the exact `cosign verify` command to use:

```
========================================
 Build Summary
========================================
Image: registry.gitlab.com/your-org/nexio-api@sha256:abc123...
Tag:   registry.gitlab.com/your-org/nexio-api:sha-a1b2c3d

Verify:
  cosign verify \
    --certificate-identity-regexp "^project_path:your-org/..." \
    --certificate-oidc-issuer "https://gitlab.com" \
    registry.gitlab.com/your-org/nexio-api@sha256:abc123...
```

---

## Challenge 3 — Trigger a release

### Step 1: Create a semver tag

```bash
git tag v1.0.0
git push origin v1.0.0
```

### Step 2: Observe the additional semver tags

The tagging logic in the `build` job generates:
- `nexio-api:sha-a1b2c3d` — commit SHA (every push)
- `nexio-api:v1.0.0` — exact version (on tag)
- `nexio-api:1.0` — floating minor (on tag)
- `nexio-api:latest` — latest main (on push to main)

```bash
crane ls registry.gitlab.com/your-org/containerization-lab/nexio-api
# latest
# sha-a1b2c3d
# v1.0.0
# 1.0
```

### Step 3: Verify the release image is signed

```bash
IMAGE=registry.gitlab.com/your-org/containerization-lab/nexio-api
DIGEST=$(crane digest $IMAGE:v1.0.0)

cosign verify \
  --certificate-identity-regexp "^project_path:your-org/containerization-lab:.*" \
  --certificate-oidc-issuer "https://gitlab.com" \
  $IMAGE@$DIGEST
```

---

## Challenge 4 — Simulate a CVE failure

### Step 1: Intentionally introduce a vulnerable base image

In `phase-10-capstone/app/Dockerfile`, temporarily change the builder stage:

```dockerfile
# Intentionally old, vulnerable base for this exercise
FROM python:3.9
```

Commit and push to main:

```bash
git add phase-10-capstone/app/Dockerfile
git commit -m "test: use vulnerable base image"
git push origin main
```

### Step 2: Watch the pipeline fail at the scan step

In GitLab CI → **CI/CD → Pipelines**:

- `build` — succeeds (the image builds)
- `scan` — **fails** (Trivy finds CRITICAL CVEs in python:3.9)
- `sign` — **skipped** (because `scan` failed)
- `summary` — runs (it has `when: always`)

The vulnerable image was pushed to GHCR (the build step pushes before the scan). But it is **not signed**. Your deployment policy (enforced via Cosign admission controller or image pull policy) should reject unsigned images — so the vulnerable image can never be deployed.

### Step 3: Revert the Dockerfile

```bash
git revert HEAD
git push origin main
```

The pipeline goes green, the image is signed, and the fix is recorded in git history.

---

## Challenge 5 — Verify the complete artifact chain

For any image in the registry, you should be able to answer five questions in under two minutes:

### Q1: What code is in this image?

```bash
cosign download attestation ghcr.io/your-org/nexio-api:sha-a1b2c3d \
  | jq -r '.payload' | base64 -d \
  | jq '.predicate.invocation.configSource'
# {"uri": "git+https://github.com/your-org/...", "digest": {"sha1": "a1b2c3d..."}}
```

### Q2: Was this image built by our CI pipeline?

```bash
IMAGE=registry.gitlab.com/your-org/containerization-lab/nexio-api
cosign verify \
  --certificate-identity-regexp "^project_path:your-org/containerization-lab:.*" \
  --certificate-oidc-issuer "https://gitlab.com" \
  $IMAGE@sha256:...
# Verification for ... -- The following checks were performed ...
```

### Q3: What are the dependencies in this image?

```bash
cosign download attestation ghcr.io/your-org/nexio-api:sha-a1b2c3d \
  | jq -r 'select(.payload != null) | .payload' | base64 -d \
  | jq '.predicate.components[] | {name: .name, version: .version}' \
  | head -20
```

### Q4: Does this image have any known CVEs?

```bash
trivy image --severity CRITICAL,HIGH ghcr.io/your-org/nexio-api:sha-a1b2c3d
# Total: 0 (HIGH: 0, CRITICAL: 0)
```

### Q5: When was this image built and from which commit?

```bash
crane manifest ghcr.io/your-org/nexio-api:sha-a1b2c3d \
  | jq '.config.digest' \
  | xargs -I{} crane config ghcr.io/your-org/nexio-api@{} \
  | jq '.config.Labels | {
      version: ."org.opencontainers.image.version",
      created: ."org.opencontainers.image.created"
    }'
```

All five questions are answerable from the registry alone — no spreadsheet, no wiki, no Slack history.

---

## Challenge 6 — Extend the pipeline for your own service

The workflow is designed to be copied. To adapt it for a new service:

### Step 1: Change the image name

```yaml
variables:
  IMAGE_NAME: $CI_REGISTRY_IMAGE/your-service-name
```

### Step 2: Change the build context

```yaml
docker buildx build \
  ...
  path/to/your/service/
```

### Step 3: Adjust severity thresholds if needed

Start strict:

```yaml
severity: CRITICAL,HIGH
exit-code: "1"
```

If your team is working from a legacy codebase with existing HIGH findings, temporarily allow HIGH while blocking CRITICAL — and set a deadline to resolve the HIGH findings:

```yaml
severity: CRITICAL
exit-code: "1"
```

Never remove the scan step. A pipeline without a scan is a vulnerability delivery mechanism.

---

## Production considerations

### 1. The pipeline is the security boundary
GitLab CI jobs run on ephemeral runners. `$CI_REGISTRY_PASSWORD` (a short-lived job token) and the `id_tokens:` OIDC JWT are the only credentials needed — both are provided automatically and scoped to the current job. There are no long-lived secrets to rotate, no SSH keys to protect, no service account JSON files. This is the correct model.

### 2. Enforce signatures at deployment time
A signing pipeline is only meaningful if unsigned images are rejected at deployment. In Kubernetes, deploy a Kyverno policy or OPA Gatekeeper constraint that calls `cosign verify` before admitting a pod. Without enforcement, signing is an audit trail — useful but not a security control.

### 3. Pin Docker image versions in production
```yaml
image: docker:27.3.1        # pinned — not docker:latest
image: aquasec/trivy:0.62.0 # pinned — not trivy:latest
```
Floating tags (`docker:latest`, `trivy:latest`) can change between pipeline runs, introducing unexpected behaviour or vulnerabilities. Pin to specific versions and use Renovate or Dependabot to open MRs when new versions are published.

### 4. The build job pushes before the scan
This is a deliberate trade-off: the multi-platform build (`docker buildx build --push`) requires the image to be in the registry before attestations can be attached. The image exists in the registry but is unsigned. Enforce a policy that only signed images can be pulled — unsigned images in the registry are harmless without enforcement.

### 5. Treat the pipeline itself as code
`.gitlab-ci.yml` deserves the same review standards as application code. A change to the pipeline should require approval from a platform engineer. An MR that removes the CVE scan step or changes `exit-code: 1` to `0` should be caught in review — not discovered in a post-incident retrospective. Use GitLab's **protected branches** and **required approvals** to enforce this.

---

## Outcome

A single reusable workflow that enforces the full supply chain security posture for every image Nexio ships:

- **Built** from an audited Dockerfile with multi-platform support and remote caching
- **Scanned** for CVEs before signing — findings block the pipeline
- **Signed** with an OIDC-bound keyless certificate tied to the exact CI workflow identity
- **Attested** with SBOM and provenance — verifiable by any auditor with registry access
- **Tagged** with an immutable SHA and optionally a semver version
- **Reported** with a build summary on every run

Any engineer can run this pipeline. Any auditor can verify any image it produced. Any deployment system can enforce that only images from this pipeline are allowed to run.

---

[Back to Phase 9](../phase-9-container-native/README.md) | [Back to main README](../README.md)
