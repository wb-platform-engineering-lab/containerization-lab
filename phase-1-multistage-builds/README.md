# Phase 1 — Multi-Stage Builds & Image Optimization

> **Concepts introduced:** Multi-stage build, slim base image, `.dockerignore`, build cache invalidation, `docker history`, `dive`

---

## Concepts introduced

| Concept | What it is | Why it matters |
|---|---|---|
| **Multi-stage build** | Multiple `FROM` blocks in one Dockerfile; only the last stage ships | Build tools stay out of the final image — smaller, safer |
| **Slim base image** | `python:3.12-slim` vs `python:3.12` — Debian minimal vs full | Removes ~850 MB of compilers, manpages, and utilities never used at runtime |
| **`.dockerignore`** | A list of paths the build context excludes before sending files to the daemon | Prevents local junk (`.git/`, `__pycache__/`, `.venv/`) from polluting the image |
| **Build cache invalidation** | Docker rebuilds from the first layer whose inputs changed | Layer order is a performance decision — slow layers must come before fast ones |
| **`docker history`** | Shows each layer of an image, its size, and the command that created it | Reveals exactly where the bytes come from |
| **`dive`** | Interactive TUI for exploring image layers and wasted space | Faster than `docker history` for diagnosing bloat |

---

## The problem

> *Nexio — 3 engineers. Six weeks after Phase 0.*
>
> The team had grown to three. They'd added GitHub Actions to run tests on every push. The first CI pipeline took 18 minutes — most of it spent building the Docker image.
>
> The lead engineer checked the image size.
>
> ```
> nexio-api   latest   abc123   2 hours ago   1.05GB
> ```
>
> Over a gigabyte. For a 30-line Flask app and one dependency.
>
> She ran `docker history nexio-api:latest`. The `python:3.12` base layer alone was 998 MB. It contained a C compiler, `man` pages, Perl, documentation for packages they'd never install. All of it shipped to every CI runner, every production host, every pull.
>
> At their current push frequency — 15 pushes a day — that was 15 GB of image data transferred daily. Artifact Registry was charging them for it. CI runners were spending 12 of those 18 minutes just pulling layers.
>
> *"The app is 30 lines. The image should not be a gigabyte."*
>
> She opened the Dockerfile and added one line: `AS builder`.

---

## Architecture

```
Phase 0 — single-stage build
────────────────────────────────────────────────────────
  FROM python:3.12          ← 998 MB base (compiler, manpages, Perl...)
  WORKDIR /app
  COPY requirements.txt .
  RUN pip install ...       ← pip + build tools added to this layer
  COPY app.py .
  ─────────────────────────────────────────────────────
  Final image: ~1.05 GB     (all of the above, shipped together)


Phase 1 — multi-stage build
────────────────────────────────────────────────────────
  Stage 1: builder (never shipped)
  ├── FROM python:3.12-slim AS builder
  ├── COPY requirements.txt .
  └── RUN pip install --prefix=/install ...
         └── packages land in /install

  Stage 2: runtime (the only stage that ships)
  ├── FROM python:3.12-slim             ← clean 130 MB base
  ├── COPY --from=builder /install ...  ← packages only, not pip itself
  └── COPY app.py .
  ─────────────────────────────────────────────────────
  Final image: ~125 MB      (8× smaller than Phase 0)
```

---

## Repository structure

```
phase-1-multistage-builds/
└── app/
    ├── Dockerfile        ← two-stage optimized build
    ├── .dockerignore     ← excludes __pycache__, .git, .venv, etc.
    ├── app.py            ← same Flask API as Phase 0 (unchanged)
    └── requirements.txt  ← same pinned dependency
```

The application code is identical to Phase 0. Everything that changes is packaging.

---

## Challenge 1 — Measure the Phase 0 baseline

Before optimizing, establish what you are optimizing against. If you completed Phase 0, the image is already in your local cache. If not, build it now:

```bash
docker build -t nexio-api:phase0 phase-0-first-container/app/
```

### Step 1: Check the image size

```bash
docker images nexio-api
```

Expected:
```
REPOSITORY   TAG      IMAGE ID       CREATED        SIZE
nexio-api    phase0   abc123def456   1 minute ago   1.05GB
```

