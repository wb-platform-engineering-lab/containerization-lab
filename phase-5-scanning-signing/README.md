# Phase 5 — Container Security Scanning & Signing

> **Concepts introduced:** SBOM, CVE scanning in CI, Cosign key-based signing, Sigstore keyless signing (CI), provenance attestation, `docker buildx --attest`
>
> **CI/CD:** GitLab CI (`.gitlab-ci.yml`)

---

## Concepts introduced

| Concept | What it is | Why it matters |
|---|---|---|
| **SBOM** | Software Bill of Materials — a machine-readable inventory of every component in an image | Regulatory compliance (EO 14028), supply chain auditing, rapid CVE response |
| **CVE scanning in CI** | Trivy runs as a CI step and fails the pipeline on critical findings | Shifts security left — vulnerabilities caught before they reach a registry |
| **Cosign** | A tool for signing and verifying container images | Cryptographic proof that an image came from your pipeline and hasn't been tampered with |
| **Key-based signing** | Cosign signs with a private key you generate and control — no outbound calls to Sigstore services | Right for private infrastructure; requires secure key storage and rotation |
| **Sigstore / keyless signing** | Cosign uses a short-lived certificate bound to a CI OIDC identity instead of a long-lived private key | No key management, no key leakage — used in the GitLab CI pipeline (Challenge 6) |
| **Provenance attestation** | Metadata describing how an image was built (repo, commit, builder, workflow) | Answers: was this image built by our CI? From which commit? By which workflow? |
| **`--attest`** | `docker buildx build --attest type=sbom` / `--attest type=provenance` | Generates and attaches SBOM and provenance as OCI attestation manifests alongside the image |

---

## The problem

> *Nexio — 25 engineers. Five months in.*
>
> A prospective enterprise customer sent a security questionnaire. One section: *Software Supply Chain Security*.
>
> *"Can you provide a Software Bill of Materials for your API?"*
> *"How do we verify that the image you've deployed hasn't been modified since it left your CI pipeline?"*
> *"Do you have documented build provenance for each release?"*
>
> The answers were no, no, and no.
>
> The deal was worth €400,000 annually. The customer gave Nexio 30 days.
>
> The platform team spent the first week reading the SLSA framework documentation and the Sigstore project. By the end of the month, every image pushed to the registry was:
>
> - Scanned for CVEs with Trivy before pushing
> - Signed with Cosign using a keyless OIDC certificate tied to the GitLab CI job identity
> - Accompanied by an SBOM in CycloneDX format
> - Accompanied by a provenance attestation documenting the exact repo, commit, and workflow that produced it
>
> Any auditor — internal or external — could verify any image in under 30 seconds.

---

## Architecture

```
Developer pushes to main
│
└── GitLab CI: .gitlab-ci.yml
    │
    ├── 1. build job (docker buildx build)
    │       ├── --attest type=provenance → provenance manifest attached to image
    │       └── --attest type=sbom      → SBOM manifest attached to image
    │
    ├── 2. Push to GitLab Container Registry
    │       registry.gitlab.com/org/nexio-api:sha-a1b2c3
    │
    ├── 3. scan job (Trivy)
    │       ├── CVE scan → GitLab format report → Security dashboard
    │       └── EXIT CODE 1 on CRITICAL/HIGH → pipeline fails before signing
    │
    └── 4. sign job (Cosign — keyless)
            ├── GitLab OIDC token (id_tokens:) → Fulcio CA → short-lived certificate
            ├── Certificate proves: this signature was created by
            │   https://gitlab.com/org/repo//.gitlab-ci.yml@refs/heads/main
            └── Signature stored in Rekor transparency log (public, tamper-evident)


Verification (anyone, anytime)
└── cosign verify --certificate-identity-regexp ... \
                  --certificate-oidc-issuer "https://gitlab.com" \
                  registry.gitlab.com/org/nexio-api@sha256:abc123
    └── Rekor: signature found, certificate valid, identity matches → OK
```

---

## Repository structure

