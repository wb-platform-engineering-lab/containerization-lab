# Phase 9 — Container-Native Application Design

> **Concepts introduced:** 12-factor app, external config via mounted files, structured logging, SIGTERM graceful shutdown, liveness vs readiness probes, image promotion

---

## Concepts introduced

| Concept | What it is | Why it matters |
|---|---|---|
| **12-factor app** | A methodology for building apps that run well in containers | Stateless, config-via-env, disposable processes — the container model maps directly |
| **External config** | Config loaded from a mounted file, not baked into the image | Same image runs in dev, staging, and prod — only the mounted config changes |
| **Structured logging** | Emitting logs as JSON to stdout | Log aggregators (Loki, Datadog, CloudWatch) parse fields, not regex-scraped text |
| **SIGTERM handler** | The app catches the shutdown signal and drains gracefully | Prevents dropped requests during rolling deploys and `compose down` |
| **Liveness probe** | `/health/live` — is the process alive? | Orchestrators restart the container if this fails |
| **Readiness probe** | `/health/ready` — can the process serve traffic? | Orchestrators stop routing to this container if this returns non-200 |
| **Image promotion** | The same digest moves from dev → staging → prod, config changes | Never rebuild for a different environment — the image is the immutable artifact |

---

## The problem

> *Nexio — 100 engineers. Nine months in.*
>
> The production deploy pipeline rebuilt the Docker image for every environment. Dev build, staging build, prod build — three separate `docker build` runs from the same commit. The images were *almost* identical. Almost.
>
> One day, a pip dependency resolved differently between the staging and prod builds. A transitive dependency had released a patch between the two builds, 40 minutes apart. Staging passed. Prod failed. A feature was delayed by four hours.
>
> The second issue surfaced the same week: a Kubernetes rolling deploy was dropping requests. The new pod started, passed the readiness check, received traffic — and then failed on its first database connection. The readiness probe was hitting `/health` which only checked if the Flask process was up, not if PostgreSQL was reachable.
>
> The third issue: `docker compose down` during a load test lost 12 in-flight requests. Python had no SIGTERM handler — the OS killed it mid-response.
>
> *"The app was not designed for the environment it was running in."*
>
> All three problems had the same root cause: the application had not been written for containers. It was written for a server, then packaged into a container. The fixes were architectural — not packaging.

---

## Architecture

```
Image promotion (one build, three environments)
────────────────────────────────────────────────────────────────────
  git push → CI builds nexio-api:sha-a1b2c3d (ONCE)
  │
  ├── Deploy to dev
  │    └── mount config/dev.yaml → service_name=nexio-api-dev, max_events=50
  │
  ├── Tests pass → promote same digest to staging
  │    └── mount config/staging.yaml → service_name=nexio-api-staging, max_events=20
  │
  └── Staging passes → promote same digest to prod
       └── mount config/prod.yaml → service_name=nexio-api, max_events=20

  The image digest never changes. The config changes.


Health probes (liveness vs readiness)
────────────────────────────────────────────────────────────────────
  GET /health/live   → 200 always (if the process is running)
  GET /health/ready  → 200 if postgres + redis reachable, else 503
                       503 during graceful shutdown (_shutdown=True)

  Kubernetes uses both:
  - livenessProbe:  /health/live  → restart the pod if it fails
  - readinessProbe: /health/ready → stop sending traffic if it fails


Graceful shutdown
────────────────────────────────────────────────────────────────────
  Kubernetes/Compose sends SIGTERM
  ├── _shutdown = True  → readiness probe returns 503
  │    └── load balancer stops routing new requests here
  ├── App finishes in-flight requests
  └── sys.exit(0)  → clean exit
  
  Kubernetes waits terminationGracePeriodSeconds (default 30s) before SIGKILL
  Compose waits stop_grace_period (30s in docker-compose.yml)
```

---

## Repository structure

```
phase-9-container-native/
├── docker-compose.yml           ← mounts config/dev.yaml into the API
├── .env.example
├── config/
│   ├── dev.yaml                 ← service_name=nexio-api-dev, max_events=50
│   └── prod.yaml                ← service_name=nexio-api, max_events=20
├── api/
│   ├── Dockerfile               ← BuildKit, non-root, HEALTHCHECK on /health/live
│   ├── .dockerignore
│   ├── app.py                   ← SIGTERM handler, /health/live + /health/ready, structured logs
│   └── requirements.txt         ← adds pyyaml
└── worker/
    ├── Dockerfile
    └── worker.js
```

