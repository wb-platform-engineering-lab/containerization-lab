# Phase 4 — BuildKit & Advanced Build Patterns

> **Concepts introduced:** `# syntax=docker/dockerfile:1`, `--mount=type=cache`, `--mount=type=secret`, `--build-arg`, `docker buildx`, multi-platform builds, remote cache

---

## Concepts introduced

| Concept | What it is | Why it matters |
|---|---|---|
| **BuildKit** | Docker's next-generation build engine | Parallel stage execution, cache mounts, secrets in build, faster builds |
| **`# syntax` directive** | Pins the BuildKit frontend version | Unlocks `RUN --mount` and other extended syntax — must be line 1 |
| **`--mount=type=cache`** | Mounts a persistent host-side cache into a `RUN` step | pip/npm/apt caches survive across builds without leaking into image layers |
| **`--mount=type=secret`** | Mounts a secret into a `RUN` step without writing it to any layer | Inject credentials at build time (private PyPI, GitHub tokens) with zero leakage |
| **`--build-arg`** | Pass a variable into the Dockerfile at build time | Inject version, commit SHA, and build date into image labels |
| **`docker buildx`** | CLI plugin for multi-platform builds via BuildKit | Build `linux/amd64` + `linux/arm64` images from a single command |
| **Remote cache** | `--cache-from` / `--cache-to` with a registry backend | Share the build cache between CI runners so every build is fast |

---

## The problem

> *Nexio — 15 engineers. Four months in.*
>
> CI had been running for two months. Every push triggered a Docker build. Every Docker build took between 12 and 18 minutes — most of it downloading and reinstalling pip dependencies from scratch.
>
> The CI runners were ephemeral. Each one started with an empty Docker layer cache. `pip install flask` downloaded the same 9 MB tarball on every single run, across every runner, across every branch.
>
> The infrastructure cost was measurable. The developer frustration was louder.
>
> The lead engineer pulled up the BuildKit documentation.
>
> ```bash
> RUN --mount=type=cache,target=/root/.cache/pip \
>     pip install --prefix=/install -r requirements.txt
> ```
>
> *"That's it?"*
>
> On the next build: 28 seconds. The pip cache hit on every layer. Dependencies downloaded once, reused forever — without those bytes ever appearing inside the image.

---

## Architecture

```
Without BuildKit cache mount
─────────────────────────────────────────────────────
  CI Runner 1 (push to feature-branch)
  └── RUN pip install → downloads 9 MB from PyPI → 4 min

  CI Runner 2 (push to main)
  └── RUN pip install → downloads 9 MB from PyPI → 4 min

  Result: 100 builds/week × 4 min = 400 min/week wasted on downloads


With BuildKit cache mount + remote registry cache
─────────────────────────────────────────────────────
  First build
  ├── RUN --mount=type=cache ... pip install → downloads 9 MB → 4 min
  └── --cache-to: type=registry → pushes layers to registry

  Every subsequent build (same or different runner)
  ├── --cache-from: type=registry → pulls matching layers
  └── RUN --mount=type=cache ... → pip cache already populated → 28 sec

  Result: 100 builds/week × 28 sec = ~47 min/week (88% reduction)
```

---

## Repository structure

```
phase-4-buildkit/
└── app/
    ├── Dockerfile        ← syntax directive, cache mounts, build-args
    ├── .dockerignore
    ├── app.py            ← same Flask API as Phase 3
    └── requirements.txt
```

---

## Challenge 1 — Enable BuildKit and verify

### Step 1: Check your Docker version

```bash
docker version --format '{{.Server.Version}}'
```

BuildKit is the default build engine since Docker 23.0. If you are on an older version, enable it explicitly:

```bash
export DOCKER_BUILDKIT=1
```

Or add it permanently to the Docker daemon:
```json
// /etc/docker/daemon.json
{ "features": { "buildkit": true } }
```

### Step 2: Verify BuildKit is active

```bash
docker buildx version
```

Expected:
```
github.com/docker/buildx v0.x.x ...
```

### Step 3: Review the syntax directive

```bash
head -3 phase-4-buildkit/app/Dockerfile
```

```dockerfile
# syntax=docker/dockerfile:1
```