### Step 2: Inspect the layers with `docker history`

```bash
docker history nexio-api:phase0
```

Expected output (condensed):
```
IMAGE         CREATED BY                                      SIZE
abc123        CMD ["python" "app.py"]                         0B
<missing>     EXPOSE 5000                                     0B
<missing>     COPY app.py .                                   2.1kB
<missing>     pip install -r requirements.txt                 9.4MB
<missing>     COPY requirements.txt .                         20B
<missing>     WORKDIR /app                                    0B
<missing>     /bin/sh -c #(nop)  ENV ...                     0B
<missing>     /bin/sh -c apt-get install ... python3 ...      998MB   ← the problem
```

Nearly the entire image is the base layer. The application itself — `app.py` + Flask — is under 12 MB. The other 1,040 MB is a full Debian OS with tools that never run in production.

### Step 3: Understand what is in that base image

```bash
docker run --rm nexio-api:phase0 python -c "import sys; print(sys.version)"
# Python 3.12.x — good, that's what we need

docker run --rm nexio-api:phase0 which gcc
# /usr/bin/gcc   ← a C compiler. In a Flask API image.

docker run --rm nexio-api:phase0 du -sh /usr/lib/gcc
# ~50MB of compiler toolchain never used at runtime
```

This is the cost of `FROM python:3.12`. It includes everything a developer might want for compiling Python extensions — shipped to every environment whether or not it is needed.

---

## Challenge 2 — Add a `.dockerignore` file

Before touching the Dockerfile, fix the build context.

When you run `docker build`, Docker sends the entire directory (the "build context") to the daemon. Without a `.dockerignore`, that includes `.git/`, `__pycache__/`, `.venv/`, and any other file sitting in the directory.

### Step 1: See what gets sent today

```bash
# Simulate what gets included in the build context
docker build --no-cache -t nexio-api:ctx-test phase-0-first-container/app/ 2>&1 | head -5
```

The first line shows the build context size being transferred.

### Step 2: Review the `.dockerignore`

```bash
cat phase-1-multistage-builds/app/.dockerignore
```

```
__pycache__/
*.pyc
*.pyo
.pytest_cache/
.venv/
venv/
.git/
.gitignore
*.md
tests/
test_*.py
.idea/
.vscode/
*.swp
```

> **Why exclude `.git/`?** Git history can be large and contains every version of every file ever committed. It has no place in a runtime image. If you need the commit SHA at build time, pass it as a `--build-arg`, not via the build context.

> **Why exclude `*.md`?** README files are for humans. They add bytes to the image and serve no runtime purpose. Documents belong in a registry like Confluence, not inside a container.

### Step 3: Verify the file is in place

```bash
cat phase-1-multistage-builds/app/.dockerignore
```

`.dockerignore` is applied automatically when it sits alongside the Dockerfile. No flag required.

---

## Challenge 3 — Switch to a slim base image

The fastest single change: swap `python:3.12` for `python:3.12-slim`. No multi-stage yet — just measure the impact of the base image choice alone.

### Step 1: Build with slim base, single stage

Create a temporary Dockerfile to isolate the slim-only effect:

```bash
cat <<'EOF' > /tmp/Dockerfile.slim-only
FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app.py .
EXPOSE 5000
CMD ["python", "app.py"]
EOF

docker build -t nexio-api:slim-only -f /tmp/Dockerfile.slim-only \
  phase-1-multistage-builds/app/
```

### Step 2: Compare sizes

```bash
docker images nexio-api
```

Expected:
```
REPOSITORY   TAG         SIZE
nexio-api    phase0      1.05GB
nexio-api    slim-only   182MB
```

The base image change alone cuts 870 MB (~83%) — without touching the build strategy at all.

### Step 3: Understand what `slim` removes

`python:3.12-slim` is built from `debian:bookworm-slim`. It removes:
- Documentation (`/usr/share/doc/`, `/usr/share/man/`)
- Locales (`/usr/lib/locale/`)
- Build toolchains (`gcc`, `g++`, `make`)
- Unused system libraries

What it keeps: Python, pip, and the minimum Debian packages needed to run Python programs. For a pure Python app with no C extension compilation, slim is sufficient.

