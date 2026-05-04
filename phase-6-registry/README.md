# Phase 6 — Registry & Image Lifecycle Management

> **Concepts introduced:** Tagging strategy, image promotion, `crane`, `skopeo`, GitLab Container Registry cleanup policies, multi-arch manifest inspection
>
> **CI/CD:** GitLab CI (`.gitlab-ci.yml`)

---

## Concepts introduced

| Concept | What it is | Why it matters |
|---|---|---|
| **Tagging strategy** | The scheme for naming image versions in a registry | Mutable tags (`:latest`) mislead; immutable tags (`:sha-abc123`) make deployments reproducible |
| **Image promotion** | Moving the same image digest from one tag/repo to another without a rebuild | The same bytes go from dev → staging → prod — no "works in staging" surprises |
| **`crane`** | A CLI for interacting with container registries | Copy, list, delete, and inspect images without pulling them to disk |
| **`skopeo`** | An alternative CLI for registry inspection and copying | Works without a Docker daemon; great for CI and policy auditing |
| **Cleanup policy** | Rules that automatically delete old images from a registry | Without policies, tag counts grow unbounded and storage costs compound |
| **Multi-arch manifest** | A manifest list pointing to per-platform image manifests | `docker pull` automatically serves the right variant for the host architecture |

---

## The problem

> *Nexio — 40 engineers. Six months in.*
>
> The platform team opened the GitLab Container Registry storage page for the first time in six months.
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
  Every push to any branch:
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
  Build once → push :sha-a1b2c3d to registry (dev path)
  Test passes → crane copy :sha-a1b2c3d to staging path
  Staging passes → crane copy :sha-a1b2c3d to prod path

  Same digest at every stage. No rebuilds. No "works in staging" surprises.

  registry.gitlab.com/YOUR_NAMESPACE/containerization-lab/nexio-api-dev:sha-a1b2c3d
          ↓  crane copy (no bytes transferred to your machine)
  registry.gitlab.com/YOUR_NAMESPACE/containerization-lab/nexio-api-staging:sha-a1b2c3d
          ↓  crane copy
  registry.gitlab.com/YOUR_NAMESPACE/containerization-lab/nexio-api:sha-a1b2c3d


Cleanup policy (GitLab built-in)
────────────────────────────────────────────────────────────────────
  Keep: tags matching v*.*.* (semver releases)
  Keep: tags matching main (latest main branch build)
  Delete: sha-* tags older than 90 days
  Delete: untagged (dangling) manifests older than 7 days
  Schedule: daily at 01:00 UTC
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

This phase is primarily about registry tooling and lifecycle policy, not Dockerfile changes.

---

## Challenge 1 — Understand the tagging problem

### Step 1: Demonstrate the `:latest` problem

```bash
# Build the image
docker build -t nexio-api:latest phase-6-registry/app/
docker inspect nexio-api:latest --format '{{.Id}}'
# sha256:aaaa...

# Rebuild with --no-cache (simulates a new commit changing nothing visible)
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

# In promotion, you copy the digest — not image bytes — to a new tag
# crane copy src:tag dst:tag  (see Challenge 3)
```

---

## Challenge 2 — Push to the GitLab Container Registry with a proper tagging strategy

### Step 1: Authenticate

The GitLab Container Registry uses your GitLab credentials. Use the PAT you created in Phase 5 (scopes: `read_registry`, `write_registry`). If you need a new one, follow the same steps as Phase 5 Challenge 3 Step 2.

```bash
export GL_PAT=glpat-xxxxxxxxxxxxxxxxxxxx

echo $GL_PAT | docker login registry.gitlab.com \
  -u YOUR_GITLAB_USERNAME \
  --password-stdin
# Login Succeeded
```

### Step 2: Tag and push with all three tag types