```
phase-5-scanning-signing/
├── .gitlab-ci.yml           ← full CI pipeline: build, scan, sign
└── app/
    ├── Dockerfile            ← same as Phase 4 (BuildKit, build-args)
    ├── .dockerignore
    ├── app.py
    └── requirements.txt
```

---

## Challenge 1 — Generate an SBOM locally with Trivy

Before automating in CI, understand what an SBOM is and how to read one.

### Step 1: Build the image

```bash
docker build \
  --build-arg APP_VERSION=$(git rev-parse --short HEAD 2>/dev/null || echo "dev") \
  --build-arg BUILD_DATE=$(date -u +%Y-%m-%dT%H:%M:%SZ) \
  -t nexio-api:0.4 \
  phase-5-scanning-signing/app/
```

### Step 2: Generate SBOM in CycloneDX JSON format

```bash
trivy image --format cyclonedx --output nexio-api.sbom.json nexio-api:0.4
```

### Step 3: Inspect the SBOM

```bash
cat nexio-api.sbom.json | jq '.components[] | {name: .name, version: .version, purl: .purl}' | head -40
```

Expected output (truncated):
```json
{"name": "flask", "version": "3.1.0", "purl": "pkg:pypi/flask@3.1.0"}
{"name": "werkzeug", "version": "3.1.3", "purl": "pkg:pypi/werkzeug@3.1.3"}
{"name": "click", "version": "8.1.8", "purl": "pkg:pypi/click@8.1.8"}
{"name": "libc6", "version": "2.36-9", "purl": "pkg:deb/debian/libc6@2.36-9"}
...
```

An SBOM lists every component in the image: Python packages (with PyPI PURLs), OS packages (with Debian PURLs), and their versions. When a new CVE is published for `werkzeug`, you can query every SBOM in your registry to find which images are affected — without rescanning.

### Step 4: Generate in SPDX format

SPDX is the other major SBOM standard (used more in the US government context):

```bash
trivy image --format spdx-json --output nexio-api.sbom.spdx.json nexio-api:0.4
cat nexio-api.sbom.spdx.json | jq '.packages[0:3] | .[] | {name: .name, versionInfo: .versionInfo}'
```

> **CycloneDX vs SPDX:** Both are valid. CycloneDX is more commonly used in DevSecOps toolchains. SPDX has broader government adoption (NTIA minimum requirements). Generate both if you need to satisfy multiple stakeholders.

---

## Challenge 2 — Scan with Trivy and fail the pipeline

### Step 1: Run a targeted scan

```bash
trivy image --severity CRITICAL,HIGH --exit-code 1 --ignore-unfixed nexio-api:0.4
```

`--exit-code 1` causes Trivy to exit with code 1 if any findings match the severity filter — the image is never pushed. `--ignore-unfixed` restricts this to vulnerabilities that have a fix available: OS-level CVEs in the base image are often reported as CRITICAL but have no upstream patch yet, and blocking on those would make the pipeline permanently red through no fault of your code.

### Step 2: Simulate a vulnerable image

Build an image with an outdated base:

```bash
cat <<'EOF' > /tmp/Dockerfile.vuln
FROM python:3.9
RUN pip install flask==2.0.0
COPY phase-5-scanning-signing/app/app.py /app/app.py
WORKDIR /app
CMD ["python", "app.py"]
EOF

docker build -t nexio-api:vulnerable -f /tmp/Dockerfile.vuln .
trivy image --severity CRITICAL,HIGH --exit-code 1 nexio-api:vulnerable
echo "Exit code: $?"
# Exit code: 1 — pipeline would stop here
```

### Step 3: Understand the SARIF output format

SARIF is the format used in CI for uploading results to GitLab's Security dashboard (`--format gitlab` was removed from Trivy in v0.50):

```bash
trivy image --severity CRITICAL,HIGH \
  --format sarif \
  --output trivy-results.sarif \
  nexio-api:0.4

cat trivy-results.sarif | jq '.runs[0].results | length'
# 0 — no findings
```

