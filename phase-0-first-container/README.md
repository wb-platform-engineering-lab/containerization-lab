# Phase 0 — Your First Container

> **Concepts introduced:** Image, Container, Layer, Dockerfile, Port mapping, Environment variables

---

## Concepts introduced

| Concept | What it is | Why it matters |
|---|---|---|
| **Image** | Read-only filesystem snapshot + metadata | The artifact you build, store, and ship — identical everywhere |
| **Container** | A running instance of an image | Isolated process with its own filesystem and network namespace |
| **Layer** | One Dockerfile instruction = one cached filesystem diff | Unchanged layers are reused — fast rebuilds, shared storage |
| **Dockerfile** | A recipe that defines how to build an image | Reproducible, version-controlled build instructions |
| **Port mapping** | `-p host:container` at runtime | The container is isolated by default — you explicitly decide what to expose |
| **Environment variable** | `-e KEY=value` at runtime | Configuration injected at runtime, not baked into the image |

---

## The problem

> *Nexio — 1 engineer. Day one.*
>
> The lead engineer had been building for four weeks straight. The MVP was done: a Python Flask API that ingested events and returned structured payloads. It ran perfectly on her MacBook.
>
> Then the second engineer joined. He had Python 3.10. She had 3.12. Different pip. A `requirements.txt` that resolved a subtly different dependency tree. The app crashed with a stack trace that had nothing to do with the code.
>
> She spent 45 minutes helping him debug his environment instead of building features.
>
> *"We can't keep doing this. Every new hire loses a day to setup."*
>
> She opened a terminal, created a new file, and typed `FROM python:3.12`. That was the first container. From that point on, the entire team ran the same environment — on any machine, in under a minute.

---

## Architecture

```
Your machine (host)
│
├── docker build → Image: nexio-api:0.1
│                  ├── Layer 1: python:3.12 base OS
│                  ├── Layer 2: pip install flask
│                  └── Layer 3: app.py
│
└── docker run → Container: nexio
                 ├── Isolated filesystem (from image)
                 ├── Isolated network namespace
                 │   └── Port 5000 (internal)
                 │        └── mapped to → host port 5000
                 └── Process: python app.py
                              └── endpoints: /, /health, /event
```

---

## Repository structure

```
phase-0-first-container/
└── app/
    ├── Dockerfile        ← build instructions
    ├── app.py            ← Flask API (3 endpoints)
    └── requirements.txt  ← pinned dependencies
```

---

## Challenge 1 — Inspect the application

Before touching Docker, understand what you are packaging.

### Step 1: Read the source

```bash
cat phase-0-first-container/app/app.py
```

Three endpoints:
- `GET /` — welcome message with service name and version
- `GET /health` — health check (used by orchestrators in later phases)
- `GET /event` — a sample enriched event payload

### Step 2: Read the dependencies

```bash
cat phase-0-first-container/app/requirements.txt
```

A single pinned dependency: `flask==3.1.0`. Pinning to an exact version means the build is reproducible — running `pip install` in six months gives the same result as today.

> **Why pin?** Unpinned dependencies (`flask`) install the latest version at build time. A breaking change in Flask 4.0 could silently break your image on the next build, with no change to your code.

---

## Challenge 2 — Read and understand the Dockerfile

```bash
cat phase-0-first-container/app/Dockerfile
```

Walk through each instruction:

```dockerfile
FROM python:3.12
```
Sets the base image. Every image starts from an existing one. `python:3.12` is an official image (~1 GB) that includes Python, pip, and a full Debian OS. We will shrink this in Phase 1.

```dockerfile
WORKDIR /app
```
Sets the working directory for all subsequent instructions and for the running container. Creates the directory if it doesn't exist. Prefer this over `RUN mkdir && cd` — it is idempotent and explicit.

```dockerfile
COPY requirements.txt .
RUN pip install -r requirements.txt
```
These two instructions are intentionally ordered before copying the source code. Docker rebuilds from the first changed layer downward. `requirements.txt` changes rarely — `app.py` changes constantly. This ordering means a code change hits only the last layer, not the pip install.

```dockerfile
COPY app.py .
```
Copies the source code into the image. This layer will be rebuilt on every code change — but because it comes after the pip install, the install layer stays cached.

