# Phase 0 — Your First Container

---

> **Nexio — 1 engineer. Day one.**
>
> The lead engineer had been building for four weeks straight. The MVP was done: a Python Flask API that ingested events and returned structured payloads. It ran perfectly on her MacBook.
>
> Then the second engineer joined. He had a different Python version. Different pip. A `requirements.txt` that installed a subtly different tree of transitive dependencies.
>
> The app crashed with a stack trace that had nothing to do with the code.
>
> She opened a terminal, created a new file, and typed:
>
> ```
> FROM python:3.12
> ```
>
> *That was the first container.*

---

## What you will build

A single Python Flask API running in a Docker container. You will write a Dockerfile, build an image, run a container, and interact with it from your host machine.

## Learning objectives

By the end of this phase you will be able to:

- Explain what a Docker **image** and a **container** are (and the difference between them)
- Write a basic `Dockerfile` using `FROM`, `WORKDIR`, `COPY`, `RUN`, `EXPOSE`, and `CMD`
- Build an image with `docker build`
- Run, inspect, and stop containers with `docker run`, `docker ps`, `docker logs`, `docker exec`, `docker stop`, `docker rm`
- Publish a container port to your host machine with `-p`
- Pass environment variables into a container with `-e`

---

## Core concepts

### Image vs Container

| | Image | Container |
|---|---|---|
| What it is | A read-only filesystem snapshot + metadata | A running instance of an image |
| Analogy | A class definition | An object (instance) |
| Created with | `docker build` | `docker run` |
| Stored | In your local image cache (or a registry) | In memory / on disk while running |

### The Dockerfile

A `Dockerfile` is a recipe that tells Docker how to build an image, one instruction at a time. Each instruction creates a new **layer**. Layers are cached — if a layer's inputs haven't changed, Docker reuses the cached result.

```
FROM python:3.12          ← base image (layer 1)
WORKDIR /app              ← set working directory (layer 2)
COPY requirements.txt .   ← copy a file into the image (layer 3)
RUN pip install ...       ← execute a command during build (layer 4)
COPY app.py .             ← copy source code (layer 5)
EXPOSE 5000               ← document the port (metadata only)
CMD ["python", "app.py"]  ← default command to run (metadata)
```

> **Why does order matter?**
> Docker rebuilds from the first changed layer downward. `requirements.txt` changes less often than `app.py`, so we copy and install it *before* copying the source code. This way, a code change doesn't invalidate the pip install layer.

---

## Step-by-step walkthrough

### 1. Inspect the app

```bash
cat phase-0-first-container/app/app.py
```

Three endpoints:
- `GET /` — welcome message
- `GET /health` — health check
- `GET /event` — a sample event payload

### 2. Build the image

```bash
docker build -t nexio-api:0.1 phase-0-first-container/app/
```

`-t nexio-api:0.1` — tags the image with a name and version.

Watch the output carefully. You will see each layer being executed in sequence. Run it a second time — notice how Docker says `CACHED` for layers that haven't changed. That's the layer cache at work.

**Check the image was created:**

```bash
docker images nexio-api
```

You will see the image size. Note it — we will compare it in Phase 1.

### 3. Run a container

```bash
docker run -d --name nexio -p 5000:5000 nexio-api:0.1
```

Flags explained:
- `-d` — detached mode (run in background)
- `--name nexio` — give the container a memorable name
- `-p 5000:5000` — map port 5000 on your host to port 5000 in the container

### 4. Verify it is running

```bash
docker ps
```

You should see `nexio` in the list with status `Up`.

### 5. Call the API

```bash
# Welcome message
curl http://localhost:5000/

# Health check
curl http://localhost:5000/health

# Sample event
curl http://localhost:5000/event
```

### 6. Inspect the running container

```bash
# Stream live logs
docker logs -f nexio

# Open a shell inside the container
docker exec -it nexio sh

# Inside the container — look around
ls /app
cat /app/app.py
python --version
exit
```

`docker exec` runs a command inside an *already running* container. `-it` gives you an interactive terminal. This is your most powerful debugging tool.

### 7. Pass an environment variable

Stop and remove the current container, then re-run it with a custom `SERVICE_NAME`:

```bash
docker stop nexio && docker rm nexio

docker run -d --name nexio -p 5000:5000 \
  -e SERVICE_NAME=nexio-events \
  nexio-api:0.1

curl http://localhost:5000/health
# {"service": "nexio-events", "status": "healthy"}
```

### 8. Stop and clean up

```bash
docker stop nexio
docker rm nexio
```

Or in one shot:

```bash
docker rm -f nexio
```

---

## Command reference

| Command | What it does |
|---|---|
| `docker build -t name:tag .` | Build an image from a Dockerfile in `.` |
| `docker images` | List local images |
| `docker run -d -p host:container name:tag` | Run a container in the background |
| `docker ps` | List running containers |
| `docker ps -a` | List all containers (including stopped) |
| `docker logs -f name` | Stream container logs |
| `docker exec -it name sh` | Open a shell in a running container |
| `docker stop name` | Gracefully stop a container (SIGTERM) |
| `docker rm name` | Remove a stopped container |
| `docker rm -f name` | Force-stop and remove |
| `docker rmi name:tag` | Remove an image |
| `docker inspect name` | Show full container metadata as JSON |

---

## What this Dockerfile does NOT do yet

This Dockerfile works. But it has several issues that we will fix in later phases:

| Issue | Impact | Fixed in |
|---|---|---|
| Uses `python:3.12` (full image, ~1 GB) | Slow builds, large attack surface | Phase 1 |
| Single-stage build | Image includes build tools not needed at runtime | Phase 1 |
| No `.dockerignore` | Any file in the directory is copied into the image | Phase 1 |
| Runs as `root` | Container breakout = full host access | Phase 3 |
| No `HEALTHCHECK` instruction | Docker can't tell if the app is actually working | Phase 3 |
| No image labels / OCI metadata | No traceability to source commit | Phase 3 |

---

## Troubleshooting

### Port already in use

```
docker: Error response from daemon: driver failed programming external connectivity:
Bind for 0.0.0.0:5000 failed: port is already allocated.
```

Find what's using port 5000 and stop it, or map to a different host port:

```bash
docker run -d --name nexio -p 5001:5000 nexio-api:0.1
# then curl http://localhost:5001/
```

### Container exits immediately

The app crashed on startup. Check the logs:

```bash
docker logs nexio
```

### `curl` returns connection refused

The container may not be running. Verify:

```bash
docker ps -a
# If status is "Exited", check logs:
docker logs nexio
```

---

## Knowledge check

Before moving to Phase 1, make sure you can answer these without looking:

1. What is the difference between an image and a container?
2. Why do we copy `requirements.txt` before `app.py` in the Dockerfile?
3. What does `EXPOSE 5000` actually do? Does it publish the port?
4. What is the difference between `docker stop` and `docker kill`?
5. How do you open a shell in a running container?
6. What flag runs a container in the background?
7. If you change `app.py` and rebuild, which layers will Docker rebuild?

---

## What's next

In **Phase 1**, we measure the cost of our current approach:

```bash
docker images nexio-api
# SIZE   ~1.05 GB
```

Over 1 GB for a 30-line Flask app. We will fix this with **multi-stage builds**, a `.dockerignore` file, and a `slim` base image — bringing the image down to under 100 MB.

---

[Next: Phase 1 — Multi-Stage Builds & Image Optimization →](../phase-1-multistage-builds/README.md)