> **When `slim` is not enough:** If your dependencies include packages that compile C extensions at install time (e.g. `psycopg2`, `Pillow`, `cryptography`), the compilation happens in the builder stage using the full image — and the compiled binary is copied to the slim runtime stage. That's exactly what multi-stage is designed for.

---

## Challenge 4 — Write the multi-stage Dockerfile

Now combine the slim base with a proper two-stage build.

### Step 1: Review the Dockerfile

```bash
cat phase-1-multistage-builds/app/Dockerfile
```

Key decisions annotated:

```dockerfile
# Stage 1 — builder (this stage is never shipped)
FROM python:3.12-slim AS builder
```

`AS builder` names this stage. The name is referenced by `--from=builder` in Stage 2. Without `AS`, you would reference it by index (`--from=0`), which is fragile.

```dockerfile
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt
```

Two flags doing important work:

| Flag | What it does |
|---|---|
| `--no-cache-dir` | Skips writing pip's HTTP cache to the layer — saves ~10–20 MB |
| `--prefix=/install` | Installs packages into `/install` instead of system paths — makes it trivial to copy just the packages to Stage 2 |

```dockerfile
# Stage 2 — runtime (the only stage that ships)
FROM python:3.12-slim
```

A fresh `python:3.12-slim` image. No pip cache, no build artifacts, no intermediate files from Stage 1. The builder stage could have made a complete mess — none of it crosses into Stage 2.

```dockerfile
COPY --from=builder /install /usr/local
```

Copies everything under `/install` from the builder stage into `/usr/local` of the runtime stage. Python looks for installed packages in `/usr/local/lib/python3.12/site-packages/` — exactly where they land.

```dockerfile
COPY app.py .
```

Only the source file. Not `requirements.txt` — it is not needed at runtime, only at build time.

### Step 2: Build the optimized image

```bash
docker build -t nexio-api:0.1 phase-1-multistage-builds/app/
```

### Step 3: Compare all three variants

```bash
docker images nexio-api
```

Expected:
```
REPOSITORY   TAG         SIZE
nexio-api    phase0      1.05GB
nexio-api    slim-only   182MB
nexio-api    0.1         125MB
```

| Build | Strategy | Size | vs Phase 0 |
|---|---|---|---|
| `phase0` | Single-stage, full base | 1.05 GB | baseline |
| `slim-only` | Single-stage, slim base | 182 MB | −83% |
| `0.1` | Multi-stage, slim base | 125 MB | −88% |

---

## Challenge 5 — Analyse layers with `docker history`

### Step 1: Compare layer histories

```bash
docker history nexio-api:phase0
docker history nexio-api:0.1
```

Phase 0 output (condensed):
```
IMAGE     CREATED BY                             SIZE
...       COPY app.py .                          2.1kB
...       RUN pip install -r requirements.txt    9.4MB
...       COPY requirements.txt .                20B
...       /bin/sh -c #(nop)  WORKDIR /app        0B
...       python:3.12 base layers                998MB   ← everything below this
```

Phase 1 output (condensed):
```
IMAGE     CREATED BY                                     SIZE
...       COPY app.py .                                  2.1kB
...       COPY --from=builder /install /usr/local        8.9MB   ← packages only
...       /bin/sh -c #(nop)  WORKDIR /app                0B
...       python:3.12-slim base layers                   130MB
```

The pip cache, build tools, and the Stage 1 filesystem are completely absent. Only the installed package files crossed the stage boundary.

### Step 2: (Optional) Analyse with `dive`

`dive` is an interactive TUI that shows layer contents and highlights wasted space (files added then deleted within the same image):

```bash
# Install dive (macOS)
brew install dive

# Analyse the optimized image
dive nexio-api:0.1
```

In the dive UI:
- Left pane: layer list with size deltas
- Right pane: filesystem tree for the selected layer
- `Tab` switches panes, arrow keys navigate
- Look for yellow-highlighted files — those are files present in a lower layer that were overwritten or deleted (wasted space)

For our image, dive should show 0% wasted space — there is nothing to clean up between layers.

---

## Challenge 6 — Verify the optimized image works