```dockerfile
EXPOSE 5000
```
Documentation only. It does **not** publish the port on the host. Think of it as a comment in the Dockerfile that says "this process listens on 5000". The actual port mapping happens at `docker run` time with `-p`.

```dockerfile
CMD ["python", "app.py"]
```
The default command when the container starts. Uses exec form (JSON array) — not shell form (`CMD python app.py`). Exec form makes `python` PID 1, which means it receives SIGTERM directly when the container stops. Shell form wraps it in `/bin/sh -c`, meaning SIGTERM goes to the shell, not to Python.

> **ENTRYPOINT vs CMD:** `CMD` is the default command — it can be overridden at `docker run`. `ENTRYPOINT` is the fixed executable — it cannot be overridden without `--entrypoint`. For a simple app, `CMD` is enough.

---

## Challenge 3 — Build the image

### Step 1: Build

```bash
docker build -t nexio-api:0.1 phase-0-first-container/app/
```

`-t nexio-api:0.1` — tags the image `name:tag`. The tag `0.1` is arbitrary here; in Phase 5 we will use the Git commit SHA.

Watch the output: each `FROM`, `COPY`, `RUN` line corresponds to a layer being executed.

### Step 2: Rebuild and observe the cache

Run the exact same command again:

```bash
docker build -t nexio-api:0.1 phase-0-first-container/app/
```

Expected output:
```
 => CACHED [2/4] WORKDIR /app
 => CACHED [3/4] COPY requirements.txt .
 => CACHED [4/4] RUN pip install -r requirements.txt
```

Nothing changed — every layer is served from cache. This is why layer ordering matters.

### Step 3: Check the image

```bash
docker images nexio-api
```

Expected output:
```
REPOSITORY   TAG   IMAGE ID       CREATED         SIZE
nexio-api    0.1   abc123def456   2 minutes ago   1.05GB
```

Note the size. In Phase 1 we will bring this below 120 MB using a multi-stage build.

---

## Challenge 4 — Run the container and verify

### Step 1: Start the container

```bash
docker run -d --name nexio -p 5000:5000 nexio-api:0.1
```

| Flag | Meaning |
|---|---|
| `-d` | Detached — runs in the background, returns the container ID |
| `--name nexio` | Assigns a name so you can reference it without the ID |
| `-p 5000:5000` | Maps host port 5000 → container port 5000 |

### Step 2: Confirm it is running

```bash
docker ps
```

Expected:
```
CONTAINER ID   IMAGE           COMMAND             STATUS         PORTS                    NAMES
a1b2c3d4e5f6   nexio-api:0.1   "python app.py"     Up 3 seconds   0.0.0.0:5000->5000/tcp   nexio
```

### Step 3: Call the API

```bash
# Welcome message
curl http://localhost:5000/
# {"message":"Welcome to Nexio...","service":"nexio-api","version":"0.1.0"}

# Health check
curl http://localhost:5000/health
# {"service":"nexio-api","status":"healthy"}

# Sample event
curl http://localhost:5000/event
# {"event_id":"evt_demo_001","type":"page_view",...}
```

---

## Challenge 5 — Inspect the container internals

### Step 1: Stream logs

```bash
docker logs -f nexio
```

You will see Flask's request log lines for every `curl` you made. `-f` follows the stream (like `tail -f`). `Ctrl+C` to exit — the container keeps running.

### Step 2: Open a shell inside the container

```bash
docker exec -it nexio sh
```

`exec` runs a new command inside an *already running* container. `-it` gives you an interactive terminal. You are now inside the container's isolated filesystem.

```bash
# Where are we?
pwd
# /app

# What files are here?
ls -la
# app.py  requirements.txt  (and the installed packages in /usr/local/lib)

# What Python version?
python --version
# Python 3.12.x

# What user are we?
whoami
# root   ← we will fix this in Phase 3

# What processes are running?
ps aux
# PID 1: python app.py   ← public health issue: PID 1 is the app directly

exit
```

### Step 3: Inspect metadata

```bash
docker inspect nexio
```

This returns the full container configuration as JSON: network settings, mounts, environment variables, the image layers it was created from, the start command. Use `| jq .` for readable output if you have jq installed.

---

## Challenge 6 — Pass environment variables at runtime

The app reads `SERVICE_NAME` from the environment. This is the 12-factor app pattern: configuration via environment, not baked into the image.

