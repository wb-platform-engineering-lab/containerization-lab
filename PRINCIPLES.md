# DevSecOps Principles — Before You Write a Single Dockerfile

> Read this before Phase 0. The phases teach you *how* to do things. This document explains *why* — and what you should have decided before you start.

---

## What DevSecOps is (and what it isn't)

DevSecOps is the practice of integrating security into every stage of the software development lifecycle — not as a gate at the end, but as a continuous property of the system.

The name comes from merging three disciplines:

- **Dev** — building software
- **Sec** — keeping it secure
- **Ops** — running it reliably

Before DevSecOps, the workflow looked like this:

```
Develop → Develop → Develop → Develop → SECURITY REVIEW → Deploy
                                              ↑
                              (finds 47 issues, sends back to dev,
                               release delayed by 3 weeks)
```

Security was a gate. A separate team. A checklist applied once, late, under pressure. Findings were numerous, prioritisation was unclear, and developers resented the friction because it felt disconnected from their work.

DevSecOps looks like this:

```
Develop → [scan] → Develop → [scan + sign] → Develop → [scan + sign + attest] → Deploy
            ↑                      ↑                              ↑
       (finds 1 CVE,          (0 findings,                (0 findings, signed,
        fixed in 10 min)       signed, continues)          promoted to prod)
```

Security runs on every commit. Findings are small and immediate. Developers fix issues in the same context they introduced them — not three weeks later. The security team shifts from being a blocker to being a platform team that builds the tooling developers use.

**What DevSecOps is not:**

- It is not buying a security tool and integrating it into CI. Tooling is 20% of the work.
- It is not shifting all security responsibility to developers. The security team still owns the policy; developers own the implementation.
- It is not a destination. It is a practice that requires continuous improvement as threats evolve and the codebase grows.

---

## The three foundational shifts

### 1. Shift left — catch problems earlier

"Shifting left" means moving security checks earlier in the development process — before code is merged, before images are built, before they reach production.

The economics are clear:

| When found | Relative cost to fix |
|---|---|
| During development (pre-commit) | 1× |
| After merge (CI) | 6× |
| In staging | 15× |
| In production | 60× |

For containers specifically, shifting left means:
- Scanning the Dockerfile for misconfigurations before building (tools: Hadolint, Checkov)
- Scanning the built image for CVEs before pushing (Trivy)
- Verifying base image digests are pinned before merging (Renovate/Dependabot)

This lab implements scanning in CI (Phase 5) and base image pinning (Phase 1 and 3).

### 2. Shift right — observe what's actually running

Shifting left does not mean ignoring production. "Shifting right" means monitoring security posture in the running environment — because what you deployed six months ago may now have new CVEs, and what's running in production may not be what you think it is.

For containers, this means:
- Continuous rescanning of the registry (daily Trivy runs against pushed images, not just at build time)
- Runtime threat detection (Falco watching for unexpected syscalls)
- Drift detection (comparing the deployed image digest to what CI signed)

This lab covers the CI half. The runtime half (Falco, drift detection) is an extension covered in Phase 7.

### 3. Shared responsibility — security is everyone's problem

In a DevSecOps culture, security is not the security team's problem alone. It is a shared responsibility:

| Role | Responsibility |
|---|---|
| Developer | Write secure code; fix CVEs in their images; follow the policy |
| Platform/DevOps | Build and maintain the security toolchain; set the gates |
| Security team | Define policy (what severity fails a build?); audit; incident response |
| Everyone | Treat a security finding the same as a failing test — it blocks the merge |

The key cultural shift: a Trivy scan that finds a CRITICAL CVE is not a security team problem. It is the same as a failing unit test. The developer who introduced the dependency owns the fix.

---

## The eight DevSecOps principles for container pipelines

### 1. Build once, sign once, promote

Never rebuild an image for a different environment. Build exactly once, sign the artifact, then promote the same signed digest from dev → staging → production by changing only the config (Phase 9) and the registry tag (Phase 6).

