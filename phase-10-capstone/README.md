# Phase 10 — Capstone: Production Pipeline

> **This phase ties together every technique from Phases 1–9 into a single, production-grade GitHub Actions workflow.**

---

## What this phase builds

A complete CI/CD pipeline that runs on every push to `main` and on every semver release tag. It integrates:

| Phase | Technique | Where it appears |
|---|---|---|
| 1 | Multi-stage Dockerfile, `.dockerignore` | `context: phase-10-capstone/app` |
| 3 | Non-root user, HEALTHCHECK, OCI labels | Dockerfile |
| 4 | BuildKit cache mounts, `--build-arg`, multi-platform, GHA cache | `build-push-action` with `cache-from/to: type=gha` |
| 5 | Trivy CVE scan, Cosign keyless signing, SBOM + provenance | `scan` and `sign` jobs |
| 6 | SHA + semver tagging, GHCR push | `metadata-action` tags matrix |
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
    ├── docker/setup-buildx-action       ← Phase 4: BuildKit + QEMU
    ├── docker/metadata-action           ← Phase 6: sha + semver tags
    ├── docker/build-push-action         ← Phase 4: multi-platform, GHA cache
    │    ├── platforms: linux/amd64,linux/arm64
    │    ├── cache-from/to: type=gha,mode=max
    │    ├── build-args: APP_VERSION, BUILD_DATE
    │    ├── provenance: true            ← Phase 5: provenance attestation
    │    └── sbom: true                 ← Phase 5: SBOM attestation
    └── outputs: digest, image_ref, tags
         │
         ▼
    Job 2: scan  (needs: build)
    ├── aquasecurity/trivy-action        ← Phase 5: CVE scan
    │    ├── severity: CRITICAL,HIGH
    │    ├── exit-code: 1               ← fail pipeline on findings
    │    └── format: sarif              ← upload to GitHub Security tab
    └── ✓ scan passes
         │
         ▼
    Job 3: sign  (needs: build, scan)
    ├── sigstore/cosign-installer        ← Phase 5: keyless signing
    ├── cosign sign --yes image@digest  ← signs by digest, not tag
    └── cosign verify ...               ← self-verify before the job ends
         │
         ▼
    Job 4: summary  (always runs)
    └── $GITHUB_STEP_SUMMARY            ← build report in PR/push view
```

---

## Repository structure

```
phase-10-capstone/
├── .github/
│   └── workflows/
│       └── production-pipeline.yml   ← the complete pipeline
└── app/
    ├── Dockerfile                    ← all best practices from Phases 1–9
    ├── .dockerignore
    ├── app.py
    └── requirements.txt
```

---

## Challenge 1 — Review the full workflow

### Step 1: Read the workflow file

```bash
cat phase-10-capstone/.github/workflows/production-pipeline.yml
```

Walk through each job and match it to the phase that introduced the technique.

### Step 2: Understand the job dependency graph

```yaml
jobs:
  build:   # no dependency — runs first
  scan:    # needs: [build]  — runs after build
  sign:    # needs: [build, scan]  — runs only after clean scan
  summary: # needs: [build, scan, sign], if: always()  — always runs
```

The ordering is the security guarantee: an image is **never signed before it has passed a CVE scan**. If Trivy finds a CRITICAL vulnerability, the `scan` job fails, the `sign` job is skipped (because its `needs` dependency failed), and the unsigned, vulnerable image stays in the registry unendorsed.

### Step 3: Understand the `if` conditions

```yaml
# scan job
if: github.event_name != 'push' || github.ref == 'refs/heads/main'

# sign job
if: |
  github.event_name != 'pull_request' &&
  (github.ref == 'refs/heads/main' || startsWith(github.ref, 'refs/tags/v'))
```

On pull requests: build runs (validates the Dockerfile), scan runs (catches CVEs in PRs), sign does NOT run (PRs are not deployed). On pushes to `main`: all four jobs run. On semver tags: all four jobs run.

---

## Challenge 2 — Set up the repository and run the pipeline

### Step 1: Fork or use the lab repository

The workflow is at `phase-10-capstone/.github/workflows/production-pipeline.yml`. For it to run, the `.github/workflows/` directory must be at the root of the repository that GitHub Actions monitors.

Copy the workflow to the root of your own repository:

```bash
mkdir -p .github/workflows
cp phase-10-capstone/.github/workflows/production-pipeline.yml \
   .github/workflows/production-pipeline.yml
