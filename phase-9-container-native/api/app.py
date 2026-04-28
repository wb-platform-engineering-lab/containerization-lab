import json
import logging
import os
import signal
import sys
import uuid
from datetime import datetime, timezone

import psycopg2
import redis
import yaml
from flask import Flask, jsonify, request

# ── Structured logging ──────────────────────────────────────────────────────
logging.basicConfig(
    stream=sys.stdout,
    level=logging.INFO,
    format='{"time":"%(asctime)s","level":"%(levelname)s","message":"%(message)s"}',
)
log = logging.getLogger(__name__)

app = Flask(__name__)

# ── External config ─────────────────────────────────────────────────────────
# Config is loaded from a mounted file, not baked into the image.
# The same image runs in dev, staging, and production — only the config changes.
CONFIG_PATH = os.getenv("CONFIG_PATH", "/etc/nexio/config.yaml")

def load_config():
    try:
        with open(CONFIG_PATH) as f:
            return yaml.safe_load(f)
    except FileNotFoundError:
        log.warning("Config file not found at %s, using defaults", CONFIG_PATH)
        return {}

config = load_config()

SERVICE_NAME = config.get("service_name") or os.getenv("SERVICE_NAME", "nexio-api")
DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://nexio:nexio@localhost:5432/nexio")
REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379")
MAX_EVENTS = config.get("max_events_returned", 20)

redis_client = redis.from_url(REDIS_URL, decode_responses=True)
_shutdown = False


# ── Graceful shutdown ───────────────────────────────────────────────────────
def _handle_sigterm(signum, frame):
    """
    Handle SIGTERM from Docker/Kubernetes. Gives in-flight requests time to
    complete before the process exits. Sets _shutdown so the readiness check
    can start returning 503 immediately, draining the load balancer.
    """
    global _shutdown
    log.info("Received SIGTERM — starting graceful shutdown")
    _shutdown = True
    # In production, sleep here for the load balancer drain period (e.g. 5s)
    # before actually exiting, so no new requests are routed to this instance.
    sys.exit(0)

signal.signal(signal.SIGTERM, _handle_sigterm)
signal.signal(signal.SIGINT, _handle_sigterm)


# ── Database ────────────────────────────────────────────────────────────────
def get_db():
    return psycopg2.connect(DATABASE_URL)


def init_db():
    conn = get_db()
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS events (
            id          VARCHAR PRIMARY KEY,
            type        VARCHAR NOT NULL,
            user_id     VARCHAR NOT NULL,
            payload     JSONB,
            created_at  TIMESTAMPTZ DEFAULT NOW()
        )
    """)
    conn.commit()
    cur.close()
    conn.close()
    log.info("Database schema initialised")


# ── Routes ──────────────────────────────────────────────────────────────────
@app.route("/health/live")
def liveness():
    """Liveness: is the process alive? Returns 200 always (if running)."""
    return jsonify({"status": "alive", "service": SERVICE_NAME})


@app.route("/health/ready")
def readiness():
    """
    Readiness: can the process serve traffic? Returns 503 during shutdown
    or if dependencies are unavailable. Orchestrators stop routing here
    when this returns non-200.
    """
    if _shutdown:
        return jsonify({"status": "shutting_down"}), 503

    checks = {}
    try:
        redis_client.ping()
        checks["redis"] = "ok"
    except Exception as exc:
        checks["redis"] = f"error: {exc}"
    try:
        conn = get_db()
        conn.close()
        checks["postgres"] = "ok"
    except Exception as exc:
        checks["postgres"] = f"error: {exc}"

    ready = all(v == "ok" for v in checks.values())
    code = 200 if ready else 503
    return jsonify({"status": "ready" if ready else "not_ready", "checks": checks}), code


@app.route("/event", methods=["POST"])
def ingest_event():
    data = request.get_json(force=True)
    event = {
        "id": str(uuid.uuid4()),
        "type": data.get("type", "unknown"),
        "user_id": data.get("user_id", "anonymous"),
        "payload": data.get("properties", {}),
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    log.info("Ingesting event id=%s type=%s", event["id"], event["type"])

    conn = get_db()
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO events (id, type, user_id, payload) VALUES (%s, %s, %s, %s)",
        (event["id"], event["type"], event["user_id"], json.dumps(event["payload"])),
    )
    conn.commit()
    cur.close()
    conn.close()

    redis_client.lpush("event_queue", json.dumps(event))
    return jsonify({"status": "accepted", "event_id": event["id"]}), 201


@app.route("/events")
def list_events():
    conn = get_db()
    cur = conn.cursor()
    cur.execute(
        "SELECT id, type, user_id, created_at FROM events ORDER BY created_at DESC LIMIT %s",
        (MAX_EVENTS,),
    )
    rows = cur.fetchall()
    cur.close()
    conn.close()
    return jsonify([
        {"id": r[0], "type": r[1], "user_id": r[2], "created_at": r[3].isoformat()}
        for r in rows
    ])


if __name__ == "__main__":
    log.info("Starting %s (config=%s, max_events=%s)", SERVICE_NAME, CONFIG_PATH, MAX_EVENTS)
    init_db()
    app.run(host="0.0.0.0", port=5000, debug=False)
