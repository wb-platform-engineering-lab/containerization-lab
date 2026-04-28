# Phase 2 — Multi-Container Apps with Docker Compose

> **Concepts introduced:** `docker-compose.yml`, services, named networks, named volumes, `healthcheck`, `depends_on` with conditions, `.env` files

---

## Concepts introduced

| Concept | What it is | Why it matters |
|---|---|---|
| **Service** | A named container definition in `docker-compose.yml` | Declarative — describe the desired state, not the commands to achieve it |
| **Named network** | A user-defined Docker bridge network shared by services | Services reach each other by service name, not IP address |
| **Named volume** | A Docker-managed persistent filesystem attached to a container | Data survives `docker compose down`; lost with `docker compose down -v` |
| **`healthcheck`** | A command Docker polls to determine if a container is ready | Enables `depends_on: condition: service_healthy` — correct startup ordering |
| **`depends_on`** | Declares a startup dependency between services | With `condition: service_healthy`, a service only starts after its dependency is accepting traffic |
| **`.env` file** | Key-value file that Compose reads for variable substitution | Keeps secrets out of `docker-compose.yml` and out of version control |

---

## The problem

> *Nexio — 5 engineers. Two months after Phase 1.*
>
> The stack had grown. The API now needed Redis for the event queue and PostgreSQL for storage. The worker consumed from Redis and wrote processed records.
>
> Running it locally meant six commands in the right order, with the right flags, with the right environment variables. Only one engineer had them memorised. Everyone else either bothered her or spent 20 minutes reading old Slack messages.
>
> ```bash
> docker network create backend
> docker run -d --name nexio-postgres --network backend \
>   -e POSTGRES_DB=nexio -e POSTGRES_USER=nexio -e POSTGRES_PASSWORD=secret \
>   -v postgres_data:/var/lib/postgresql/data postgres:16-alpine
> docker run -d --name nexio-redis --network backend \
>   -v redis_data:/data redis:7-alpine
> # wait... is postgres actually ready? let me try the api and see if it crashes
> docker run -d --name nexio-api --network backend \
>   -p 5000:5000 \
>   -e DATABASE_URL=postgresql://nexio:secret@nexio-postgres:5432/nexio \
>   -e REDIS_URL=redis://nexio-redis:6379 \
>   nexio-api:0.1
> docker run -d --name nexio-worker --network backend \
>   -e REDIS_URL=redis://nexio-redis:6379 \
>   nexio-worker:0.1
> ```
>
> *"We need one command. One file. Anyone can run it."*

The decision: Docker Compose. The entire stack — four services, two volumes, one network — described in a single YAML file.

---

## Architecture

```
docker compose up
│
├── postgres (nexio-postgres)
│   ├── image: postgres:16-alpine
│   ├── volume: postgres_data → /var/lib/postgresql/data
│   ├── network: backend
│   └── healthcheck: pg_isready -U nexio
│
├── redis (nexio-redis)
│   ├── image: redis:7-alpine
│   ├── volume: redis_data → /data
│   ├── network: backend
│   └── healthcheck: redis-cli ping
│
├── api (nexio-api)          ← waits for postgres healthy + redis healthy
│   ├── build: ./api
│   ├── port: 5000:5000
│   ├── env: DATABASE_URL, REDIS_URL
│   └── network: backend
│        POST /event → INSERT INTO postgres + LPUSH redis queue
│        GET  /events → SELECT FROM postgres
│        GET  /health → pings postgres + redis
│
└── worker (nexio-worker)    ← waits for redis healthy
    ├── build: ./worker
    ├── env: REDIS_URL
    └── network: backend
         BRPOP event_queue → logs processed events
```

---

## Repository structure

```
phase-2-compose/
├── docker-compose.yml     ← four-service stack definition
├── .env.example           ← template for secrets (copy to .env)
├── api/
│   ├── Dockerfile         ← multi-stage Python build
│   ├── .dockerignore
│   ├── app.py             ← Flask API with Redis + PostgreSQL
│   └── requirements.txt   ← flask, psycopg2-binary, redis
└── worker/
    ├── Dockerfile         ← multi-stage Node.js build
    ├── .dockerignore
    ├── worker.js          ← polls Redis queue, logs processed events
    └── package.json
```

---

## Challenge 1 — Understand the stack

### Step 1: Review the API

```bash
cat phase-2-compose/api/app.py
```

The API has three meaningful endpoints now:

| Endpoint | Method | What it does |
|---|---|---|
| `/health` | GET | Pings Redis and PostgreSQL; returns `healthy` or `degraded` |
| `/event` | POST | Inserts into PostgreSQL and pushes to Redis queue |
| `/events` | GET | Returns the last 20 events from PostgreSQL |

