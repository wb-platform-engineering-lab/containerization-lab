import json
import os
import uuid
from datetime import datetime, timezone

import psycopg2
import redis
from flask import Flask, jsonify, request

app = Flask(__name__)

SERVICE_NAME = os.getenv("SERVICE_NAME", "nexio-api")
DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://nexio:nexio@localhost:5432/nexio")
REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379")
DEBUG_MODE = os.getenv("DEBUG", "false").lower() == "true"

redis_client = redis.from_url(REDIS_URL, decode_responses=True)


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


@app.route("/health")
def health():
    checks = {}
    try:
        redis_client.ping()
        checks["redis"] = "healthy"
    except Exception as exc:
        checks["redis"] = f"unhealthy: {exc}"
    try:
        conn = get_db()
        conn.close()
        checks["postgres"] = "healthy"
    except Exception as exc:
        checks["postgres"] = f"unhealthy: {exc}"

    overall = "healthy" if all(v == "healthy" for v in checks.values()) else "degraded"
    return jsonify({"status": overall, "service": SERVICE_NAME, "checks": checks})


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
        "SELECT id, type, user_id, created_at FROM events ORDER BY created_at DESC LIMIT 20"
    )
    rows = cur.fetchall()
    cur.close()
    conn.close()
    return jsonify([
        {"id": r[0], "type": r[1], "user_id": r[2], "created_at": r[3].isoformat()}
        for r in rows
    ])


if __name__ == "__main__":
    init_db()
    app.run(host="0.0.0.0", port=5000, debug=DEBUG_MODE)
