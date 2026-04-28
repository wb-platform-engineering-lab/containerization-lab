# Phase 5 — Container Security Scanning & Signing

> **Concepts introduced:** SBOM, CVE scanning in CI, Cosign, Sigstore keyless signing, provenance attestation, `docker buildx --attest`

---

## Concepts introduced

| Concept | What it is | Why it matters |
|---|---|---|
| **SBOM** | Software Bill of Materials — a machine-readable inventory of every component in an image | Regulatory compliance (EO 14028), supply chain auditing, rapid CVE response |
| **CVE scanning in CI** | Trivy runs as a CI step and fails the pipeline on critical findings | Shifts security left — vulnerabilities caught before they reach a registry |
| **Cosign** | A tool for signing and verifying container images | Cryptographic proof that an image came from your pipeline and hasn't been tampered with |
| **Sigstore / keyless signing** | Cosign uses a short-lived certificate bound to a CI identity (OIDC token) instead of a long-lived private key | No key management, no key rotation, no key leakage |
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
> - Signed with Cosign using a keyless OIDC certificate tied to the GitHub Actions workflow identity
> - Accompanied by an SBOM in CycloneDX format
> - Accompanied by a provenance attestation documenting the exact repo, commit, and workflow that produced it
>
> Any auditor — internal or external — could verify any image in under 30 seconds.

---

## Architecture

```
Developer pushes to main
│
└── GitHub Actions: .github/workflows/scan-sign.yml
    │
    ├── 1. Build (docker buildx build)
    │       ├── --attest type=provenance → provenance manifest attached to image
    │       └── --attest type=sbom      → SBOM manifest attached to image
    │
    ├── 2. Push to GHCR
    │       ghcr.io/org/nexio-api:sha-a1b2c3
    │
    ├── 3. Scan (Trivy)
    │       ├── CVE scan → SARIF uploaded to GitHub Security tab
    │       └── EXIT CODE 1 on CRITICAL/HIGH → pipeline fails before signing
    │
    └── 4. Sign (Cosign — keyless)
            ├── GitHub OIDC token → Fulcio CA → short-lived certificate
            ├── Certificate proves: this signature was created by
            │   github.com/org/repo, workflow=scan-sign.yml, ref=main
            └── Signature stored in Rekor transparency log (public, tamper-evident)


Verification (anyone, anytime)
└── cosign verify --certificate-identity-regexp ... \
                  --certificate-oidc-issuer ... \
                  ghcr.io/org/nexio-api@sha256:abc123
    └── Rekor: signature found, certificate valid, identity matches → OK
```

---

## Repository structure

```
phase-5-scanning-signing/
├── .github/
│   └── workflows/
│       └── scan-sign.yml    ← full CI pipeline: build, scan, sign
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
trivy image --severity CRITICAL,HIGH --exit-code 1 nexio-api:0.4
```

`--exit-code 1` causes Trivy to exit with code 1 if any findings match the severity filter. In CI, a non-zero exit code fails the step — the image is never pushed.

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

SARIF is the standard format for uploading security results to GitHub's Security tab:

```bash
trivy image --severity CRITICAL,HIGH \
  --format sarif \
  --output trivy-results.sarif \
  nexio-api:0.4

cat trivy-results.sarif | jq '.runs[0].results | length'
# 0 — no findings
```

In CI, the SARIF file is uploaded to GitHub via `github/codeql-action/upload-sarif`. Results appear in the **Security → Code scanning** tab of the repository, grouped by severity, with links to CVE details.

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

### Step 2: Tag and push the image to a registry

Keyless signing requires the image to be in a registry (not just local). If you do not have GHCR access, use Docker Hub:

```bash
# Log in to your registry
docker login ghcr.io   # or docker login

# Tag and push
docker tag nexio-api:0.4 ghcr.io/YOUR_USERNAME/nexio-api:0.4
docker push ghcr.io/YOUR_USERNAME/nexio-api:0.4

# Capture the digest
DIGEST=$(docker inspect --format='{{index .RepoDigests 0}}' ghcr.io/YOUR_USERNAME/nexio-api:0.4)
echo $DIGEST
# ghcr.io/YOUR_USERNAME/nexio-api@sha256:abc123...
```

