# Phase 6 — Registry & Image Lifecycle Management

> **Concepts introduced:** Tagging strategy, image promotion, `crane`, `skopeo`, registry retention policies, multi-arch manifest inspection

---

## Concepts introduced

| Concept | What it is | Why it matters |
|---|---|---|
| **Tagging strategy** | The scheme for naming image versions in a registry | Mutable tags (`:latest`) mislead; immutable tags (`:sha-abc123`) make deployments reproducible |
| **Image promotion** | Moving the same image digest from one tag/repo to another | The same bytes go from dev → staging → prod without a rebuild |
| **`crane`** | A CLI for interacting with container registries | Copy, list, delete, and inspect images without pulling them to disk |
| **`skopeo`** | An alternative CLI for registry inspection and copying | Works without a Docker daemon; great for CI and policy auditing |
| **Retention policy** | Rules that automatically delete old images from a registry | Without policies, tag counts grow unbounded and storage costs compound |
| **Multi-arch manifest** | A manifest list pointing to per-platform image manifests | `docker pull` automatically serves the right variant for the host architecture |

---

## The problem

> *Nexio — 40 engineers. Six months in.*
>
> The platform team opened the GHCR billing dashboard for the first time in six months.
>
> 4,312 image tags. €290/month in storage.
>
> `nexio-api:latest` existed — and 4,311 other tags did too: one per commit, per branch, per failed CI run. Nobody had ever deleted one. The `:latest` tag had been pushed 847 times, pointing to a different image each time. Three incident postmortems referenced `:latest` in the deployment command.
>
> *"Which `:latest`? The one from 2 hours ago? The one from last Tuesday?"*
>
> The decision: a tagging strategy, a retention policy, and a promotion workflow. From that day, every deployment referenced an immutable SHA digest. `:latest` was never used in a deployment again.

---

## Architecture

```
Tagging strategy
────────────────────────────────────────────────────────────────────
  Every push:
    nexio-api:sha-a1b2c3d    ← immutable, pinned to exact bytes
                                Use this in deployment manifests

  Push to main:
    nexio-api:latest          ← mutable, developer convenience only
                                Never use in deployments

  Semver release tag (v1.2.3):
    nexio-api:v1.2.3          ← mutable tag, immutable content (by convention)
    nexio-api:1.2             ← floating minor (optional)


Promotion workflow
────────────────────────────────────────────────────────────────────
  Build once → push :sha-a1b2c3d to dev registry
  Test passes → crane copy :sha-a1b2c3d to staging registry
  Staging passes → crane copy :sha-a1b2c3d to prod registry

  Same digest at every stage. No rebuilds. No "works in staging" surprises.


Retention policy
────────────────────────────────────────────────────────────────────
  Keep: last 10 tags per image
  Keep: any tag matching v*.*.* (semver releases)
  Delete: untagged (dangling) manifests older than 7 days
  Delete: sha-* tags older than 90 days
```

---

## Repository structure

```
phase-6-registry/
└── app/
    ├── Dockerfile        ← same as Phase 5
    ├── .dockerignore
    ├── app.py
    └── requirements.txt
```

This phase is primarily about registry tooling, not Dockerfile changes.

---

## Challenge 1 — Understand the tagging problem

### Step 1: Demonstrate the `:latest` problem

```bash
# Push two different images under :latest
docker build -t nexio-api:latest phase-6-registry/app/
docker inspect nexio-api:latest --format '{{.Id}}'
# sha256:aaaa...

# Make a trivial change — add a space to a comment in app.py
# (or just rebuild with --no-cache)
docker build --no-cache -t nexio-api:latest phase-6-registry/app/
docker inspect nexio-api:latest --format '{{.Id}}'
# sha256:bbbb...  ← different ID, same tag
```

`:latest` now points to a different image. Any script that recorded `nexio-api:latest` in a deployment log is now ambiguous — it cannot tell you which image actually ran.

### Step 2: Build with an immutable SHA tag

```bash
SHORT_SHA=$(git rev-parse --short HEAD 2>/dev/null || echo "dev")
docker build \
  --build-arg APP_VERSION=$SHORT_SHA \
  --build-arg BUILD_DATE=$(date -u +%Y-%m-%dT%H:%M:%SZ) \
  -t nexio-api:sha-$SHORT_SHA \
  phase-6-registry/app/

docker images nexio-api
```

`sha-a1b2c3d` is immutable by construction: if the code changes, the SHA changes, so the tag changes. You cannot accidentally overwrite it.

### Step 3: Understand the promotion model

```bash
# Check what digest the SHA tag resolves to
docker inspect nexio-api:sha-$SHORT_SHA --format '{{.Id}}'

# In promotion, you copy the digest — not the image bytes — to a new tag
# crane copy src:tag dst:tag  (see Challenge 3)
```

---