In CI, Trivy runs with `--format sarif` and the output is uploaded as a `sast` artifact. It appears in **GitLab → Security → Vulnerability report**, grouped by severity, with links to CVE details. (`--format gitlab` was removed from Trivy in v0.50; SARIF is the current supported path for GitLab Security dashboard integration.)

---

## Challenge 3 — Install Cosign and sign an image locally

### Step 1: Install Cosign

```bash
# macOS
brew install cosign

# Linux
curl -sL https://github.com/sigstore/cosign/releases/latest/download/cosign-linux-amd64 \
  -o /usr/local/bin/cosign && chmod +x /usr/local/bin/cosign

cosign version
```

### Step 2: Authenticate with the GitLab Container Registry

The GitLab Container Registry uses your GitLab credentials for authentication. For local `docker` commands, generate a **Personal Access Token (PAT)** with `read_registry` and `write_registry` scopes.

**Create the token:**

1. Go to **gitlab.com → your avatar (top-right) → Edit profile**
2. Click **Access tokens** in the left sidebar
3. Click **Add new token**
4. Give it a name: `cr-containerization-lab`
5. Set an expiration (30 days is sufficient for the lab)
6. Select these scopes:

   | Scope | Why |
   |---|---|
   | `read_registry` | Pull images from the registry |
   | `write_registry` | Push images to the registry |

7. Click **Create personal access token**
8. **Copy the token immediately** — GitLab will not show it again

**Store and log in:**

```bash
export GL_PAT=glpat-xxxxxxxxxxxxxxxxxxxx

echo $GL_PAT | docker login registry.gitlab.com -u YOUR_GITLAB_USERNAME --password-stdin
# Login Succeeded
```

Always use `--password-stdin` instead of `-p $TOKEN` — the latter exposes the token in your shell history and in `ps` output.

### Step 3: Tag and push the image to the GitLab Container Registry

```bash
# Tag
docker tag nexio-api:0.4 registry.gitlab.com/YOUR_NAMESPACE/containerization-lab/nexio-api:0.4

# Push
docker push registry.gitlab.com/YOUR_NAMESPACE/containerization-lab/nexio-api:0.4
```

The pushed image is private by default. Visibility follows the project's visibility setting.

**Capture the digest:**

```bash
# Use crane digest — always returns a registry-qualified reference.
# docker inspect is unreliable here: if the image was built locally before
# tagging, RepoDigests may be empty or missing the registry prefix, causing
# Cosign to fall back to Docker Hub and fail with UNAUTHORIZED.
IMAGE=registry.gitlab.com/YOUR_NAMESPACE/containerization-lab/nexio-api
DIGEST=$IMAGE@$(crane digest $IMAGE:0.4)
echo $DIGEST
# registry.gitlab.com/.../nexio-api@sha256:abc123...
```

> **Why sign by digest, not tag?** Tags are mutable — `nexio-api:0.4` can be overwritten at any time. A digest (`sha256:abc123`) is immutable and uniquely identifies the exact bytes. Signing by digest creates an unforgeable link between the signature and those specific bytes.

### Step 4: Generate a local key pair

For private infrastructure you sign with your own key pair instead of delegating to
Fulcio (Sigstore's public CA) or recording anything in Rekor (Sigstore's public
transparency log). No browser, no OIDC, no outbound network calls beyond pushing
the signature to your own registry.

```bash
cosign generate-key-pair
```

This creates two files in the current directory:

| File | Contents | Who sees it |
|------|----------|-------------|
| `cosign.key` | Encrypted private key | Keep secret — treat like an SSH private key |
| `cosign.pub` | Public key | Distribute freely — commit to the repo, embed in policy |

You will be prompted for a passphrase to encrypt `cosign.key`. Use a strong passphrase
in real environments. For the lab, pressing Enter twice (empty passphrase) is fine.

> **Key storage in production:** store `cosign.key` in your secrets manager (GitLab
> CI/CD variables marked *Protected + Masked*, HashiCorp Vault, AWS Secrets Manager).
> Never commit it to the repository.

### Step 5: Sign with the private key

```bash
cosign sign \
  --key cosign.key \
  --tlog-upload=false \
  $DIGEST
```

