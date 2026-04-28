# Phase 8 — Advanced Compose Patterns

> **Concepts introduced:** Compose override files, Compose profiles, `watch` mode (hot-reload), multi-file merging with `-f`, `stop_grace_period`

---

## Concepts introduced

| Concept | What it is | Why it matters |
|---|---|---|
| **Override file** | `docker-compose.override.yml` is auto-merged with `docker-compose.yml` | Dev settings (mounts, debug ports) live separately from the base config — never committed together |
| **Compose profiles** | Label services with `profiles: [name]` — they only start when the profile is active | Optional services (debug UIs, test databases) don't start unless explicitly requested |
| **`watch` mode** | Compose monitors source files and syncs/rebuilds on change | Hot-reload without volume mounts — works with the production Dockerfile, not a special dev one |
| **Multi-file `-f`** | `docker compose -f base.yml -f prod.yml up` merges files in order | Explicit environment promotion — prod settings are never accidentally applied in dev |
| **`stop_grace_period`** | How long Compose waits between SIGTERM and SIGKILL | Gives the app time to drain in-flight requests before hard termination |

---

## The problem

> *Nexio — 80 engineers. Eight months in.*
>
> The local dev environment had quietly diverged from production. It started small: an engineer added `DEBUG=true` to the compose file and forgot to revert it. Another mounted the host's source code directly into the container with a volume — the production image was never tested locally. A third added a `redis.conf` override to the Compose file for debugging and committed it.
>
> Then a bug appeared that only reproduced in CI. Three engineers spent two days chasing it. The root cause: the local Compose setup was running a different Redis configuration than production.
>
> *"The base compose file needs to be the truth. Dev conveniences go in an override. Production settings go in a separate file. Nobody touches the base for environment-specific concerns."*

The solution: a three-layer Compose strategy:
- `docker-compose.yml` — the production-like base, committed and stable
- `docker-compose.override.yml` — dev additions, auto-loaded locally, committed but never merged into prod runs
- `docker-compose.prod.yml` — production resource limits and settings, applied explicitly with `-f`

---

## Architecture

```
Compose file merge order
────────────────────────────────────────────────────────────────────
  docker compose up
  └── docker-compose.yml          ← base (always loaded)
      + docker-compose.override.yml  ← auto-loaded if present

  docker compose -f docker-compose.yml up
  └── docker-compose.yml only     ← override excluded

  docker compose -f docker-compose.yml -f docker-compose.prod.yml up
  └── docker-compose.yml          ← base
      + docker-compose.prod.yml   ← production overrides (resource limits, restart=always)


Compose profiles
────────────────────────────────────────────────────────────────────
  docker compose up                     → api, worker, postgres, redis
  docker compose --profile debug up     → + adminer (DB UI)


Watch mode
────────────────────────────────────────────────────────────────────
  docker compose watch
  └── monitors api/app.py
      on change → syncs file into running container (no rebuild)
      on Dockerfile change → rebuilds image and restarts
```

---

## Repository structure

```
phase-8-advanced-compose/
├── docker-compose.yml          ← base: 4 services, production-like
├── docker-compose.override.yml ← dev: source mounts, debug flag, adminer profile
├── docker-compose.prod.yml     ← prod: resource limits, restart=always
├── .env.example
├── api/
│   ├── Dockerfile
│   ├── app.py                  ← reads DEBUG env var
│   └── requirements.txt
└── worker/
    ├── Dockerfile
    └── worker.js
```

---

## Challenge 1 — Understand base + override auto-merging

### Step 1: Review the base file

```bash
cat phase-8-advanced-compose/docker-compose.yml
```

The base defines all four services with no source mounts, `DEBUG=false`, and no debug tools. This is the configuration that CI and production both use.

### Step 2: Review the override file

```bash
cat phase-8-advanced-compose/docker-compose.override.yml
```

The override adds:
- A source code volume mount on the API (enables hot-reload)
- `DEBUG: "true"` for Flask's development server
- An overridden `command` that enables `--reload`
- The `adminer` service (only with `--profile debug`)

### Step 3: See the merged config without starting anything

```bash
cd phase-8-advanced-compose
docker compose config
```

`docker compose config` prints the fully merged, resolved configuration. Verify that `DEBUG: "true"` appears in the API service and that the volume mount is present — even though neither is in `docker-compose.yml` directly.