The API uses the service name `postgres` and `redis` as hostnames — not `localhost`. Inside a Docker network, container names (and Compose service names) resolve via Docker's embedded DNS. `localhost` inside the API container refers to the API container itself.

### Step 2: Review the worker

```bash
cat phase-2-compose/worker/worker.js
```

The worker uses `BRPOP` — a blocking Redis pop that waits up to 5 seconds for an element. This is more efficient than a polling loop with `sleep`. When the API pushes to `event_queue` with `LPUSH`, the worker receives it immediately.

### Step 3: Review the Compose file

```bash
cat phase-2-compose/docker-compose.yml
```

Notice the `depends_on` structure for the API:

```yaml
depends_on:
  postgres:
    condition: service_healthy
  redis:
    condition: service_healthy
```

`condition: service_healthy` means Compose waits until the `healthcheck` command passes before starting the API container. Without this, the API starts immediately, attempts a database connection before PostgreSQL is ready, and crashes.

> **Why not just use `depends_on` without a condition?** The default `depends_on` only waits for the container to *start* — not for it to be *ready*. PostgreSQL takes a few seconds to initialize after the process starts. Without health checks, you have a race condition.

---

## Challenge 2 — Configure environment and start the stack

### Step 1: Create your `.env` file

```bash
cp phase-2-compose/.env.example phase-2-compose/.env
```

Edit `.env` and set a password:

```
POSTGRES_PASSWORD=changeme
```

> **Why `.env` instead of hardcoding in `docker-compose.yml`?** `docker-compose.yml` is committed to version control. Passwords in it are exposed to everyone with repo access, forever. `.env` is gitignored — it lives only on the machine running the stack.

Verify `.env` is in `.gitignore`:

```bash
grep -n '\.env' /Users/will/Documents/Gitlab/labs/devops-labs/containerization-lab/.gitignore
```

### Step 2: Start the stack

```bash
cd phase-2-compose
docker compose up --build
```

`--build` forces images to be rebuilt. Drop it on subsequent runs if the code hasn't changed.

Watch the startup sequence in the logs. You will see:
1. `postgres` starts, runs its initialization, passes the health check
2. `redis` starts, passes its health check
3. `api` starts (after both are healthy), initializes the database schema
4. `worker` starts (after Redis is healthy), begins polling the queue

### Step 3: Run in detached mode

```bash
docker compose up --build -d
```

`-d` returns control to the terminal. All services run in the background.

---

## Challenge 3 — Verify the stack

### Step 1: Check all services are running

```bash
docker compose ps
```

Expected:
```
NAME             IMAGE                STATUS                   PORTS
nexio-api        phase-2-compose-api  Up (healthy)             0.0.0.0:5000->5000/tcp
nexio-postgres   postgres:16-alpine   Up (healthy)
nexio-redis      redis:7-alpine       Up (healthy)
nexio-worker     phase-2-compose...   Up
```

### Step 2: Check the API health

```bash
curl http://localhost:5000/health
```

Expected:
```json
{"checks": {"postgres": "healthy", "redis": "healthy"}, "service": "nexio-api", "status": "healthy"}
```

### Step 3: Ingest an event

```bash
curl -s -X POST http://localhost:5000/event \
  -H "Content-Type: application/json" \
  -d '{"type": "page_view", "user_id": "usr_42", "properties": {"page": "/checkout"}}'
```

Expected:
```json
{"event_id": "a1b2c3...", "status": "accepted"}
```

### Step 4: Watch the worker process it

```bash
docker compose logs worker
```

Expected:
```
nexio-worker  | [worker] connected to Redis at redis://redis:6379
nexio-worker  | [worker] polling queue "event_queue" — waiting for events...
nexio-worker  | [worker] processed event id=a1b2c3... type=page_view user=usr_42 at=2026-04-28T...
```

### Step 5: Retrieve stored events

```bash
curl http://localhost:5000/events
```

The event is now stored in PostgreSQL and returned in the response.

---

## Challenge 4 — Explore Compose operations

### Follow logs for a specific service

```bash
docker compose logs -f api
```

### Open a shell in a running service

```bash
docker compose exec api sh
```

This is the Compose equivalent of `docker exec -it nexio-api sh`. Useful for debugging — inspect environment variables, test database connections, check file contents.

```bash
# Inside the api container
env | grep DATABASE_URL
python -c "import psycopg2; psycopg2.connect('$DATABASE_URL')" && echo "DB OK"
exit
```

### Connect directly to PostgreSQL