---

## Challenge 1 — Read the 12-factor principles and map them to the code

The [12-factor app](https://12factor.net) methodology was written for cloud-native services. Every factor has a direct container equivalent.

### The factors most relevant to containerization

| Factor | What it means | How it appears in this phase |
|---|---|---|
| **III. Config** | Store config in the environment | `DATABASE_URL`, `REDIS_URL` via env; `service_name` via mounted YAML |
| **IV. Backing services** | Treat databases, queues as attached resources | Postgres/Redis via env-var URLs — swappable without code changes |
| **VI. Processes** | Execute the app as stateless, share-nothing processes | Nothing written to the container filesystem; all state in Postgres/Redis |
| **VIII. Concurrency** | Scale via the process model | Scale workers with `--scale worker=N` (Phase 8) |
| **IX. Disposability** | Fast startup and graceful shutdown | SIGTERM handler + `stop_grace_period` |
| **XI. Logs** | Treat logs as event streams | Structured JSON to stdout, never to files |

### Step 1: Review the app structure

```bash
cat phase-9-container-native/api/app.py
```

Look for:
- `load_config()` — loads YAML from a mounted path
- `_handle_sigterm()` — sets `_shutdown = True`, then calls `sys.exit(0)`
- `/health/live` and `/health/ready` — two separate probe endpoints
- `logging.basicConfig` with JSON format to stdout

### Step 2: Review the config files

```bash
cat phase-9-container-native/config/dev.yaml
cat phase-9-container-native/config/prod.yaml
```

Two files, two environments, same image. The image is never rebuilt for a config change.

---

## Challenge 2 — External config via mounted files

### Step 1: Start the stack with dev config

```bash
cd phase-9-container-native
cp .env.example .env
docker compose up --build -d
```

The `docker-compose.yml` mounts `./config/dev.yaml` into the API at `/etc/nexio/config.yaml:ro`.

### Step 2: Verify the config was loaded

```bash
curl http://localhost:5000/health/live
# {"service": "nexio-api-dev", "status": "alive"}
```

`nexio-api-dev` comes from `config/dev.yaml`. The image has no knowledge of this value — it came from a file mounted at runtime.

### Step 3: Simulate a prod deployment with a different config

```bash
docker compose down

# Override the mounted config to prod.yaml
docker run -d --name nexio-prod -p 5000:5000 \
  -e DATABASE_URL="postgresql://nexio:changeme@localhost:5432/nexio" \
  -e REDIS_URL="redis://localhost:6379" \
  -v $(pwd)/config/prod.yaml:/etc/nexio/config.yaml:ro \
  nexio-api-phase9:latest 2>/dev/null || \
  echo "(build the image first: docker build -t nexio-api-phase9 ./api/)"
```

Or more simply — verify the concept by inspecting the config:

```bash
docker compose up -d
docker compose exec api cat /etc/nexio/config.yaml
# service_name: nexio-api-dev
# max_events_returned: 50

docker compose down
```

> **Why a YAML file instead of just environment variables?** For simple string values, env vars are ideal (and should always be the first choice). For structured config — feature flags, nested settings, per-route limits — a YAML file is more readable and easier to review in a pull request. Use env vars for secrets and connection strings; use a config file for structured application settings.

---

## Challenge 3 — Liveness vs readiness probes

### Step 1: Start the stack

```bash
docker compose up --build -d
```

### Step 2: Understand the two endpoints

```bash
# Liveness: is the Python process alive and responding?
curl http://localhost:5000/health/live
# {"service": "nexio-api-dev", "status": "alive"}
# Always 200 as long as Flask is running

# Readiness: can the process serve real traffic?
curl http://localhost:5000/health/ready
# {"checks": {"postgres": "ok", "redis": "ok"}, "status": "ready"}
# 200 only when both dependencies are reachable
```

### Step 3: Simulate a dependency failure and observe readiness

```bash
# Stop Redis — the API should become unready
docker compose stop redis

curl http://localhost:5000/health/ready
# {"checks": {"postgres": "ok", "redis": "error: ..."}, "status": "not_ready"}
# HTTP 503

curl http://localhost:5000/health/live
# {"service": "nexio-api-dev", "status": "alive"}
# HTTP 200 — the process is still alive
```

A Kubernetes `livenessProbe` on `/health/live` would NOT restart this pod — the process is fine.
A Kubernetes `readinessProbe` on `/health/ready` WOULD stop routing traffic here — the app cannot serve requests without Redis.

```bash
docker compose start redis
curl http://localhost:5000/health/ready
# {"status": "ready"}  ← back to normal
```

### Step 4: The Kubernetes equivalents

```yaml
# In a Kubernetes pod spec:
livenessProbe:
  httpGet:
    path: /health/live
    port: 5000
  initialDelaySeconds: 10
  periodSeconds: 30

readinessProbe:
  httpGet:
    path: /health/ready
    port: 5000
  initialDelaySeconds: 5
  periodSeconds: 10
  failureThreshold: 3
```

The DOCKER `HEALTHCHECK` instruction in the Dockerfile maps to the liveness probe concept — it tells Docker whether the container is working. In Kubernetes, both probes are configured in the pod spec, not the Dockerfile.

---

## Challenge 4 — Graceful shutdown under load

### Step 1: Start the stack

```bash
docker compose up --build -d
```

### Step 2: Simulate requests in-flight during a shutdown

In one terminal, send a stream of requests:

```bash
while true; do
  curl -s -X POST http://localhost:5000/event \
    -H "Content-Type: application/json" \
    -d '{"type":"load_test","user_id":"usr_1"}' > /dev/null
  sleep 0.1
done
```

In another terminal, stop the API:

```bash
docker compose stop api
```

### Step 3: Observe the logs

```bash
docker compose logs api | tail -10
```

You should see the structured shutdown log line:

```json
{"time":"...","level":"INFO","message":"Received SIGTERM — starting graceful shutdown"}
```

The SIGTERM handler:
1. Sets `_shutdown = True` — the readiness probe immediately returns 503
2. Calls `sys.exit(0)` — Flask finishes the current request, then exits cleanly

No requests are truncated mid-response because Flask processes one request at a time (single-threaded by default). In a production WSGI server (gunicorn), the worker process finishes its current request before exiting.

### Step 4: Verify the `stop_grace_period` in docker-compose.yml

```bash
grep -A1 "stop_grace_period" phase-9-container-native/docker-compose.yml
```

```yaml
stop_grace_period: 30s
```

Compose sends SIGTERM, waits 30 seconds, then sends SIGKILL. If the app exits before 30 seconds (which it will, after draining), it exits cleanly. The 30-second window guarantees even slow requests complete.

---

## Challenge 5 — Verify structured logging

### Step 1: Send some requests

```bash
curl http://localhost:5000/event \
  -X POST -H "Content-Type: application/json" \
  -d '{"type":"page_view","user_id":"usr_1"}'

curl http://localhost:5000/events
```

### Step 2: Read the logs as structured JSON

```bash
docker compose logs api | grep -v "^$" | tail -10
```

Expected:
```json
{"time":"2026-04-28 10:00:01,123","level":"INFO","message":"Starting nexio-api-dev (config=/etc/nexio/config.yaml, max_events=50)"}
{"time":"2026-04-28 10:00:01,456","level":"INFO","message":"Database schema initialised"}
{"time":"2026-04-28 10:00:05,789","level":"INFO","message":"Ingesting event id=abc-123 type=page_view"}
```

Every log line is valid JSON. A log aggregator (Loki, Datadog, CloudWatch Logs Insights) can:
- Filter by `level=ERROR` without regex
- Group by `type` field
- Alert on specific `message` values

Contrast with unstructured logs:
```
2026-04-28 10:00:05 - INFO - Ingesting event id=abc-123 type=page_view
```

This requires regex to extract fields — fragile, slow to query, and breaks when the message format changes.

### Step 3: Parse logs with jq

```bash
docker compose logs api 2>/dev/null \
  | grep "^nexio-api" \
  | sed 's/nexio-api  | //' \
  | grep "^{" \
  | jq -s '[.[] | select(.level == "INFO")] | length'
```

---

## Challenge 6 — Demonstrate image promotion

This challenge demonstrates the core principle: **build once, deploy everywhere.**

### Step 1: Build the image with a SHA tag

```bash
SHORT_SHA=$(git rev-parse --short HEAD 2>/dev/null || echo "dev")
docker build -t nexio-api:sha-$SHORT_SHA phase-9-container-native/api/
```

### Step 2: Run in "dev" with dev config

```bash
docker run -d --name nexio-dev -p 5000:5000 \
  -e DATABASE_URL="postgresql://nexio:changeme@host.docker.internal:5432/nexio" \
  -e REDIS_URL="redis://host.docker.internal:6379" \
  -v $(pwd)/phase-9-container-native/config/dev.yaml:/etc/nexio/config.yaml:ro \
  nexio-api:sha-$SHORT_SHA

curl http://localhost:5000/health/live
# {"service": "nexio-api-dev", ...}

docker rm -f nexio-dev
```

### Step 3: Run in "prod" with prod config — SAME IMAGE

```bash
docker run -d --name nexio-prod -p 5000:5000 \
  -e DATABASE_URL="postgresql://nexio:changeme@host.docker.internal:5432/nexio" \
  -e REDIS_URL="redis://host.docker.internal:6379" \
  -v $(pwd)/phase-9-container-native/config/prod.yaml:/etc/nexio/config.yaml:ro \
  nexio-api:sha-$SHORT_SHA

curl http://localhost:5000/health/live
# {"service": "nexio-api", ...}   ← prod service name, same image

docker rm -f nexio-prod
```

The digest of `nexio-api:sha-$SHORT_SHA` is identical in both runs. Only the mounted config file changed. There was no rebuild.

```bash
docker compose down
```

---

## Command reference

| Command | What it does |
|---|---|
| `docker compose stop --timeout N` | Send SIGTERM, wait N seconds before SIGKILL |
| `docker run -v ./config/dev.yaml:/etc/nexio/config.yaml:ro` | Mount config read-only |
| `docker compose logs api \| jq -R 'fromjson?'` | Parse JSON log lines |

---

## Production considerations

### 1. Never bake environment-specific config into an image
If the only difference between your dev and prod images is a `DEBUG=false` ENV instruction, that is one thing. But if connection strings, feature flags, or service names are different, they must be injected at runtime — via env vars for secrets and simple values, via mounted files for structured config. An image that must be rebuilt for each environment is not an immutable artifact.

### 2. Liveness and readiness probes serve different purposes — never conflate them
A liveness probe that checks database connectivity will restart a healthy pod every time the database has a transient blip. A readiness probe on a path that always returns 200 will route traffic to a pod that cannot actually serve requests. Design each probe for its purpose: liveness checks the process, readiness checks the dependencies.

### 3. The SIGTERM handler must complete within `terminationGracePeriodSeconds`
In Kubernetes, `terminationGracePeriodSeconds` defaults to 30. If the SIGTERM handler takes longer — because it is waiting for a 45-second database transaction to commit — Kubernetes sends SIGKILL and the transaction is lost. Design handlers to complete within the grace period, or increase the period deliberately and document why.

### 4. Structured logging is infrastructure, not style
JSON logs are a requirement, not a preference. Teams that ship plaintext logs spend engineering time writing log parsers, maintain regex patterns that break on message changes, and cannot query across fields. Enforce JSON logging in the base Docker image or the app framework — not as a per-team convention.

### 5. Stateless processes enable horizontal scaling
A process that writes state to its local filesystem, holds in-memory caches that are not shared, or assumes it is the only instance will break when scaled. The constraint is: anything the process writes locally is gone when the container is replaced. Design accordingly — external state stores only.

---

## Outcome

The application is now designed for the container environment it runs in. Config is injected at runtime via mounted files — the same image promotes from dev to prod without rebuilding. SIGTERM is handled gracefully — no requests are dropped during rolling deploys or `compose down`. Liveness and readiness are separate probes with separate semantics. Every log line is structured JSON emitted to stdout. The app is horizontally scalable, disposable, and environment-agnostic.

---

[Back to Phase 8](../phase-8-advanced-compose/README.md) | [Next: Phase 10 — Capstone: Production Pipeline →](../phase-10-capstone/README.md)