```bash
REGISTRY=registry.gitlab.com/YOUR_NAMESPACE/containerization-lab
SHORT_SHA=$(git rev-parse --short HEAD 2>/dev/null || echo "dev")
VERSION="0.6.0"

# Build once
docker build \
  --build-arg APP_VERSION=$SHORT_SHA \
  -t $REGISTRY/nexio-api:sha-$SHORT_SHA \
  phase-6-registry/app/

# Tag the same image with additional names — no rebuild, no extra storage
docker tag $REGISTRY/nexio-api:sha-$SHORT_SHA $REGISTRY/nexio-api:latest
docker tag $REGISTRY/nexio-api:sha-$SHORT_SHA $REGISTRY/nexio-api:v$VERSION

# Push all three
docker push $REGISTRY/nexio-api:sha-$SHORT_SHA
docker push $REGISTRY/nexio-api:latest
docker push $REGISTRY/nexio-api:v$VERSION
```

All three tags point to the **same image digest** — different names for the same bytes. The registry stores the manifest once and creates tag references.

### Step 3: Verify in the GitLab UI

Navigate to your project in GitLab → **Deploy → Container Registry**.

You will see `nexio-api` with three tags. Click into the image to confirm all three tags share the same digest.

### Step 4: Verify with the CLI

```bash
REGISTRY=registry.gitlab.com/YOUR_NAMESPACE/containerization-lab
SHORT_SHA=$(git rev-parse --short HEAD 2>/dev/null || echo "dev")

D1=$(crane digest $REGISTRY/nexio-api:sha-$SHORT_SHA)
D2=$(crane digest $REGISTRY/nexio-api:latest)
D3=$(crane digest $REGISTRY/nexio-api:v0.6.0)

echo "sha tag: $D1"
echo "latest:  $D2"
echo "semver:  $D3"
[ "$D1" = "$D2" ] && [ "$D1" = "$D3" ] && echo "ALL SAME" || echo "MISMATCH"
```

---

## Challenge 3 — Inspect and manage images with `crane`

`crane` is a lightweight CLI for registry operations. It works without a Docker daemon — useful in CI and in environments where Docker is not running.

### Step 1: Install crane

```bash
# macOS
brew install crane

# Linux
curl -sL https://github.com/google/go-containerregistry/releases/latest/download/go-containerregistry_Linux_x86_64.tar.gz \
  | tar -xz -C /usr/local/bin crane
```

Authenticate crane with the same PAT:

```bash
crane auth login registry.gitlab.com \
  -u YOUR_GITLAB_USERNAME \
  -p $GL_PAT
```

### Step 2: List all tags for an image

```bash
REGISTRY=registry.gitlab.com/YOUR_NAMESPACE/containerization-lab
crane ls $REGISTRY/nexio-api
```

Expected output:
```
latest
sha-a1b2c3d
v0.6.0
```

### Step 3: Inspect the manifest without pulling

```bash
SHORT_SHA=$(git rev-parse --short HEAD 2>/dev/null || echo "dev")
crane manifest $REGISTRY/nexio-api:sha-$SHORT_SHA | jq '{
  mediaType: .mediaType,
  digest: .config.digest,
  size: .config.size,
  layers: (.layers | length)
}'
```

### Step 4: Simulate image promotion with `crane copy`

`crane copy` transfers manifest and layers directly between registry paths — the image bytes never touch your machine.

```bash
# Simulate promoting from the dev path to a staging path
crane copy \
  $REGISTRY/nexio-api:sha-$SHORT_SHA \
  $REGISTRY/nexio-api-staging:sha-$SHORT_SHA

# Confirm the staging path now exists
crane ls $REGISTRY/nexio-api-staging
```

The digest is identical — same image, new registry path. This is how you guarantee staging and production run exactly what was tested.

### Step 5: Delete a tag

```bash
# Delete the staging tag after testing
crane delete $REGISTRY/nexio-api-staging:sha-$SHORT_SHA
```

Deletes only the tag reference. The underlying manifest remains if other tags still point to it.

---

## Challenge 4 — Inspect multi-arch manifests

A multi-arch manifest (built with `docker buildx` in Phase 4) is a manifest list — it points to per-platform image manifests rather than to an image directly.