A smaller image that breaks is not an improvement.

### Step 1: Run the optimized container

```bash
docker run -d --name nexio -p 5000:5000 nexio-api:0.1
```

### Step 2: Verify all endpoints

```bash
curl http://localhost:5000/
curl http://localhost:5000/health
curl http://localhost:5000/event
```

All three should return the same JSON responses as Phase 0.

### Step 3: Confirm pip is NOT in the runtime image

One of the main goals of the multi-stage build is excluding build tools from the final image. Verify:

```bash
docker exec nexio which pip
# (no output — pip is not installed)

docker exec nexio pip --version
# sh: pip: not found
```

pip was only needed during the builder stage to install packages. It does not exist in the runtime image — which means an attacker who gains code execution inside the container cannot use pip to install additional tools.

### Step 4: Confirm Flask is importable

Packages must still be present:

```bash
docker exec nexio python -c "import flask; print(flask.__version__)"
# 3.1.0
```

### Step 5: Teardown

```bash
docker rm -f nexio
docker rmi nexio-api:slim-only nexio-api:phase0
```

---

## Command reference

| Command | What it does |
|---|---|
| `docker history name:tag` | Show layers, sizes, and creating commands |
| `docker build --no-cache` | Force rebuild every layer, ignoring cache |
| `docker build -f path/Dockerfile` | Use a specific Dockerfile (not the default) |
| `docker image prune` | Remove all dangling (untagged) images |
| `docker system prune` | Remove stopped containers, unused networks, dangling images |
| `dive name:tag` | Interactive layer explorer (requires `dive` installed) |

---

## Production considerations

### 1. Use digest-pinned base images in both stages
Both `FROM python:3.12-slim AS builder` and the runtime `FROM python:3.12-slim` should be pinned to a digest in production. If Docker Hub silently updates the `3.12-slim` tag with a new package, your "unchanged" build now has different bytes. Pin once, update intentionally:

```dockerfile
FROM python:3.12-slim@sha256:abc123... AS builder
...
FROM python:3.12-slim@sha256:abc123...
```

Automate digest bumps with Dependabot or Renovate — they open a PR when a new digest is available, giving you a review gate.

### 2. Lock transitive dependencies with a lock file
`requirements.txt` pins Flask to `3.1.0` but not Flask's own dependencies. On the next build, a newer version of Werkzeug (Flask's dependency) might install. Use `pip-compile` from `pip-tools` to generate a fully resolved `requirements.lock`:

```bash
pip-compile requirements.txt --output-file requirements.lock
```

Then in the Dockerfile:

```dockerfile
COPY requirements.lock .
RUN pip install --no-cache-dir --prefix=/install -r requirements.lock
```

Every build installs the exact same dependency tree, forever.

### 3. Never install pip in the runtime stage
This phase removes pip from the runtime image by design. Keep it that way. If your application needs to install packages at runtime, that is an architectural problem — not a packaging one. Dependencies belong in the image, determined at build time.

### 4. Multi-stage caching in CI needs explicit cache sources
`docker build` uses the local layer cache on the machine running the build. In CI, each runner may be a fresh VM with an empty cache — every build starts from scratch. In Phase 4, we address this with BuildKit's `--cache-from` flag, which pulls a remote cache from the registry before building.

### 5. Builder stage failures are still failures
If `pip install` fails in Stage 1, the build fails — Docker does not proceed to Stage 2. This is correct behaviour. A failed dependency install means there is no valid runtime stage to build. Do not suppress errors in builder stages.

### 6. The `.dockerignore` applies to all stages
A single `.dockerignore` file controls what gets sent in the build context for all stages. Files excluded from the context cannot be `COPY`'d by any stage. Keep this in mind when structuring multi-stage builds that pull from different source directories.

---

## Outcome

The image is now 125 MB — an 88% reduction from Phase 0. Build tools and pip are absent from the runtime stage. The build context is clean. A code change hits only the last two layers, leaving the dependency install layer fully cached.

The app is still running as root. That is addressed in Phase 3.

---

[Back to Phase 0](../phase-0-first-container/README.md) | [Next: Phase 2 — Multi-Container Apps with Docker Compose →](../phase-2-compose/README.md)