## Challenge 2 — Push to GHCR with a proper tagging strategy

### Step 1: Log in to GHCR

```bash
echo $GITHUB_TOKEN | docker login ghcr.io -u YOUR_GITHUB_USERNAME --password-stdin
```

### Step 2: Tag and push with all three tag types

```bash
REGISTRY=ghcr.io/YOUR_USERNAME
SHORT_SHA=$(git rev-parse --short HEAD 2>/dev/null || echo "dev")
VERSION="0.6.0"

# Build once
docker build \
  --build-arg APP_VERSION=$SHORT_SHA \
  -t $REGISTRY/nexio-api:sha-$SHORT_SHA \
  phase-6-registry/app/

# Tag the same image with additional names (no rebuild)
docker tag $REGISTRY/nexio-api:sha-$SHORT_SHA $REGISTRY/nexio-api:latest
docker tag $REGISTRY/nexio-api:sha-$SHORT_SHA $REGISTRY/nexio-api:v$VERSION

# Push all three
docker push $REGISTRY/nexio-api:sha-$SHORT_SHA
docker push $REGISTRY/nexio-api:latest
docker push $REGISTRY/nexio-api:v$VERSION
```

All three tags point to the **same image digest** — they are different names for the same bytes. No storage is duplicated.

### Step 3: Verify in the registry

```bash
docker manifest inspect $REGISTRY/nexio-api:sha-$SHORT_SHA | jq '.schemaVersion'
docker manifest inspect $REGISTRY/nexio-api:latest | jq '.schemaVersion'
```

Both manifests should resolve to the same `sha256` content digest.

---

## Challenge 3 — Inspect and manage images with `crane`

`crane` is a lightweight CLI for registry operations. It works without a Docker daemon — useful in environments where Docker is not installed, and faster than pulling images for inspection.

### Step 1: Install crane

```bash
# macOS
brew install crane

# Linux
curl -sL https://github.com/google/go-containerregistry/releases/latest/download/go-containerregistry_Linux_x86_64.tar.gz \
  | tar -xz -C /usr/local/bin crane
```

### Step 2: List all tags for an image

```bash
crane ls ghcr.io/YOUR_USERNAME/nexio-api
```

Expected output:
```
latest
sha-a1b2c3d
v0.6.0
```

### Step 3: Inspect the manifest without pulling

```bash
crane manifest ghcr.io/YOUR_USERNAME/nexio-api:sha-$SHORT_SHA | jq '{
  mediaType: .mediaType,
  digest: .config.digest,
  size: .config.size,
  layers: (.layers | length)
}'
```

### Step 4: Copy an image between registries (promotion)

```bash
# Promote from dev registry to a staging repository — no pull, no push of bytes
crane copy \
  ghcr.io/YOUR_USERNAME/nexio-api:sha-$SHORT_SHA \
  ghcr.io/YOUR_USERNAME/nexio-api-staging:sha-$SHORT_SHA
```

`crane copy` transfers the manifest and layers directly between registries server-to-server. The image is never downloaded to your machine. This is how immutable image promotion works in practice.

### Step 5: Delete a tag

```bash
crane delete ghcr.io/YOUR_USERNAME/nexio-api:sha-oldhash
```

Deletes only the tag — not the underlying manifest, if other tags still reference it.

---

## Challenge 4 — Inspect multi-arch manifests

A multi-arch manifest (built with `docker buildx` in Phase 4) is a manifest list — it points to per-platform image manifests, not to an image directly.

### Step 1: Inspect a multi-arch manifest

```bash
crane manifest ghcr.io/YOUR_USERNAME/nexio-api:sha-$SHORT_SHA | jq '.'
```

If the image was built with `--platform linux/amd64,linux/arm64`, the output will be a manifest list:

```json
{
  "mediaType": "application/vnd.oci.image.index.v1+json",
  "manifests": [
    {
      "mediaType": "application/vnd.oci.image.manifest.v1+json",
      "digest": "sha256:amd64digest...",
      "platform": { "architecture": "amd64", "os": "linux" }
    },
    {
      "mediaType": "application/vnd.oci.image.manifest.v1+json",
      "digest": "sha256:arm64digest...",
      "platform": { "architecture": "arm64", "os": "linux" }
    }
  ]
}
```

### Step 2: Inspect a specific platform's manifest

```bash
crane manifest --platform linux/amd64 \
  ghcr.io/YOUR_USERNAME/nexio-api:sha-$SHORT_SHA | jq '{
  layers: [.layers[] | {digest: .digest, size: .size}]
}'
```

---

## Challenge 5 — Inspect the registry with `skopeo`

`skopeo` is a complementary tool to `crane` — particularly useful for copying between registries with different authentication schemes, and for inspecting images without pulling.

### Step 1: Install skopeo

```bash
# macOS
brew install skopeo

# Ubuntu
sudo apt-get install skopeo
```