Rebuilding for each environment means:
- Different transitive dependencies if a package was updated between builds
- The staging and prod images may not be byte-for-byte identical
- You cannot prove that what passed security review in staging is what you shipped to prod

**Implemented in:** Phase 4 (BuildKit), Phase 5 (signing by digest), Phase 6 (promotion via `crane copy`), Phase 9 (external config).

### 2. Every artifact is traceable

Any image running in production must be traceable back to:
- The exact source commit it was built from
- The CI pipeline that built it
- The time it was built
- The dependencies it contains (SBOM)
- Who (or what) signed it

Without this, you cannot answer the question: *"Is the image running in production the one we reviewed and approved?"*

**Implemented in:** Phase 3 (OCI labels), Phase 4 (`--build-arg APP_VERSION`), Phase 5 (SBOM + provenance attestation, Cosign signing).

### 3. Fail fast on vulnerabilities

A CVE scan that reports findings but does not fail the pipeline is a metrics dashboard, not a security control. Gates must be enforced:

- `trivy image --exit-code 1 --severity CRITICAL` — the pipeline stops
- Unsigned images rejected at the cluster admission controller
- Images above the vulnerability threshold blocked from being pulled (Harbor, Phase 6b)

Fail fast also means: define your threshold before you start scanning. If you start scanning and every pipeline fails, the team will work around it. Define what's acceptable (CRITICAL always fails; HIGH fails after a 30-day remediation period), communicate it, then enforce it.

**Implemented in:** Phase 5 (Trivy `--exit-code 1`), Phase 6b (Harbor vulnerability threshold), Phase 10 (pipeline gate ordering: scan before sign).

### 4. Least privilege everywhere

Every component — images, CI runners, container processes, registry access — should have the minimum permission required to do its job. Nothing more.

For containers:
- Images run as non-root (Phase 3)
- Containers have zero Linux capabilities unless explicitly needed (Phase 7)
- Filesystems are read-only where the app does not need to write (Phase 7)
- CI uses short-lived OIDC tokens, not long-lived service account keys (Phase 5, Phase 10)
- Registry push access scoped to specific projects/repositories

**Implemented in:** Phase 3 (non-root user), Phase 5 (keyless OIDC signing), Phase 7 (cap-drop, read-only fs, seccomp).

### 5. Security as code

Every security policy, threshold, scan configuration, and signing identity must be version-controlled. A security posture that exists only in a wiki page or a team member's memory is not a security posture — it is a risk.

For containers:
- Dockerfile linting rules in `.hadolint.yaml`
- Trivy configuration in `.trivy.yaml` or CI workflow
- Seccomp profiles in the repository (Phase 7)
- Cosign verification identity documented in the deploy manifest

If the security configuration is not in git, it cannot be reviewed, cannot be audited, and will drift.

**Implemented in:** Phase 7 (seccomp JSON in repo), Phase 10 (full pipeline in `production-pipeline.yml`).

### 6. Immutable artifacts

An image tag must not change after it is published to the registry. `nexio-api:latest` is not an artifact — it is an alias that changes with every push. `nexio-api@sha256:abc123` is an artifact — it refers to exactly one set of bytes, forever.

Implications:
- Deployments always reference a digest, never a mutable tag
- Signing is done by digest (Phases 5, 10)
- Promotion is done by copying a digest to a new tag (Phase 6)
- The SBOM and provenance attestation are attached to the digest

**Implemented in:** Phase 5 (sign by digest), Phase 6 (tagging strategy, `crane copy`).

### 7. Automate the human out of the loop

A security control that requires a human to take action is a control that will eventually be skipped — under deadline pressure, on a Friday afternoon, during an incident. Automation removes the decision point.

For containers:
- CI automatically scans every push — no manual scan step
- Harbor automatically scans every pushed image — no opt-in required
- Cosign signing is part of the pipeline, not a post-deploy action
- Retention policies run on a schedule — no manual cleanup
- Renovate/Dependabot automatically opens PRs for base image updates