This must be the **first line** of the Dockerfile. It tells Docker to pull the specified BuildKit frontend image before parsing the rest of the file. `docker/dockerfile:1` resolves to the latest stable 1.x version — it unlocks `RUN --mount`, `RUN --network`, inline cache, heredoc syntax, and other features not available in the classic parser.

> **What happens without this directive?** Docker uses the built-in parser, which does not support `RUN --mount`. The build fails with `unknown flag: --mount` even if BuildKit is enabled.

---

## Challenge 2 — Cache pip dependencies across builds

### Step 1: Build without the cache mount and time it

```bash
time docker build --no-cache -t nexio-api:0.3-nocache \
  -f phase-1-multistage-builds/app/Dockerfile \
  phase-1-multistage-builds/app/
```

Note the time. The `pip install` step downloads packages fresh every time.

### Step 2: Review the cache mount in the Phase 4 Dockerfile

```bash
cat phase-4-buildkit/app/Dockerfile
```

The key instruction:

```dockerfile
RUN --mount=type=cache,target=/root/.cache/pip \
    pip install --prefix=/install -r requirements.txt
```

`--mount=type=cache,target=/root/.cache/pip` mounts a BuildKit-managed cache directory at `/root/.cache/pip` for the duration of this `RUN` step. The directory is **not** part of the image layer — it exists only on the host's BuildKit cache storage. On the next build, pip finds its cache populated and skips downloading already-cached packages.

### Step 3: Build the first time

```bash
time docker build -t nexio-api:0.3 phase-4-buildkit/app/
```

This build populates the cache. Note the time.

### Step 4: Force-rebuild and observe the cache hit

```bash
time docker build --no-cache -t nexio-api:0.3 phase-4-buildkit/app/
```

`--no-cache` invalidates Docker's layer cache, forcing `RUN` to execute again. But the BuildKit cache mount at `/root/.cache/pip` is unaffected — it persists independently. pip finds its packages in the local cache and skips downloading from PyPI.

Expected: significantly faster than Step 1, despite `--no-cache`.

### Step 5: Verify the cache is not in the image

```bash
docker run --rm nexio-api:0.3 ls /root/.cache/
# ls: cannot access '/root/.cache/': No such file or directory
```

The pip cache directory exists only on the host. Nothing was written to any image layer.

---

## Challenge 3 — Inject build metadata with `--build-arg`

Static version labels (Phase 3) mean every commit produces an image labelled `version=0.1.0`. In CI, you need the image to carry the exact commit SHA and build timestamp so you can trace any running container back to its source.

### Step 1: Review ARG declarations in the Dockerfile

```dockerfile
# Stage 1 — builder
FROM python:3.12-slim AS builder
ARG APP_VERSION=dev
ARG BUILD_DATE=unknown
```

And in Stage 2:

```dockerfile
# Stage 2 — runtime
FROM python:3.12-slim
ARG APP_VERSION=dev    ← must be redeclared after each FROM
ARG BUILD_DATE=unknown
```

`ARG` values do not cross stage boundaries. If you declare `ARG APP_VERSION` only in Stage 1, Stage 2 cannot reference it. Redeclare in every stage that uses it.

### Step 2: Build with dynamic values

```bash
docker build \
  --build-arg APP_VERSION=$(git rev-parse --short HEAD 2>/dev/null || echo "dev") \
  --build-arg BUILD_DATE=$(date -u +%Y-%m-%dT%H:%M:%SZ) \
  -t nexio-api:0.3 \
  phase-4-buildkit/app/
```

### Step 3: Verify the labels

```bash
docker inspect nexio-api:0.3 | jq '.[0].Config.Labels'
```

Expected:
```json
{
  "org.opencontainers.image.created": "2026-04-28T10:00:00Z",
  "org.opencontainers.image.version": "a1b2c3d",
  ...
}
```

Any image running in production now carries the exact commit SHA it was built from. `docker inspect` on any container answers the question: *"what code is actually running?"*

> **`ARG` values are visible in `docker history`.** Do not use `--build-arg` for secrets. If you pass a password via `ARG`, anyone with access to the image (or `docker history`) can read it. Use `--mount=type=secret` for secrets (Challenge 4).