### Step 1: Inspect a multi-arch manifest

```bash
REGISTRY=registry.gitlab.com/YOUR_NAMESPACE/containerization-lab
crane manifest $REGISTRY/nexio-api:sha-$SHORT_SHA | jq '.'
```

If the image was built with `--platform linux/amd64,linux/arm64`, the output is a manifest list:

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
  $REGISTRY/nexio-api:sha-$SHORT_SHA | jq '{
  layers: [.layers[] | {digest: .digest, size: .size}]
}'
```

---

## Challenge 5 — Inspect the registry with `skopeo`

`skopeo` complements `crane` — particularly useful for copying between registries with different authentication schemes and for inspecting raw image metadata.

### Step 1: Install skopeo

```bash
# macOS
brew install skopeo

# Ubuntu
sudo apt-get install skopeo
```

### Step 2: Inspect image metadata

```bash
REGISTRY=registry.gitlab.com/YOUR_NAMESPACE/containerization-lab
SHORT_SHA=$(git rev-parse --short HEAD 2>/dev/null || echo "dev")

skopeo inspect \
  --creds YOUR_GITLAB_USERNAME:$GL_PAT \
  --override-os linux \
  --override-arch amd64 \
  docker://$REGISTRY/nexio-api:sha-$SHORT_SHA