### Step 2: Inspect image metadata

```bash
skopeo inspect docker://ghcr.io/YOUR_USERNAME/nexio-api:sha-$SHORT_SHA
```

Returns image labels, environment variables, created timestamp, and layer digests — without pulling the image.

### Step 3: Compare two image digests

```bash
# Get digest of sha tag
D1=$(skopeo inspect docker://ghcr.io/YOUR_USERNAME/nexio-api:sha-$SHORT_SHA \
  --format '{{.Digest}}')

# Get digest of latest
D2=$(skopeo inspect docker://ghcr.io/YOUR_USERNAME/nexio-api:latest \
  --format '{{.Digest}}')

echo "sha-tag: $D1"
echo "latest:  $D2"
[ "$D1" = "$D2" ] && echo "SAME IMAGE" || echo "DIFFERENT IMAGES"
```

This is how you verify that `:latest` and your SHA tag point to the same bytes after a push.

---

## Challenge 6 — Configure a retention policy on GHCR

GHCR (and most registries) accumulate untagged manifests — digests that are no longer referenced by any tag. These are "dangling" manifests, created when you overwrite a tag with a new build. They consume storage and serve no purpose.

### Step 1: List untagged manifests

```bash
# List all manifests and find those with no tags
crane ls ghcr.io/YOUR_USERNAME/nexio-api | grep "^sha256:"
# These are untagged manifests — no human-readable name, but they take up storage
```

### Step 2: Delete old SHA tags with crane

```bash
# List all sha- tags older than a threshold and delete them
# (In production, run this on a schedule in CI)
crane ls ghcr.io/YOUR_USERNAME/nexio-api \
  | grep "^sha-" \
  | head -n -10 \
  | xargs -I{} crane delete ghcr.io/YOUR_USERNAME/nexio-api:{}
```

Keep the last 10 SHA tags; delete the rest.

### Step 3: Set GHCR retention via the API (automated)

GHCR does not yet have a built-in UI for retention policies (unlike AWS ECR or Artifact Registry). Use the GitHub API to delete old versions on a schedule:

```bash
# List all package versions for an image
gh api \
  /user/packages/container/nexio-api/versions \
  --jq '.[] | {id: .id, name: .name, tags: .metadata.container.tags, updated: .updated_at}' \
  | head -20
```

In GitHub Actions, run a nightly workflow that calls this API to delete untagged versions older than 7 days:

```yaml
- name: Delete old untagged image versions
  uses: actions/delete-package-versions@v5
  with:
    package-name: nexio-api
    package-type: container
    min-versions-to-keep: 10
    delete-only-untagged-versions: true
```

---

## Command reference

| Command | What it does |
|---|---|
| `crane ls registry/image` | List all tags for an image |
| `crane manifest registry/image:tag` | Inspect the manifest without pulling |
| `crane copy src:tag dst:tag` | Copy image between registries (server-to-server) |
| `crane delete registry/image:tag` | Delete a tag |
| `crane digest registry/image:tag` | Get the content digest for a tag |
| `skopeo inspect docker://registry/image:tag` | Inspect image metadata |
| `skopeo copy docker://src docker://dst` | Copy with authentication support |

---

## Production considerations

### 1. Always deploy by digest in production
`image: nexio-api:v1.2.3` can be rewritten. `image: nexio-api@sha256:abc123` cannot. In Kubernetes, pin pod specs to digests. The tag is a human-readable alias — the digest is the contract.

### 2. Automate tag cleanup as part of the release process
Storage costs compound silently. A nightly cleanup job is not optional — it is infrastructure hygiene. Treat old image tags like old log files: retain what you need, delete what you don't, on a schedule.

### 3. Never use `:latest` in a deployment script, runbook, or incident playbook
`:latest` at incident time means you do not know which image is running. Every deployment command in every document should reference an immutable identifier. Update your runbooks as part of this phase.

### 4. Use separate repositories for dev, staging, and production
`nexio-api-dev`, `nexio-api-staging`, `nexio-api` in GHCR. IAM policies restrict who can push to prod. Promotion (`crane copy`) is the only way to move an image from staging to prod — not a new build, not a manual push.

### 5. Protect semver tags
On GitHub, set a branch/tag protection rule that prevents force-pushing to `v*.*.*` tags. Once `v1.2.3` is pushed, it should be immutable by policy — not just by convention.

---

## Outcome

Every image in the registry is tagged with a short commit SHA (immutable) and optionally a semver version (human-readable). Deployments reference SHA digests. Promotions use `crane copy` — no rebuilds, the same bytes move from dev to staging to prod. A nightly job removes untagged manifests and old SHA tags. Storage costs are predictable.

---

[Back to Phase 5](../phase-5-scanning-signing/README.md) | [Next: Phase 7 — Runtime Security & Hardening →](../phase-7-runtime-security/README.md)