Flags:

| Flag | Effect |
|------|--------|
| `--key cosign.key` | Use your local private key instead of Fulcio OIDC flow |
| `--tlog-upload=false` | Do not upload an entry to Rekor — keeps the signature fully private |

Cosign pushes the signature as an OCI artifact to your GitLab registry alongside the
image. No request is made to any Sigstore service.

Expected output:

```
Pushing signature to: registry.gitlab.com/YOUR_NAMESPACE/containerization-lab/nexio-api
```

### Step 6: Verify the signature

```bash
cosign verify \
  --key cosign.pub \
  --insecure-ignore-tlog \
  $DIGEST
```

Flags:

| Flag | Effect |
|------|--------|
| `--key cosign.pub` | Verify against your public key (no Fulcio certificate chain) |
| `--insecure-ignore-tlog` | Do not require a Rekor entry — correct for key-based private signing |

Expected output:

```
Verification for registry.gitlab.com/YOUR_NAMESPACE/containerization-lab/nexio-api@sha256:abc123 --
The following checks were performed on each of these signatures:
  - The cosign claims were validated
  - The signatures were verified against the specified public key

[{"critical":{"identity":{"docker-reference":"registry.gitlab.com/..."},...}}]
```

> **What the signature proves:** that whoever held `cosign.key` at signing time
> approved this exact image digest. It does not prove *which person* signed it the way
> an OIDC certificate does — identity is asserted by controlling the key, not by a CA.
> This is acceptable for private infrastructure where you control the key distribution.

---

## Challenge 4 — Understand keyless signing and the Sigstore ecosystem

Challenge 3 used key-based signing — the right choice for private infrastructure
where no request should leave your network. The trade-off is key management: the
private key must be stored securely, rotated, and protected from leakage. If it
leaks, every signature it ever produced is suspect.

Keyless signing (the default in public CI/CD) eliminates the key entirely by
replacing it with a short-lived certificate issued by Sigstore's public CA (Fulcio)
bound to a verifiable OIDC identity — no key to rotate, no key to leak. The cost is
that every signing event is recorded in a public transparency log (Rekor) and requires
outbound access to Sigstore's infrastructure.

### Key-based vs keyless — when to use which

| | Key-based (Challenge 3) | Keyless (this challenge) |
|---|---|---|
| External network | Registry only | Fulcio + Rekor + registry |
| Identity proof | Whoever holds the key | OIDC (GitHub Actions, Google, etc.) |
| Key management | Required | None (ephemeral cert) |
| Signature auditability | Private | Public (Rekor log) |
| Right for | Private infra, air-gapped, self-hosted | Public OSS, SaaS CI/CD |

### How Sigstore keyless signing works

```
1. Cosign generates a short-lived ephemeral key pair (valid for ~10 minutes)
2. Cosign requests a certificate from Fulcio, proving:
      "This key pair is controlled by an identity that authenticated
       via OIDC as: github.com/org/repo (workflow=scan-sign.yml, ref=main)"
3. Fulcio issues a certificate binding the ephemeral public key to that identity
4. Cosign signs the image digest with the ephemeral private key
5. The signature + certificate are stored in the registry
6. The signing event is recorded in Rekor (public, tamper-evident append-only log)
7. The ephemeral private key is discarded — it was only valid for 10 minutes anyway
```

Verification does not require knowing the signer's key in advance. It requires knowing their *identity*:

```bash
cosign verify \
  --certificate-identity-regexp \
    "^project_path:YOUR_NAMESPACE/containerization-lab:.*" \
  --certificate-oidc-issuer "https://gitlab.com" \
  $DIGEST
```

This means: *"accept this image only if it was signed by a GitLab CI job running in this project."*

### Step 1: Look up a signature in Rekor

```bash
# Get the Rekor log entry for the signature
REKOR_UUID=$(cosign triangulate $DIGEST)
echo $REKOR_UUID

# Fetch the log entry
rekor-cli get --uuid $REKOR_UUID --format json | jq .
```