---

## Challenge 4 — Pass secrets at build time without leaking them

Some builds need credentials during the build itself — a private PyPI index, a GitHub token to install a private package, an internal NPM registry. Passing these via `ARG` or `ENV` writes them into the image layer and `docker history`. They are visible forever.

BuildKit's `--mount=type=secret` solves this: the secret is mounted into the `RUN` step as a file and is never written to any layer.

### Step 1: Create a test secret file

```bash
echo "supersecret-token" > /tmp/my_token.txt
```

### Step 2: Use the secret in a build

Create a temporary Dockerfile to demonstrate the pattern:

```bash
cat <<'EOF' > /tmp/Dockerfile.secret-demo
# syntax=docker/dockerfile:1
FROM python:3.12-slim
RUN --mount=type=secret,id=my_token \
    TOKEN=$(cat /run/secrets/my_token) && \
    echo "Token length: ${#TOKEN}" && \
    echo "Build step that uses TOKEN to authenticate..."
CMD ["python", "-c", "print('running')"]
EOF

docker build \
  --secret id=my_token,src=/tmp/my_token.txt \
  -t secret-demo \
  -f /tmp/Dockerfile.secret-demo \
  /tmp/
```

### Step 3: Verify the secret is not in the image

```bash
docker run --rm secret-demo cat /run/secrets/my_token
# cat: /run/secrets/my_token: No such file or directory

docker history secret-demo
# The secret value does not appear in any layer
```

The secret existed only during the `RUN` step, mounted at `/run/secrets/my_token`. It was never written to any filesystem layer.

> **Real-world use:** A private pip index requires an API key in the `pip.conf` or passed via `PIP_INDEX_URL`. Instead of baking it into the image, mount it as a secret:
> ```dockerfile
> RUN --mount=type=secret,id=pip_token \
>     PIP_INDEX_URL=https://$(cat /run/secrets/pip_token)@private.pypi.example.com/simple/ \
>     pip install --prefix=/install -r requirements.txt
> ```

---

## Challenge 5 — Build multi-platform images with `docker buildx`

Apple Silicon Macs (M1/M2/M3) run `linux/arm64`. Most CI runners and production servers run `linux/amd64`. An image built on a Mac without specifying a platform may not run on a CI runner — and vice versa.

`docker buildx` builds for multiple platforms in a single command and pushes a multi-platform manifest to the registry.

### Step 1: Create a buildx builder

```bash
docker buildx create --name nexio-builder --use
docker buildx inspect --bootstrap
```

The builder starts a BuildKit daemon that can cross-compile for other architectures using QEMU emulation.

### Step 2: Build for both amd64 and arm64

```bash
docker buildx build \
  --platform linux/amd64,linux/arm64 \
  --build-arg APP_VERSION=$(git rev-parse --short HEAD 2>/dev/null || echo "dev") \
  --build-arg BUILD_DATE=$(date -u +%Y-%m-%dT%H:%M:%SZ) \
  -t nexio-api:0.3-multiplatform \
  --load \
  phase-4-buildkit/app/
```

> **`--load` vs `--push`:** `--load` loads the image into the local Docker image store (works only for single-platform builds). `--push` pushes directly to a registry (supports multi-platform manifests). To test locally, build for your native platform only and use `--load`. To publish a proper multi-platform manifest, use `--push`.

### Step 3: Inspect the platform

```bash
docker inspect nexio-api:0.3-multiplatform | jq '.[0].Architecture'
# "arm64"  (or "amd64", depending on your host)
```

When you push a multi-platform manifest to a registry, the registry serves the correct variant automatically — an `amd64` host pulls the `amd64` layer, an `arm64` host pulls the `arm64` layer. No per-platform tagging required.

---

## Challenge 6 — Share the build cache across CI runners

On a local machine, BuildKit's cache is stored on disk. In CI, each runner typically starts fresh — no cache, no benefit from previous builds. The solution is a **registry-based remote cache**: export the cache layers to the registry after a build, import them at the start of the next build.

### Step 1: Understand the flags