```bash
docker compose exec postgres psql -U nexio -d nexio
```

```sql
-- List tables
\dt

-- Query events
SELECT id, type, user_id, created_at FROM events ORDER BY created_at DESC;

-- Exit
\q
```

### Connect directly to Redis

```bash
docker compose exec redis redis-cli
```

```
127.0.0.1:6379> LLEN event_queue
(integer) 0           ← 0 means the worker consumed everything

127.0.0.1:6379> KEYS *
127.0.0.1:6379> exit
```

---

## Challenge 5 — Verify volume persistence

Named volumes survive `docker compose down`. This is the difference between restarting services and destroying data.

### Step 1: Stop the stack (keep volumes)

```bash
docker compose down
```

Services are stopped and removed. Volumes are untouched.

### Step 2: Restart the stack

```bash
docker compose up -d
```

### Step 3: Verify data persisted

```bash
curl http://localhost:5000/events
```

The events you ingested before the restart are still there — they survived in the named volume `postgres_data`.

### Step 4: Destroy everything including volumes

```bash
docker compose down -v
```

`-v` removes named volumes. Now the database is gone. On the next `compose up`, PostgreSQL starts fresh.

> **Never use `-v` in production.** It is permanently destructive. In production, volumes back databases that took months to populate.

---

## Challenge 6 — Simulate a dependency failure

### Step 1: Kill Redis while the worker is running

```bash
docker compose up -d

# Kill Redis
docker compose stop redis
```

### Step 2: Watch the worker reconnect

```bash
docker compose logs -f worker
```

The worker will log reconnection errors. The `restart: on-failure` policy in `docker-compose.yml` will restart it automatically.

### Step 3: Observe the API degradation

```bash
curl http://localhost:5000/health
```

Expected:
```json
{"checks": {"postgres": "healthy", "redis": "unhealthy: ..."}, "status": "degraded"}
```

The API correctly reports partial health — it can still serve reads from PostgreSQL, but event ingestion is broken.

### Step 4: Restore Redis

```bash
docker compose start redis
docker compose logs -f worker   # watch it reconnect
```

---

## Command reference

| Command | What it does |
|---|---|
| `docker compose up --build` | Build images and start all services |
| `docker compose up -d` | Start in detached mode (background) |
| `docker compose down` | Stop and remove containers and networks |
| `docker compose down -v` | Also remove named volumes |
| `docker compose ps` | List service status and ports |
| `docker compose logs -f name` | Follow logs for a service |
| `docker compose exec name sh` | Shell into a running service |
| `docker compose stop name` | Stop a specific service |
| `docker compose start name` | Start a stopped service |
| `docker compose restart name` | Restart a specific service |
| `docker compose build` | Rebuild images without starting |

---

## Production considerations

### 1. Never put real credentials in `docker-compose.yml`
Use `.env` for local dev, but in production use proper secret management: Docker Swarm secrets, Kubernetes Secrets (preferably from Vault), or a cloud secret manager. The `docker-compose.yml` should contain only non-sensitive defaults and `${VARIABLE}` references.

### 2. Health checks are not optional in production
Without a health check, `depends_on: condition: service_healthy` falls back to `service_started` — which does not wait for readiness. Every service that other services depend on must have a meaningful health check. A health check that only verifies the process is running (not that it can serve traffic) is not a real health check.

### 3. Named volumes require a backup strategy
`postgres_data` is managed by Docker on the host filesystem. If the host dies, the data is gone. In production, use a managed database (Cloud SQL, RDS) or attach persistent disks with snapshot policies. Compose volumes are appropriate for local development only.

### 4. `restart: on-failure` is not a substitute for reliability
`restart: on-failure` catches crashes. It does not handle slow dependencies, network partitions, or cascading failures. In production Kubernetes, liveness and readiness probes, PodDisruptionBudgets, and circuit breakers handle this — covered in later phases.

### 5. Service-to-service communication uses service names, not `localhost`
Inside a Docker Compose network, each service is reachable by its name (e.g. `postgres`, `redis`). This DNS resolution is provided by Docker's embedded DNS server. `localhost` inside a container refers to that container only. This is a common source of confusion when moving from a single-machine setup to a multi-container one.

---

## Outcome

A four-service stack — API, worker, PostgreSQL, Redis — running with a single `docker compose up`. Startup ordering is correct because health checks drive `depends_on`. Data survives restarts via named volumes. Configuration is injected via `.env`. Any engineer can clone the repo and have a working local environment in under two minutes.

---

[Back to Phase 1](../phase-1-multistage-builds/README.md) | [Next: Phase 3 — Production-Ready Images →](../phase-3-production-ready/README.md)