### Step 1: Stop and remove the current container

```bash
docker stop nexio && docker rm nexio
```

`docker stop` sends SIGTERM, waits 10 seconds for a graceful shutdown, then sends SIGKILL. `docker rm` removes the stopped container. The image (`nexio-api:0.1`) is not affected.

### Step 2: Re-run with a custom variable

```bash
docker run -d --name nexio -p 5000:5000 \
  -e SERVICE_NAME=nexio-events \
  nexio-api:0.1
```

### Step 3: Verify the change

```bash
curl http://localhost:5000/health
# {"service":"nexio-events","status":"healthy"}
```

The same image, different behaviour — driven entirely by runtime configuration. This is the foundation for promoting one image across dev, staging, and production without rebuilding it.

### Step 4: Teardown

```bash
docker rm -f nexio
```

`-f` force-stops and removes in one command.

---

## Command reference

| Command | What it does |
|---|---|
| `docker build -t name:tag .` | Build an image from a Dockerfile in the current directory |
| `docker images` | List local images |
| `docker run -d -p HOST:CONTAINER name:tag` | Run a container in the background |
| `docker ps` | List running containers |
| `docker ps -a` | List all containers (including stopped) |
| `docker logs -f name` | Stream container logs |
| `docker exec -it name sh` | Open an interactive shell in a running container |
| `docker stop name` | Gracefully stop a container (SIGTERM → SIGKILL after 10s) |
| `docker rm name` | Remove a stopped container |
| `docker rm -f name` | Force-stop and remove |
| `docker rmi name:tag` | Remove an image from the local cache |
| `docker inspect name` | Full container metadata as JSON |
| `docker history name:tag` | Show image layers and their sizes |

---

## What this Dockerfile does NOT do yet

This Dockerfile works — and that was the only goal of Phase 0. The following issues are intentional:

| Issue | Impact | Fixed in |
|---|---|---|
| `python:3.12` base image (~1 GB) | Slow CI, high storage cost, large attack surface | Phase 1 |
| Single-stage build | Build tools included in the final image | Phase 1 |
| No `.dockerignore` | Any file in the directory gets copied into the image | Phase 1 |
| Runs as `root` | A container escape gives full host access | Phase 3 |
| No `HEALTHCHECK` instruction | Docker can't distinguish a crashed app from a running one | Phase 3 |
| No OCI image labels | No traceability to source commit or build timestamp | Phase 3 |

---

## Production considerations

### 1. Never use `:latest` in production
`latest` is mutable — two pulls of `nexio-api:latest` may return different images. In production, every image must be tagged with an immutable identifier: a Git commit SHA (`nexio-api:sha-a1b2c3`) or a semver tag (`nexio-api:v1.4.2`). This is covered in Phase 5.

### 2. Pin base image digests for true reproducibility
`FROM python:3.12` resolves to whatever image Docker Hub has at build time. The tag can be overwritten. For a truly reproducible build, pin to the digest:
```dockerfile
FROM python:3.12@sha256:abc123...
```
This guarantees the base layer never changes under you.

### 3. Separate image build from image run in CI
In a real pipeline, the `docker build` step runs on one machine (a CI runner), and the `docker run` step runs on another (a server or Kubernetes node). The image is the contract between them — it must be pushed to a registry in between. Local builds with `docker run` are for development only.

### 4. Containers should be stateless and ephemeral
Any data written inside a running container (to its writable layer) is lost when the container is removed. Persistent data must be written to a mounted volume or an external service (a database, an object store). Design your application accordingly — a container restart should be a non-event.

### 5. One process per container
This Dockerfile runs one process (`python app.py`). That is the intended model. Avoid running multiple services (e.g. app + nginx + cron) inside one container — it makes logging, health checking, and restarts significantly more complex and defeats the isolation model.

---

## Outcome

A Python Flask API packaged as a Docker image, running as an isolated container with a mapped port and runtime-injected configuration. The same image runs identically on any machine with Docker installed — no Python version conflicts, no missing dependencies, no "it works on mine".

The image is 1 GB and runs as root. Both of those facts are fixed in subsequent phases.

---

[Back to main README](../README.md) | [Next: Phase 1 — Multi-Stage Builds & Image Optimization →](../phase-1-multistage-builds/README.md)