```

Adjust the `context:` path in the workflow to match where your Dockerfile lives.

### Step 2: Verify GHCR permissions

The workflow uses `secrets.GITHUB_TOKEN` for GHCR authentication. No additional secrets need to be configured — the token is provided automatically by GitHub Actions. Ensure the repository's **Settings → Actions → General → Workflow permissions** is set to "Read and write permissions".

### Step 3: Push to main and watch the pipeline

```bash
git add .github/workflows/production-pipeline.yml
git commit -m "feat: add production pipeline"
git push origin main
```

Navigate to **Actions** in your GitHub repository. You will see the four jobs running in sequence.

### Step 4: Inspect the build summary

After the pipeline completes, click on the workflow run and scroll to the bottom. The `summary` job writes to `$GITHUB_STEP_SUMMARY` — a markdown report visible directly in the workflow run view:

```
## Build Summary

| Step  | Status  |
|-------|---------|
| Build | success |
| Scan  | success |
| Sign  | success |

### Image
ghcr.io/your-org/nexio-api@sha256:abc123...

### Verify signature
cosign verify \
  --certificate-identity-regexp "https://github.com/your-org/..." \
  --certificate-oidc-issuer "https://token.actions.githubusercontent.com" \
  ghcr.io/your-org/nexio-api@sha256:abc123...
```

---

## Challenge 3 — Trigger a release

### Step 1: Create a semver tag

```bash
git tag v1.0.0
git push origin v1.0.0
```

### Step 2: Observe the additional semver tags

The `metadata-action` generates:
- `nexio-api:sha-a1b2c3d` — commit SHA (every push)
- `nexio-api:v1.0.0` — exact version (on tag)
- `nexio-api:1.0` — floating minor (on tag)
- `nexio-api:latest` — latest main (on push to main)

```bash
crane ls ghcr.io/your-org/nexio-api
# latest
# sha-a1b2c3d
# v1.0.0
# 1.0
```

### Step 3: Verify the release image is signed

```bash
DIGEST=$(crane digest ghcr.io/your-org/nexio-api:v1.0.0)

cosign verify \
  --certificate-identity-regexp "https://github.com/your-org/containerization-lab/.*" \
  --certificate-oidc-issuer "https://token.actions.githubusercontent.com" \
  ghcr.io/your-org/nexio-api@$DIGEST
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

In GitHub Actions:

- `build` — succeeds (the image builds)
- `scan` — **fails** (Trivy finds CRITICAL CVEs in python:3.9)
- `sign` — **skipped** (because `scan` failed)
- `summary` — reports the failure

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
cosign verify \
  --certificate-identity-regexp "https://github.com/your-org/containerization-lab/.*" \
  --certificate-oidc-issuer "https://token.actions.githubusercontent.com" \
  ghcr.io/your-org/nexio-api@sha256:...
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
env:
  IMAGE_NAME: ${{ github.repository_owner }}/your-service-name
```

### Step 2: Change the build context

```yaml
- uses: docker/build-push-action@v6
  with:
    context: path/to/your/service
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
The workflow runs on GitHub-hosted runners with ephemeral environments. The `id-token: write` permission and the `GITHUB_TOKEN` are the only credentials needed. There are no long-lived secrets to rotate, no SSH keys to protect, no service account JSON files. This is the correct model.

### 2. Enforce signatures at deployment time
A signing pipeline is only meaningful if unsigned images are rejected at deployment. In Kubernetes, deploy a Kyverno policy or OPA Gatekeeper constraint that calls `cosign verify` before admitting a pod. Without enforcement, signing is an audit trail — useful but not a security control.

### 3. Pin action versions by commit SHA in production
```yaml
uses: actions/checkout@11bd71901bbe5b1630ceea73d27597364c9af683  # v4.2.2
```
Tag-based action references (`@v4`) can be overwritten by the action author. A SHA reference is immutable. Use [Dependabot](https://docs.github.com/en/code-security/dependabot/working-with-dependabot/keeping-your-actions-up-to-date-with-dependabot) to automatically open PRs when new versions are available.

### 4. The build job pushes before the scan
This is a deliberate trade-off: the multi-platform build (`docker buildx build --push`) requires the image to be in the registry before attestations can be attached. The image exists in the registry but is unsigned. Enforce a policy that only signed images can be pulled — unsigned images in the registry are harmless without enforcement.

### 5. Treat the pipeline itself as code
`production-pipeline.yml` deserves the same review standards as application code. A change to the pipeline should require approval from a platform engineer. A PR that removes the CVE scan step or changes `exit-code: "1"` to `"0"` should be caught in review — not discovered in a post-incident retrospective.

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