```bash
docker buildx build \
  --cache-from type=registry,ref=ghcr.io/your-org/nexio-api:buildcache \
  --cache-to   type=registry,ref=ghcr.io/your-org/nexio-api:buildcache,mode=max \
  -t nexio-api:0.3 \
  --push \
  phase-4-buildkit/app/
```

| Flag | Meaning |
|---|---|
| `--cache-from type=registry,...` | Pull cached layers from the registry before building |
| `--cache-to type=registry,...` | Export cache layers to the registry after building |
| `mode=max` | Cache all intermediate layers, not just the final stage — maximises cache hits |

### Step 2: How this plays out in GitHub Actions

```yaml
- name: Build and push
  uses: docker/build-push-action@v6
  with:
    context: phase-4-buildkit/app
    push: true
    tags: ghcr.io/your-org/nexio-api:sha-${{ github.sha }}
    build-args: |
      APP_VERSION=${{ github.sha }}
      BUILD_DATE=${{ github.event.head_commit.timestamp }}
    cache-from: type=gha       # GitHub Actions cache (built-in)
    cache-to: type=gha,mode=max
```

`type=gha` uses GitHub Actions' own cache storage — no external registry needed. The cache is keyed by branch and Dockerfile content hash. A push to `main` populates the cache; subsequent pushes on `feature` branches start with the `main` cache as a fallback.

---

## Command reference

| Command | What it does |
|---|---|
| `docker buildx create --use` | Create and activate a new BuildKit builder |
| `docker buildx inspect --bootstrap` | Start the builder and print its configuration |
| `docker buildx build --platform linux/amd64,linux/arm64` | Multi-platform build |
| `docker buildx build --push` | Build and push to registry (required for multi-platform) |
| `docker buildx build --load` | Build and load into local image store (single platform only) |
| `docker buildx ls` | List available builders |
| `docker buildx rm name` | Remove a builder |
| `docker buildx prune` | Clean BuildKit cache on the current builder |

---

## Production considerations

### 1. Cache mounts dramatically change CI economics
For a Python project with 50 dependencies, `--mount=type=cache` + `--cache-from registry` can reduce `pip install` time from 4 minutes to under 10 seconds on a warm cache. Multiply that by 100 builds per day across 20 engineers and the time and cost savings are significant. This is the highest-impact optimization in this phase.

### 2. `ARG` values break the layer cache
Every `ARG` that changes (like `BUILD_DATE`) invalidates the layer containing it and all subsequent layers. Place `ARG BUILD_DATE` as late as possible in the Dockerfile — after all expensive `RUN` steps — so a new date doesn't bust the pip install cache. In the Phase 4 Dockerfile, build args are referenced only in `LABEL` instructions which come before `COPY app.py`, keeping the expensive install layer stable.

### 3. Multi-platform builds in CI require `--push`, not `--load`
`docker buildx build --platform linux/amd64,linux/arm64 --load` fails — the local Docker image store cannot hold a multi-platform manifest. In CI, always use `--push` with multi-platform builds. For local testing, build for your native platform only.

### 4. Secrets in build args persist in image history forever
`docker history` outputs every `ARG` value passed to the build, even if the `ARG` is not referenced in a `LABEL`. If you need to rotate a credential that was passed as `--build-arg`, you cannot do so — the old value is baked into every image layer forever. Use `--mount=type=secret` for any value that must not persist.

### 5. Pin the `# syntax` directive in production
`# syntax=docker/dockerfile:1` resolves to the latest 1.x frontend. For reproducible builds, pin to a specific version: `# syntax=docker/dockerfile:1.6.0`. The frontend is fetched from Docker Hub at build time — pinning to a digest guarantees it never changes under you.

---

## Outcome

Builds now use a persistent pip cache mounted by BuildKit — zero pip downloads on warm cache hits. Every image carries an immutable commit SHA and build timestamp in its OCI labels. Images are built for both `linux/amd64` and `linux/arm64` from a single command. CI runners share the cache via the registry, making cold builds rare.

Secrets pass through the build process without touching any image layer.

---

[Back to Phase 3](../phase-3-production-ready/README.md) | [Next: Phase 5 — Container Security Scanning & Signing →](../phase-5-scanning-signing/README.md)