**Implemented in:** Phase 5 (automated scan + sign in CI), Phase 6b (Harbor auto-scan on push), Phase 10 (full pipeline).

### 8. Defense in depth

No single security control is sufficient. A CVE scanner does not replace a read-only filesystem. Signing does not replace scanning. Runtime detection does not replace build-time security. Layer the controls:

```
Build time:   Dockerfile linting → image scanning → SBOM generation → signing
Registry:     Vulnerability threshold → pull blocking → retention
Runtime:      Non-root → no capabilities → read-only fs → seccomp → Falco
Deploy:       Admission controller verifies signature → rejects unsigned images
```

If one layer fails, the others catch it.

**Implemented across:** Phases 3, 5, 6b, 7, 10.

---

## Before you write a single Dockerfile: what to decide first

These are not technical decisions. They are organisational and architectural decisions that must be made before you build anything. Getting them wrong after the fact is expensive.

### 1. What is your threat model?

A threat model answers: *what are we protecting, from whom, and how?*

For a container pipeline, the core questions are:

| Threat | Question |
|---|---|
| Supply chain attack | Could a compromised dependency end up in production without detection? |
| Image tampering | Could someone push a modified image to the registry without detection? |
| Container escape | If an attacker achieves RCE in a container, what can they reach? |
| Credential leakage | Are secrets baked into images? Are they visible in build logs? |
| Insider threat | Can any engineer push directly to production? |

You do not need to mitigate every threat on day one. But you need to know which threats are in scope, so you can prioritise the controls that matter most for your context.

### 2. What are your compliance requirements?

Compliance requirements often dictate specific technical controls. Know them before you choose tools.

| Standard | Container-relevant requirements |
|---|---|
| **SOC 2 Type II** | Access control to registry, audit logs for image pushes, vulnerability remediation SLA |
| **ISO 27001** | Asset inventory (SBOM), change management for deployments, vulnerability management |
| **GDPR** | Data residency for images (if images contain PII-adjacent code), audit trail |
| **PCI-DSS** | Vulnerability scanning, no CRITICAL CVEs in production, access control |
| **SLSA Level 2** | Build from version-controlled source, signed provenance |
| **SLSA Level 3** | Hardened build platform, non-falsifiable provenance |

If you are subject to GDPR data residency requirements, GHCR (US-based) may not be acceptable — which means Harbor (Phase 6b) is not optional. Know this before Phase 0.

### 3. What toolchain will you standardise on?

Mixing tools creates confusion and gaps. Decide once:

| Decision | Options | This lab uses |
|---|---|---|
| CVE scanner | Trivy, Grype, Snyk, Anchore | Trivy |
| Signing | Cosign (keyless or key-based), Notary v2 | Cosign keyless |
| Registry | GHCR, ECR, GAR, Harbor, Artifactory | GHCR + Harbor |
| SBOM format | CycloneDX, SPDX | CycloneDX (primary), SPDX (secondary) |
| Base image update automation | Renovate, Dependabot, manual | Dependabot (referenced) |
| Policy enforcement | Kyverno, OPA Gatekeeper, Cosign verify | Cosign verify (referenced) |

Standardising does not mean you will never change. It means the team has one answer to "how do we scan images?" — not four.

### 4. Define your vulnerability response policy before the first scan

The first time you run Trivy on your existing images, you will find vulnerabilities. If you have not defined what to do about them, the result is paralysis or — worse — setting `--exit-code 0` so the pipeline keeps passing.

Define before you scan:

- **What severity fails the pipeline immediately?** (Recommend: CRITICAL always)
- **What is the remediation SLA for HIGH findings?** (Recommend: 30 days)
- **Who is responsible for fixing a CVE in a base image?** (Platform team)
- **Who is responsible for fixing a CVE in an app dependency?** (Service team)
- **What is the exception process?** (For CVEs with no fix available, or where the vulnerable code path is not reachable)

### 5. Design your signing trust model

Signing is only useful if verification is enforced somewhere. Before setting up Cosign:

- **Who can sign?** (Only the CI pipeline — never a developer's laptop)
- **What identity is expected?** (GitHub Actions OIDC for a specific repo and workflow)
- **Where is verification enforced?** (Kubernetes admission controller, Harbor pull policy, or both)
- **What is the rotation strategy?** (For keyless signing, certificates are ephemeral — no rotation needed. For key-based, define rotation frequency.)

Keyless signing (used in this lab) ties the signature to a CI identity — not a key that can be stolen. The trade-off is that verification requires an online check against Rekor (or a mirror). Choose keyless unless you have a specific reason to manage keys.

---

## Architecture decisions

These are the decisions that constrain every phase that follows. Make them deliberately.

### Decision 1 — Registry architecture

**The question:** Where do images live, and who can access them?

```
Option A: Single public registry (GHCR public)
  + Simple to set up
  + No authentication for pulls
  - Images are public (not acceptable for proprietary code)
  - No built-in scanning threshold enforcement
  - Subject to external rate limits

Option B: Single private registry (GHCR private, ECR, GAR)
  + Managed, no operational overhead
  + Access control via IAM
  - Data residency constraints (hosted in specific regions)
  - No built-in proxy cache

Option C: Self-hosted Harbor + external mirror
  + Full control, on-prem, any region
  + Built-in scanning + pull blocking + RBAC + proxy cache
  + Replication to public registry for external consumers
  - Operational overhead (you run it)
  - Requires dedicated infrastructure

Option D: Multi-registry (Harbor internal → ECR/GAR external)
  + Harbor is the internal source of truth + gate
  + External registry for public/cross-cloud consumption
  + Separation of concerns
  - Most complex to set up and operate
```

**This lab's choice:** GHCR for the core phases (simplicity), Harbor introduced in Phase 6b for the self-hosted, enterprise use case.

**Decision rule:** If you have data residency requirements, regulated data, or an air-gapped environment → Harbor (or Artifactory). Otherwise → a managed registry in your cloud provider is sufficient.

---

### Decision 2 — Signing trust model

**The question:** What proves an image came from your pipeline and hasn't been modified?

```
Option A: Keyless signing (Sigstore / Cosign + OIDC)
  + No private key to manage, rotate, or protect
  + Certificate is bound to CI identity (repo + workflow)
  + Signing event recorded in public Rekor transparency log
  - Verification requires internet access to Rekor (mitigable with a mirror)
  - Public transparency log means signing events are visible to anyone

Option B: Key-based signing (Cosign with a private key)
  + Works in air-gapped environments
  + No external dependency for verification
  - Private key must be stored securely (KMS, Vault) and rotated
  - Key compromise means all previously signed images are suspect

Option C: Notary v2 (TUF-based)
  + Strong supply chain trust model
  + Native support in some registries (Harbor)
  - More complex to operate than Cosign
  - Smaller ecosystem/tooling support
```

**This lab's choice:** Keyless Cosign (Phase 5, Phase 10). Suitable for most teams. Air-gapped environments should use key-based Cosign with keys stored in a KMS (AWS KMS, GCP Cloud KMS, Vault).

---

### Decision 3 — Promotion pipeline

**The question:** How does an image move from build to production?

```
Anti-pattern: Rebuild per environment
  build:dev → test → build:staging → test → build:prod → deploy
  ✗ Different bytes at each stage
  ✗ Staging approval doesn't cover prod image
  ✗ CVE found in prod may not exist in staging image (or vice versa)

Correct pattern: Build once, promote by digest
  build → sign → push(:sha-abc123) → test:dev → crane copy → test:staging → crane copy → deploy:prod
  ✓ Same bytes at every stage
  ✓ Staging approval covers exactly the prod artifact
  ✓ One SBOM, one signature, one provenance — for the artifact that ships
```

**This lab's choice:** Build once (Phase 4), sign by digest (Phase 5), promote via `crane copy` (Phase 6), config injected at runtime (Phase 9).

---

### Decision 4 — Where to enforce security policy

**The question:** Scanning finds problems. Where do you enforce that policy?

```
Layer 1 — CI pipeline (Phase 5, Phase 10)
  Trivy --exit-code 1 fails the build.
  Scope: every commit. Fast feedback.
  Gap: doesn't protect against images pushed outside CI.

Layer 2 — Registry (Phase 6b)
  Harbor blocks pulls of images above the CVE threshold.
  Scope: any pull, from any client.
  Gap: doesn't prevent the image existing in the registry.

Layer 3 — Cluster admission controller
  Kyverno or OPA Gatekeeper runs cosign verify before admitting a pod.
  Scope: any deployment to the cluster, from any source.
  Gap: none at this layer — this is the strongest enforcement point.
```

**Minimum viable:** Layer 1 (CI scan). **Production standard:** Layers 1 + 2 + 3.

Defence in depth means all three layers run independently. An image that somehow bypasses CI scan (e.g. manually pushed) is still blocked by Harbor and by the admission controller.

---

### Decision 5 — Base image management

**The question:** How do you keep base images up to date as new CVEs are published?

```
Anti-pattern: Update when someone notices
  FROM python:3.12-slim  ← 6 months old, 23 new CVEs since it was pinned
  Manual update: whenever a developer remembers, or after an incident

Correct pattern: Automated digest bumps
  FROM python:3.12-slim@sha256:abc123  ← pinned digest
  Renovate/Dependabot opens a PR when a new digest is published
  PR includes the CVE delta (new CVEs vs old digest)
  Team reviews and merges on a regular schedule (weekly or on CRITICAL)
```

**This lab's choice:** Phase 1 and Phase 3 introduce digest pinning. Renovate/Dependabot configuration is referenced but not set up (it is a repository-level configuration, not per-phase).

---

### Decision 6 — SBOM strategy

**The question:** Where do SBOMs go and how do you use them?

```
Option A: Generate + discard
  Trivy generates an SBOM in CI but doesn't store it.
  ✗ Cannot query "which images contain werkzeug < 3.0?" after the fact

Option B: Attach to image as OCI attestation (this lab)
  docker buildx --attest type=sbom attaches the SBOM to the image manifest.
  ✓ SBOM travels with the image everywhere it goes
  ✓ Verifiable via cosign verify-attestation
  ~ Querying across all images still requires tooling (Dependency-Track, etc.)

Option C: External SBOM store (Dependency-Track, Grype DB)
  SBOMs are stored centrally and indexed.
  ✓ Query "all images containing CVE-2024-XXXX" in seconds
  ✓ Continuous re-evaluation as new CVEs are published
  - Additional infrastructure to operate
```

**This lab's choice:** Option B (Phase 5, Phase 10). Option C is the right extension for teams with large numbers of images or formal vulnerability management requirements.

---

## How this lab implements each principle

| Principle | Phase(s) |
|---|---|
| Build once, sign once, promote | 4, 5, 6, 9 |
| Every artifact is traceable | 3, 4, 5 |
| Fail fast on vulnerabilities | 5, 6b, 10 |
| Least privilege everywhere | 3, 5, 7 |
| Security as code | 7, 10 |
| Immutable artifacts | 5, 6 |
| Automate the human out of the loop | 5, 6b, 10 |
| Defense in depth | 3, 5, 6b, 7, 10 |

| Architecture decision | Phase(s) |
|---|---|
| Registry architecture | 6, 6b |
| Signing trust model | 5, 10 |
| Promotion pipeline | 4, 6, 9 |
| Enforcement layers | 5, 6b, 7 |
| Base image management | 1, 3 |
| SBOM strategy | 5, 10 |

---

## Reading order

You do not need to have all of this figured out before starting Phase 0. Phase 0 is intentionally simple — a single Dockerfile with no security posture at all. The point is to have a working starting point, then improve it deliberately.

But you should have read this document before Phase 3 — because Phase 3 is where the security decisions start to matter, and the choices you make there (non-root user, labels, Trivy) set the pattern for everything that follows.

---

[Start: Phase 0 — Your First Container →](./phase-0-first-container/README.md)