The Rekor entry contains the certificate, the image digest, and a timestamp. It cannot be deleted or modified — it is an append-only transparency log. This means the signing event is publicly auditable forever.

---

## Challenge 5 — Generate provenance and SBOM attestations inline with `docker buildx`

`docker buildx build` can generate and attach SBOM and provenance as OCI attestation manifests during the build itself — no separate Trivy step required for the SBOM.

### Step 1: Build with attestations

```bash
docker buildx build \
  --platform linux/amd64 \
  --build-arg APP_VERSION=$(git rev-parse --short HEAD 2>/dev/null || echo "dev") \
  --build-arg BUILD_DATE=$(date -u +%Y-%m-%dT%H:%M:%SZ) \
  --attest type=sbom \
  --attest type=provenance,mode=max \
  -t registry.gitlab.com/YOUR_NAMESPACE/containerization-lab/nexio-api:0.4 \
  --push \
  phase-5-scanning-signing/app/
```

| Attestation | What it contains |
|---|---|
| `type=sbom` | CycloneDX SBOM of all packages in the image |
| `type=provenance,mode=max` | Full build provenance: repo URL, commit SHA, build trigger, runner environment |

### Step 2: Inspect the attestations

```bash
IMAGE=registry.gitlab.com/YOUR_NAMESPACE/containerization-lab/nexio-api
cosign download attestation $IMAGE:0.4 | \
  jq -r '.payload' | base64 -d | jq '.predicate'
```

The provenance predicate contains:
- `buildType`: the build tool (Docker Buildx)
- `invocation.configSource.uri`: the GitHub repo and commit
- `materials`: the base image digests used in the build

### Step 3: Verify the SBOM attestation

```bash
IMAGE=registry.gitlab.com/YOUR_NAMESPACE/containerization-lab/nexio-api
cosign verify-attestation \
  --type cyclonedx \
  --certificate-identity-regexp \
    "^project_path:YOUR_NAMESPACE/containerization-lab:.*" \
  --certificate-oidc-issuer "https://gitlab.com" \
  $IMAGE:0.4
```

---

## Challenge 6 — Run the pipeline in GitLab CI

Challenges 1–5 taught you every command by running it locally. This challenge automates the exact same sequence in GitLab CI so you understand what runs locally versus what runs in CI — and why the CI version is the one that matters in production.

> **Why run locally first?** You cannot debug a CI pipeline you don't understand. Running Trivy and Cosign locally means you can inspect outputs, iterate on flags, and reproduce failures without the overhead of a push + queue + runner startup cycle. Once you understand each step, CI is just orchestration.

### Step 1: Copy the pipeline file to the repository root

GitLab CI requires `.gitlab-ci.yml` at the root of the repository. Copy the phase-5 pipeline there:

```bash
cp phase-5-scanning-signing/.gitlab-ci.yml .gitlab-ci.yml
```

### Step 2: Understand how authentication works in GitLab CI

Unlike GitHub Actions (which requires configuring package write permissions), GitLab CI provides registry credentials and an OIDC token automatically — no secrets to configure.

| Variable | What it is | Set by |
|---|---|---|
| `$CI_REGISTRY` | `registry.gitlab.com` | GitLab — automatic |
| `$CI_REGISTRY_USER` | `gitlab-ci-token` | GitLab — automatic |
| `$CI_REGISTRY_PASSWORD` | short-lived job token | GitLab — automatic |
| `$CI_REGISTRY_IMAGE` | full image path for this project | GitLab — automatic |

The pipeline uses these without any manual configuration.

### Step 3: Understand the `id_tokens` block

```yaml
id_tokens:
  SIGSTORE_ID_TOKEN:
    aud: sigstore
```

This is the GitLab CI equivalent of GitHub's `id-token: write` permission. It requests a short-lived OIDC JWT with audience `sigstore`, which Cosign uses to obtain a Fulcio certificate — no private key required.

The certificate identity is bound to the job's GitLab identity. In Cosign v2+, the SAN in the Fulcio certificate uses the HTTPS URI format:

```
https://gitlab.com/YOUR_NAMESPACE/containerization-lab//.gitlab-ci.yml@refs/heads/main
```

