const { createClient } = require("redis");

const REDIS_URL = process.env.REDIS_URL || "redis://localhost:6379";
const QUEUE_KEY = "event_queue";

async function main() {
  const client = createClient({ url: REDIS_URL });

  client.on("error", (err) => console.error("[worker] Redis error:", err));
  await client.connect();
  console.log("[worker] connected to Redis at", REDIS_URL);

  console.log(`[worker] polling queue "${QUEUE_KEY}" — waiting for events...`);

  while (true) {
    // BRPOP blocks up to 5 seconds waiting for an element
    const result = await client.brPop(QUEUE_KEY, 5);
    if (!result) continue;

    let event;
    try {
      event = JSON.parse(result.element);
    } catch {
      console.warn("[worker] received non-JSON payload, skipping:", result.element);
      continue;
    }

    console.log(
      `[worker] processed event id=${event.id} type=${event.type} user=${event.user_id} at=${event.created_at}`
    );
  }
}

main().catch((err) => {
  console.error("[worker] fatal error:", err);
  process.exit(1);
});