```

> **macOS note:** without `--override-os linux --override-arch amd64`, skopeo attempts to find a `darwin/arm64` variant in the manifest index and fails with `no image found in image index for architecture "arm64", variant "v8", OS "darwin"`. The flags tell it to inspect the `linux/amd64` manifest instead, which is what you actually want to audit.

Returns image labels, environment variables, created timestamp, and layer digests — without pulling the image.

### Step 3: Compare two image digests

```bash
D1=$(skopeo inspect \
  --creds YOUR_GITLAB_USERNAME:$GL_PAT \
  --override-os linux --override-arch amd64 \
  --format '{{.Digest}}' \
  docker://$REGISTRY/nexio-api:sha-$SHORT_SHA)

D2=$(skopeo inspect \
  --creds YOUR_GITLAB_USERNAME:$GL_PAT \
  --override-os linux --override-arch amd64 \
  --format '{{.Digest}}' \
  docker://$REGISTRY/nexio-api:latest)

echo "sha-tag: $D1"
echo "latest:  $D2"
[ "$D1" = "$D2" ] && echo "SAME IMAGE" || echo "DIFFERENT IMAGES"
```

This verifies that `:latest` and your SHA tag point to the same bytes after a push — before you promote to staging.

---

## Challenge 6 — Configure a cleanup policy on the GitLab Container Registry

GitLab has a built-in container registry cleanup policy — no external tool or nightly GitHub Action required. It runs on a configurable schedule and deletes tags matching your rules.

### Step 1: Enable the cleanup policy via the GitLab UI

1. Go to your project in GitLab
2. Navigate to **Settings → Packages & Registries → Container Registry**
3. Under **Clean up image tags**, click **Set cleanup rules**
4. Configure:

   | Setting | Value | Why |
   |---------|-------|-----|
   | Enable cleanup policy | On | |
   | Run cleanup | Every day | |
   | Remove tags older than | 90 days | Retains recent SHA tags |
   | Remove tags matching | `sha-.*` | Targets CI-generated tags |
   | Keep the most recent | 10 | Safety net |
   | Keep tags matching | `v\d+\.\d+\.\d+` | Preserve semver releases |
   | Keep tags matching | `main` | Preserve latest main build |

5. Click **Save**

GitLab runs the cleanup job on the schedule you set. No CI job required.

### Step 2: Configure the same policy via the GitLab API

For infrastructure-as-code and reproducibility, apply the same policy via the API:

```bash
# Replace YOUR_PROJECT_ID with the numeric project ID (visible on the project home page)
PROJECT_ID=12345678

curl --request PUT \
  --header "PRIVATE-TOKEN: $GL_PAT" \
  --header "Content-Type: application/json" \
  --data '{
    "container_expiration_policy_attributes": {
      "enabled": true,
      "cadence": "1d",
      "older_than": "90d",
      "keep_n": 10,
      "name_regex": "sha-.*",
      "name_regex_keep": "v\\d+\\.\\d+\\.\\d+|main"
    }
  }' \
  "https://gitlab.com/api/v4/projects/$PROJECT_ID"
```

Verify the policy was applied:

```bash
curl --header "PRIVATE-TOKEN: $GL_PAT" \
  "https://gitlab.com/api/v4/projects/$PROJECT_ID" \
  | jq '.container_expiration_policy'
```

### Step 3: Manually trigger cleanup on demand

```bash
# Trigger an immediate cleanup run via the API
curl --request POST \
  --header "PRIVATE-TOKEN: $GL_PAT" \
  "https://gitlab.com/api/v4/projects/$PROJECT_ID/registry/repositories"
```

Or from the UI: **Deploy → Container Registry → image name → kebab menu → Delete tags by tag name pattern**.

### Step 4: List and manually delete untagged manifests with crane

Untagged manifests (dangling digests) are created when you push a new image under an existing tag. They have no human-readable name — just a `sha256:` reference:

```bash
REGISTRY=registry.gitlab.com/YOUR_NAMESPACE/containerization-lab

# Untagged manifests show up as sha256: entries in crane ls
crane ls $REGISTRY/nexio-api | grep "^sha256:"
```

Delete one manually:

```bash
crane delete $REGISTRY/nexio-api@sha256:FULL_DIGEST_HERE
```

The GitLab cleanup policy handles these automatically once configured.

---

## Challenge 7 — Promotion pipeline in GitLab CI

The manual `crane copy` steps in Challenge 3 can be automated as a GitLab CI pipeline with environment-gated promotion.

Add this job to your `.gitlab-ci.yml`:

```yaml
variables:
  REGISTRY: registry.gitlab.com/$CI_PROJECT_NAMESPACE/$CI_PROJECT_NAME
  IMAGE_DEV: $REGISTRY/nexio-api-dev
  IMAGE_STAGING: $REGISTRY/nexio-api-staging
  IMAGE_PROD: $REGISTRY/nexio-api

stages:
  - build
  - promote-staging
  - promote-prod

build:
  stage: build
  image: docker:26
  services:
    - docker:26-dind
  before_script:
    - docker login -u $CI_REGISTRY_USER -p $CI_REGISTRY_PASSWORD $CI_REGISTRY
  script:
    - |
      docker buildx build \
        --platform linux/amd64 \
        --build-arg APP_VERSION=$CI_COMMIT_SHORT_SHA \
        --build-arg BUILD_DATE=$(date -u +%Y-%m-%dT%H:%M:%SZ) \
        -t $IMAGE_DEV:sha-$CI_COMMIT_SHORT_SHA \
        --push \
        phase-6-registry/app/
  rules:
    - if: $CI_COMMIT_BRANCH == "main"

promote-to-staging:
  stage: promote-staging
  image: gcr.io/go-containerregistry/crane:latest
  before_script:
    - crane auth login $CI_REGISTRY -u $CI_REGISTRY_USER -p $CI_REGISTRY_PASSWORD
  script:
    - |
      crane copy \
        $IMAGE_DEV:sha-$CI_COMMIT_SHORT_SHA \
        $IMAGE_STAGING:sha-$CI_COMMIT_SHORT_SHA
  environment:
    name: staging
  needs: [build]
  rules:
    - if: $CI_COMMIT_BRANCH == "main"

promote-to-prod:
  stage: promote-prod
  image: gcr.io/go-containerregistry/crane:latest
  before_script:
    - crane auth login $CI_REGISTRY -u $CI_REGISTRY_USER -p $CI_REGISTRY_PASSWORD
  script:
    - |
      crane copy \
        $IMAGE_STAGING:sha-$CI_COMMIT_SHORT_SHA \
        $IMAGE_PROD:sha-$CI_COMMIT_SHORT_SHA
      # Also update the semver tag if this is a release
      if [ -n "$CI_COMMIT_TAG" ]; then
        crane tag $IMAGE_PROD:sha-$CI_COMMIT_SHORT_SHA $CI_COMMIT_TAG
      fi
  environment:
    name: production
  needs: [promote-to-staging]
  when: manual          # requires explicit approval to promote to prod
  rules:
    - if: $CI_COMMIT_BRANCH == "main"
```

Key points:
- `CI_REGISTRY`, `CI_REGISTRY_USER`, `CI_REGISTRY_PASSWORD` are injected automatically by GitLab — no secrets to configure
- `promote-to-prod` is `when: manual` — someone must click **Play** in the pipeline UI before the image reaches production
- `crane copy` between paths in the same registry is near-instant (manifest copy, no layer transfer)

### Protect semver tags in GitLab

Prevent `v*.*.*` tags from being force-pushed or deleted:

1. Go to **Settings → Repository → Protected tags**
2. Add pattern: `v*.*.*`
3. Set **Allowed to create**: Maintainers
4. Save

Once `v1.2.3` is pushed, it cannot be overwritten — immutable by policy, not just convention.

---

## Command reference

| Command | What it does |
|---|---|
| `crane auth login registry.gitlab.com -u USER -p TOKEN` | Authenticate crane with the GitLab registry |
| `crane ls registry/image` | List all tags for an image |
| `crane manifest registry/image:tag` | Inspect the manifest without pulling |
| `crane digest registry/image:tag` | Get the content digest for a tag |
| `crane copy src:tag dst:tag` | Copy image between registries/paths (server-to-server) |
| `crane tag registry/image:tag newtag` | Add a new tag to an existing image |
| `crane delete registry/image:tag` | Delete a tag |
| `crane delete registry/image@sha256:digest` | Delete an untagged manifest |
| `skopeo inspect --creds USER:TOKEN --override-os linux --override-arch amd64 docker://registry/image:tag` | Inspect image metadata (macOS-safe) |
| `skopeo copy docker://src docker://dst` | Copy with full authentication support |

---

## Production considerations

### 1. Always deploy by digest in production
`image: nexio-api:v1.2.3` can be rewritten. `image: nexio-api@sha256:abc123` cannot. In Kubernetes, pin pod specs to digests. The tag is a human-readable alias — the digest is the contract.

### 2. Enable the GitLab cleanup policy on day one
Storage costs compound silently. GitLab's built-in cleanup policy requires no external tooling — enable it when you create the registry, not after 4,000 tags have accumulated. Start conservative (keep 10, delete after 90 days) and tighten as your team builds confidence.

### 3. Never use `:latest` in a deployment script, runbook, or incident playbook
`:latest` at incident time means you do not know which image is running. Every deployment command in every document should reference an immutable identifier. Update your runbooks as part of this phase.

### 4. Use separate registry paths for dev, staging, and production
`nexio-api-dev`, `nexio-api-staging`, `nexio-api` within the same GitLab project registry. Access tokens or CI variables restrict who can push to the prod path. `crane copy` is the only way to move an image from staging to prod — not a new build, not a manual push.

### 5. Protect semver tags
Set a protected tag rule (`v*.*.*`) in **Settings → Repository → Protected tags** so that once `v1.2.3` is pushed, it cannot be overwritten or deleted. Immutable by policy, not by convention.

---

## Outcome

Every image in the GitLab Container Registry is tagged with a short commit SHA (immutable) and optionally a semver version (human-readable). Deployments reference SHA digests. Promotions use `crane copy` — no rebuilds, the same bytes move from dev to staging to prod via a GitLab CI pipeline with a manual gate before production. A daily GitLab cleanup policy removes untagged manifests and SHA tags older than 90 days. Storage costs are predictable.

---

[Back to Phase 5](../phase-5-scanning-signing/README.md) | [Next: Phase 7 — Runtime Security & Hardening →](../phase-7-runtime-security/README.md)