Note the double `//` — this comes from the OIDC token's `sub` claim and is intentional.

This is the key difference between signing locally (you authenticate via browser) and signing in CI (the job authenticates via OIDC token, no human in the loop).

### Step 4: Push to trigger the pipeline

```bash
git add .gitlab-ci.yml
git commit -m "ci: add scan-sign pipeline"
git push origin main
```

Navigate to **GitLab → your project → CI/CD → Pipelines**. You will see the pipeline running with three stages: `build`, `scan`, `sign`.

### Step 5: Follow the build job

Click into the running pipeline, then click the `build` job. Watch the logs:

- `docker buildx create` — creates a BuildKit builder inside the DinD service
- `docker buildx build` — builds the image for `linux/amd64`, pushes to the GitLab Container Registry, attaches SBOM and provenance attestations
- The `IMAGE_DIGEST` is captured via `--metadata-file` and passed to downstream jobs via a dotenv artifact

Look for the push confirmation in the log:
```
#18 pushing manifest for registry.gitlab.com/.../nexio-api:sha-abc123@sha256:...
#18 DONE
```

### Step 6: Follow the scan job

The `scan` job starts after `build` completes. Watch the Trivy output:

```
2026-04-29T10:00:00Z INFO Vulnerability scanning is enabled
2026-04-29T10:00:05Z INFO Detected OS: debian 12.x
...
registry.gitlab.com/.../nexio-api@sha256:abc123 (debian 12.x)
Total: 0 (HIGH: 0, CRITICAL: 0)
```

A clean scan → exit code 0 → the `sign` job is unlocked.

After the scan job completes, go to **GitLab → your project → Security → Vulnerability report**. The Trivy results appear there — grouped by severity, with CVE links.

### Step 7: Follow the sign job

The `sign` job runs only after both `build` and `scan` succeed. Watch the log:

```
Generating ephemeral keys...
Retrieving signed certificate...
Successfully verified SCT...
tlog entry created with index: 12345678
Pushing signature to: registry.gitlab.com/.../nexio-api
```

No browser authentication. No private key. The runner used its OIDC token — GitLab's cryptographic proof of the job's identity — to obtain a Fulcio certificate and sign the image automatically.

### Step 8: Verify the signature locally

Back on your machine, verify that the image was signed by your CI pipeline:

```bash
IMAGE=registry.gitlab.com/YOUR_NAMESPACE/containerization-lab/nexio-api
DIGEST=$(crane digest $IMAGE:latest)

cosign verify \
  --certificate-identity-regexp \
    "^https://gitlab\.com/YOUR_NAMESPACE/containerization-lab/.*@refs/heads/main$" \
  --certificate-oidc-issuer "https://gitlab.com" \
  $IMAGE@$DIGEST
```

Expected:
```
Verification for registry.gitlab.com/.../nexio-api@sha256:abc123 --
The following checks were performed on each of these signatures:
  - The cosign claims were validated
  - Existence of the claims in the transparency log was verified offline
  - The code-signing certificate claims were validated
```

This is the end-to-end proof: the image in the registry was built by your CI pipeline, passed a Trivy scan with no CRITICAL/HIGH findings, and was signed with a certificate bound to that specific job identity. Any deployment system can run this `cosign verify` command to enforce the policy.

### Step 9: Simulate a scan failure

Edit `phase-5-scanning-signing/app/Dockerfile` and temporarily use a vulnerable base:

```dockerfile
FROM python:3.9   # intentionally old
```

Commit and push:

```bash
git add phase-5-scanning-signing/app/Dockerfile .gitlab-ci.yml
git commit -m "test: use vulnerable base to trigger scan failure"
git push origin main
```

Watch the pipeline in **CI/CD → Pipelines**:

- `build` — succeeds (the image builds fine)
- `scan` — **fails** (Trivy finds CRITICAL CVEs, exits with code 1)
- `sign` — **skipped** (because `scan` failed — a failed `needs` dependency skips dependent jobs)

The image exists in the registry but is **not signed**. A deployment policy that requires a valid Cosign signature will reject it.