### Step 4: Start without the override

```bash
docker compose -f docker-compose.yml up --build -d
```

By passing `-f docker-compose.yml` explicitly, Compose does **not** auto-load `docker-compose.override.yml`. The API starts without the source mount and without debug mode — production behaviour.

```bash
curl http://localhost:5000/health
docker compose -f docker-compose.yml down
```

### Step 5: Start with the override (normal dev workflow)

```bash
cp .env.example .env
docker compose up --build -d
```

No `-f` flag — override is auto-loaded. `DEBUG=true` and the source mount are now active.

```bash
curl http://localhost:5000/health
```

---

## Challenge 2 — Use Compose profiles for optional services

Profiles let you define services that are part of the stack but don't start by default. Only when you explicitly activate a profile does the service start.

### Step 1: Start without the debug profile

```bash
docker compose up -d
docker compose ps
```

Expected: `nexio-api`, `nexio-worker`, `nexio-postgres`, `nexio-redis` — no Adminer.

### Step 2: Start with the debug profile

```bash
docker compose --profile debug up -d
docker compose ps
```

Now Adminer is running at `http://localhost:8080`.

```bash
# Log into Adminer:
# System: PostgreSQL
# Server: postgres
# Username: nexio
# Password: (your POSTGRES_PASSWORD from .env)
# Database: nexio
```

### Step 3: Stop only the debug services

```bash
docker compose --profile debug stop adminer
```

Or stop everything:

```bash
docker compose --profile debug down
```

> **Use cases for profiles:** Test databases (only start during test runs), monitoring sidecars (start with `--profile monitoring`), debug proxy (start with `--profile debug`), seeder services (run once with `--profile seed`).

---

## Challenge 3 — Hot-reload with Compose `watch`

`docker compose watch` is a mode (added in Compose v2.22) that monitors source files and syncs changes into running containers — or rebuilds the image — based on rules you define.

### Step 1: Add `develop.watch` rules to the override

Compose watch rules are typically defined in the compose file. To test, add them inline to the override (they are already shown below as a conceptual pattern — if your Compose version supports it, add to `docker-compose.override.yml`):

```yaml
services:
  api:
    develop:
      watch:
        - action: sync         # sync the file into the container without restart
          path: ./api/app.py
          target: /app/app.py
        - action: rebuild      # rebuild the image when dependencies change
          path: ./api/requirements.txt
```

### Step 2: Start in watch mode

```bash
docker compose watch
```

Compose starts the stack and begins monitoring. It shows which files are being watched.

### Step 3: Edit `app.py` and observe the sync

Open `phase-8-advanced-compose/api/app.py` in your editor. Change the welcome message:

```python
"message": "Welcome to Nexio. CHANGED.",
```

Save the file. Compose detects the change and syncs it into the running container. With `DEBUG=true` and Flask's `--reload` flag, Flask detects the file change and restarts its internal server.

```bash
curl http://localhost:5000/
# {"message": "Welcome to Nexio. CHANGED.", ...}
```

No rebuild. No `docker compose restart`. The change appeared in under a second.

### Step 4: Edit `requirements.txt` and observe the rebuild

Add a comment to `requirements.txt` (or a new package). Compose detects the change to a file matched by `action: rebuild` and triggers a full image rebuild — because a dependency change requires a new image layer.

---

## Challenge 4 — Apply the production override with `-f`

### Step 1: Review the production override

```bash
cat phase-8-advanced-compose/docker-compose.prod.yml
```

It adds:
- `deploy.resources.limits` — CPU and memory caps per service
- `restart: always` — restart on any exit, not just on failure
- No source mounts, no debug flags

### Step 2: Simulate a production start

```bash
docker compose \
  -f docker-compose.yml \
  -f docker-compose.prod.yml \
  up --build -d
```

`docker-compose.override.yml` is NOT loaded — because we passed `-f` explicitly.

### Step 3: Verify resource limits are applied

```bash
docker inspect nexio-api | jq '.[0].HostConfig | {
  Memory: .Memory,
  CpuQuota: .CpuQuota
}'
```

Expected:
```json
{"Memory": 268435456, "CpuQuota": 50000}
```

268MB memory limit, 50% CPU quota.

```bash
docker compose -f docker-compose.yml -f docker-compose.prod.yml down
```

---

## Challenge 5 — Understand `stop_grace_period`