> **Why sign by digest, not tag?** Tags are mutable — `nexio-api:0.4` can be overwritten at any time. A digest (`sha256:abc123`) is immutable and uniquely identifies the exact bytes. Signing by digest creates an unforgeable link between the signature and those specific bytes.

### Step 3: Sign with keyless Cosign

```bash
cosign sign --yes $DIGEST
```

Cosign will:
1. Open a browser for OIDC authentication (Google, GitHub, or Microsoft identity)
2. Receive a short-lived certificate from **Fulcio** (Sigstore's certificate authority) binding your identity to the signing key
3. Create a signature and store it in the registry alongside the image
4. Record the signature in **Rekor** (Sigstore's public transparency log)

### Step 4: Verify the signature

```bash
cosign verify \
  --certificate-identity YOUR_EMAIL \
  --certificate-oidc-issuer https://accounts.google.com \
  $DIGEST
```

Expected:
```
Verification for ghcr.io/YOUR_USERNAME/nexio-api@sha256:abc123 --
The following checks were performed on each of these signatures:
  - The cosign claims were validated
  - Existence of the claims in the transparency log was verified offline
  - The code-signing certificate claims were validated

[{"critical":{"identity":{"docker-reference":"ghcr.io/..."},...}]
```

---

## Challenge 4 — Understand keyless signing and the Sigstore ecosystem

### The problem with key-based signing

Traditional image signing requires a private key. That key must be:
- Generated and stored securely (HSM, key vault)
- Rotated periodically
- Protected from leakage (if it leaks, every signature it created is suspect)
- Distributed to every verifier

Keyless signing eliminates the key.

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
  --certificate-identity-regexp "https://github.com/org/repo/.*" \
  --certificate-oidc-issuer "https://token.actions.githubusercontent.com" \
  $DIGEST
```

This means: *"accept this image only if it was signed by a GitHub Actions workflow running in this repository."*

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
  -t ghcr.io/YOUR_USERNAME/nexio-api:0.4 \
  --push \
  phase-5-scanning-signing/app/
```

| Attestation | What it contains |
|---|---|
| `type=sbom` | CycloneDX SBOM of all packages in the image |
| `type=provenance,mode=max` | Full build provenance: repo URL, commit SHA, build trigger, runner environment |

### Step 2: Inspect the attestations

```bash
cosign download attestation ghcr.io/YOUR_USERNAME/nexio-api:0.4 | \
  jq -r '.payload' | base64 -d | jq '.predicate'
```

The provenance predicate contains:
- `buildType`: the build tool (Docker Buildx)
- `invocation.configSource.uri`: the GitHub repo and commit
- `materials`: the base image digests used in the build

### Step 3: Verify the SBOM attestation

```bash
cosign verify-attestation \
  --type cyclonedx \
  --certificate-identity-regexp "https://github.com/YOUR_ORG/.*" \
  --certificate-oidc-issuer "https://token.actions.githubusercontent.com" \
  ghcr.io/YOUR_USERNAME/nexio-api:0.4
```

---

## Challenge 6 — Review the CI workflow

The workflow at `.github/workflows/scan-sign.yml` automates every step: build, scan, sign.

### Step 1: Review the workflow structure

```bash
cat phase-5-scanning-signing/.github/workflows/scan-sign.yml
```

Walk through the key sections:

**Permissions block:**
```yaml
permissions:
  contents: read
  packages: write        # push to GHCR
  id-token: write        # keyless Cosign signing via GitHub OIDC
  security-events: write # upload SARIF to GitHub Security tab
```

`id-token: write` is the critical permission for keyless signing. It allows the workflow to request a GitHub OIDC token, which Cosign uses to obtain a Fulcio certificate.

**Build step:**
```yaml
- uses: docker/build-push-action@v6
  with:
    provenance: true
    sbom: true
```

`provenance: true` and `sbom: true` generate attestation manifests inline, equivalent to `--attest type=provenance --attest type=sbom`.

**Scan step:**
```yaml
- uses: aquasecurity/trivy-action@master
  with:
    exit-code: "1"
    severity: CRITICAL,HIGH
```

If Trivy finds any CRITICAL or HIGH CVEs, the workflow exits with code 1. The signing step never runs — a vulnerable image is never signed.

**Sign step:**
```yaml
- run: |
    cosign sign --yes \
      ${{ env.REGISTRY }}/${{ env.IMAGE_NAME }}@${{ steps.build.outputs.digest }}
```

Signs by digest, not by tag. Uses the GitHub Actions OIDC token automatically — no credentials to configure.

### Step 2: What a verifier sees

Anyone with access to the image can verify it:

```bash
cosign verify \
  --certificate-identity-regexp "https://github.com/wb-platform-engineering-lab/containerization-lab/.*" \
  --certificate-oidc-issuer "https://token.actions.githubusercontent.com" \
  ghcr.io/wb-platform-engineering-lab/nexio-api@sha256:abc123
```

This answers: *"Was this image signed by our CI pipeline?"* The answer is verifiable by anyone — the signature is in the registry, the certificate is from Fulcio, and the signing event is in Rekor's public transparency log.

---

## Command reference

| Command | What it does |
|---|---|
| `trivy image name:tag` | Vulnerability scan |
| `trivy image --format cyclonedx -o sbom.json name:tag` | Generate CycloneDX SBOM |
| `trivy image --format spdx-json -o sbom.spdx.json name:tag` | Generate SPDX SBOM |
| `trivy image --format sarif -o results.sarif name:tag` | SARIF output for GitHub Security tab |
| `cosign sign --yes image@digest` | Keyless sign (keyless via OIDC) |
| `cosign verify --certificate-identity ... image@digest` | Verify a signature |
| `cosign download attestation image:tag` | Download attached attestations |
| `cosign verify-attestation --type cyclonedx ...` | Verify an SBOM attestation |
| `cosign triangulate image@digest` | Get the Rekor UUID for a signature |

---

## Production considerations

### 1. Fail-open is not acceptable — fail the pipeline on CRITICAL CVEs
A Trivy scan that only reports findings but never blocks a build provides a false sense of security. Set `--exit-code 1` on CRITICAL (and HIGH once your baseline is clean). The discomfort of a blocked pipeline is far less than the discomfort of a breach.

### 2. Sign by digest, never by tag
Tags are mutable. `nexio-api:latest` today may point to a different image tomorrow. A signature on a tag is meaningless if the tag can be reassigned. Always sign and verify by digest: `image@sha256:abc123`.

### 3. The Rekor transparency log is public
Any signature created with keyless Cosign is recorded in Rekor's public log. Do not sign internal images (containing proprietary code names, internal hostnames, or other sensitive information) with the public Rekor instance. For private deployments, run your own Sigstore stack (Fulcio + Rekor) or use Cosign with a private key.

### 4. Enforce signature verification at the cluster level
A signed image provides no protection if nothing enforces the signature at deployment time. In Kubernetes, use an admission controller — Kyverno or OPA Gatekeeper — with a policy that rejects pods whose images cannot be verified against your Cosign certificate identity. Signing without enforcement is documentation, not security.

### 5. SBOMs require a storage and query strategy
An SBOM attached to an image is useful when a CVE is published and you need to know which images are affected. But querying "which of our 10,000 images contains `libssl < 3.0.15`" requires indexing SBOMs — tools like Grype, Dependency-Track, or a registry with built-in SBOM indexing (e.g. AWS ECR, Artifact Registry). Plan the query strategy before you have the emergency.

---

## Outcome

Every image pushed to the registry is: scanned for CVEs before signing, signed with an OIDC-bound certificate tied to the exact GitHub Actions workflow that built it, and accompanied by a CycloneDX SBOM and a provenance attestation. The signing event is recorded in a public tamper-evident transparency log. Any image in the registry can be verified in 30 seconds by anyone with the registry address and the expected signing identity.

---

[Back to Phase 4](../phase-4-buildkit/README.md) | [Next: Phase 6 — Registry & Image Lifecycle Management →](../phase-6-registry/README.md)