Revert the Dockerfile and push again:

```bash
git revert HEAD --no-edit
git push origin main
```

The pipeline goes green, the image is signed, and the revert is in git history — a clean audit trail.

---

## Command reference

| Command | What it does |
|---|---|
| `trivy image name:tag` | Vulnerability scan |
| `trivy image --format cyclonedx -o sbom.json name:tag` | Generate CycloneDX SBOM |
| `trivy image --format spdx-json -o sbom.spdx.json name:tag` | Generate SPDX SBOM |
| `trivy image --format sarif -o trivy-results.sarif name:tag` | GitLab Security dashboard report (upload as `sast` artifact) |
| `cosign generate-key-pair` | Generate a local key pair (cosign.key + cosign.pub) |
| `cosign sign --key cosign.key --tlog-upload=false image@digest` | Key-based sign, no Rekor upload (private infra) |
| `cosign verify --key cosign.pub --insecure-ignore-tlog image@digest` | Verify a key-based signature |
| `cosign sign --yes image@digest` | Keyless sign via OIDC + Fulcio (public CI/CD) |
| `cosign verify --certificate-identity ... image@digest` | Verify a keyless signature |
| `cosign download attestation image:tag` | Download attached attestations |
| `cosign verify-attestation --type cyclonedx ...` | Verify an SBOM attestation |
| `cosign triangulate image@digest` | Get the Rekor UUID for a signature |

---

## Production considerations

### 1. Fail-open is not acceptable — fail the pipeline on CRITICAL CVEs
A Trivy scan that only reports findings but never blocks a build provides a false sense of security. Set `--exit-code 1` on CRITICAL (and HIGH once your baseline is clean). The discomfort of a blocked pipeline is far less than the discomfort of a breach.

### 2. Sign by digest, never by tag
Tags are mutable. `nexio-api:latest` today may point to a different image tomorrow. A signature on a tag is meaningless if the tag can be reassigned. Always sign and verify by digest: `image@sha256:abc123`.

### 3. Key-based signing keeps signatures off the public Rekor log
The local signing workflow in Challenge 3 uses `--tlog-upload=false` — signatures are stored in your private GitLab registry only. Keyless signing in CI (Challenge 6) records every signing event in Rekor's public transparency log permanently. Do not use keyless signing for internal images that contain proprietary code names, internal hostnames, or other sensitive information. Use key-based signing or a self-hosted Sigstore stack (Fulcio + Rekor) for those.

### 4. Enforce signature verification at the cluster level
A signed image provides no protection if nothing enforces the signature at deployment time. In Kubernetes, use an admission controller — Kyverno or OPA Gatekeeper — with a policy that rejects pods whose images cannot be verified. For key-based signing, the policy references `cosign.pub`. For keyless CI signing, the policy references the expected certificate identity (`project_path:YOUR_NAMESPACE/containerization-lab`) and OIDC issuer (`https://gitlab.com`). Signing without enforcement is documentation, not security.

### 5. SBOMs require a storage and query strategy
An SBOM attached to an image is useful when a CVE is published and you need to know which images are affected. But querying "which of our 10,000 images contains `libssl < 3.0.15`" requires indexing SBOMs — tools like Grype, Dependency-Track, or a registry with built-in SBOM indexing (e.g. AWS ECR, Artifact Registry). Plan the query strategy before you have the emergency.

---

## Outcome

Every image pushed to the GitLab Container Registry is: scanned for CVEs before signing, signed with a private key (locally) or a short-lived OIDC-bound certificate tied to the exact GitLab CI job identity (in CI), and accompanied by a CycloneDX SBOM and a provenance attestation. Local signing keeps all signature data in the private registry. CI signing records the event in a tamper-evident transparency log. Any image in the registry can be verified in under 30 seconds with `cosign verify` using either the public key or the expected GitLab CI certificate identity.

---

[Back to Phase 4](../phase-4-buildkit/README.md) | [Next: Phase 6 — Registry & Image Lifecycle Management →](../phase-6-registry/README.md)