When you run `docker compose down` or `docker compose stop`, Compose sends SIGTERM to each container. It then waits `stop_grace_period` seconds before sending SIGKILL. If the app needs time to finish in-flight requests, this grace period must be long enough.

### Step 1: Set a short grace period and observe truncation

```bash
# Start the stack
docker compose up -d

# Stop with a 1-second grace period (shorter than any request can complete)
docker compose stop --timeout 1 api
```

```bash
docker logs nexio-api | tail -5
```

With a 1-second grace period and a request in-flight, the app is killed before the response is sent. The client receives a connection reset.

### Step 2: The correct setting in Compose

```yaml
# In docker-compose.yml
api:
  stop_grace_period: 30s
```

30 seconds matches the upstream load balancer's connection drain timeout. After receiving SIGTERM, the app stops accepting new connections, finishes existing ones, and exits cleanly within 30 seconds. The companion to this in Phase 9 is the SIGTERM handler in `app.py`.

---

## Challenge 6 — Scale a service

```bash
docker compose up -d

# Scale the worker to 3 instances
docker compose up -d --scale worker=3

docker compose ps
```

Expected:
```
nexio-worker-1   Up
nexio-worker-2   Up
nexio-worker-3   Up
```

All three workers share the same Redis queue. `BRPOP` in Redis is atomic — each event is consumed by exactly one worker. No duplicate processing.

```bash
# Ingest 10 events
for i in $(seq 1 10); do
  curl -s -X POST http://localhost:5000/event \
    -H "Content-Type: application/json" \
    -d "{\"type\": \"page_view\", \"user_id\": \"usr_$i\"}" > /dev/null
done

# Watch all three workers share the load
docker compose logs worker
```

```bash
docker compose down
```

> **Scaling limitations in Compose:** Compose scaling works for stateless services. Do not scale stateful services (postgres, redis) — they require cluster-aware images (e.g. Patroni for PostgreSQL, Redis Cluster). Horizontal scaling of stateful services is a Kubernetes concern.

---

## Command reference

| Command | What it does |
|---|---|
| `docker compose config` | Print the merged configuration (all loaded files) |
| `docker compose -f a.yml -f b.yml up` | Load specific files (no auto-override) |
| `docker compose --profile name up` | Start services in the named profile |
| `docker compose watch` | Hot-reload: sync files or rebuild on change |
| `docker compose up --scale service=N` | Run N instances of a service |
| `docker compose stop --timeout N` | Override the grace period for this stop |

---

## Production considerations

### 1. The base file is the single source of truth
Treat `docker-compose.yml` as a contract shared between dev and production. If a setting only makes sense in one environment, it belongs in an override file — not in the base. Code review on `docker-compose.yml` matters as much as code review on application code.

### 2. Never auto-load production overrides
Production settings should be applied explicitly: `docker compose -f docker-compose.yml -f docker-compose.prod.yml up`. If production settings were in `docker-compose.override.yml`, any developer running `docker compose up` would accidentally apply them locally — and you'd discover the mistake at the worst time.

### 3. `watch` mode requires Compose v2.22+
`docker compose watch` and the `develop.watch` key are relatively new. Pin your Compose version in CI and document the minimum version in the README. Developers on older installations will see confusing errors.

### 4. Resource limits prevent noisy-neighbour problems
Without `deploy.resources.limits`, a runaway worker can consume all CPU on the host and starve the API. Memory limits trigger an OOM kill instead of a hung system. Apply limits to every service — start conservative and tune up if the app legitimately needs more.

### 5. Compose is for single-host deployments
Multi-file Compose patterns are the right tool for local dev and single-server deployments. At the point where you need multiple hosts, rolling deployments, or automatic rescheduling, the transition to Kubernetes is warranted — and the patterns from Compose (services, networks, volumes, health checks) translate directly to Kubernetes concepts.

---

## Outcome

The dev environment exactly mirrors production behaviour in the base Compose file. Dev conveniences (source mounts, debug mode, Adminer) are isolated in the override file and never accidentally applied in CI or production. Optional services start only when their profile is activated. Source changes apply instantly via watch mode without rebuilding. Production resource limits are applied with an explicit `-f` flag. The two environments can no longer drift silently.

---

[Back to Phase 7](../phase-7-runtime-security/README.md) | [Next: Phase 9 — Container-Native Application Design →](../phase-9-container-native/README.md)
