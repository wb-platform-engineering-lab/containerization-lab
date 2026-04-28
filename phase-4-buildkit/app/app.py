import os
from flask import Flask, jsonify

app = Flask(__name__)

SERVICE_NAME = os.getenv("SERVICE_NAME", "nexio-api")
VERSION = "0.1.0"


@app.route("/")
def index():
    return jsonify({
        "service": SERVICE_NAME,
        "version": VERSION,
        "message": "Welcome to Nexio. Real-time events, zero friction.",
    })


@app.route("/health")
def health():
    return jsonify({"status": "healthy", "service": SERVICE_NAME})


@app.route("/event", methods=["GET"])
def event():
    return jsonify({
        "event_id": "evt_demo_001",
        "type": "page_view",
        "user_id": "usr_42",
        "properties": {
            "page": "/checkout",
            "dwell_ms": 4820,
        },
    })


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=False)
