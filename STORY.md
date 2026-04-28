# The Nexio Story

---

Nexio started as a three-person team in a co-working space in Lyon.

The idea was simple: a real-time event processing API that let e-commerce companies react to customer behaviour in milliseconds — abandoned cart, page dwell time, live inventory signals. The kind of thing that used to require a data warehouse and a Monday morning report now delivered as a webhook.

The MVP took four weeks. A Python backend, a Node.js worker, a Redis queue, a PostgreSQL store. It ran fine on the lead engineer's MacBook Pro. She had Python 3.11 installed, the right pip version, the right Node version, a running Redis she'd forgotten she installed six months ago.

Then the second engineer joined. He spent two days trying to get it to start. Then the third joined. Same story.

*"We can't keep onboarding people like this,"* said the lead engineer. She opened a new file and typed `FROM python:3.12`.

That weekend, everything ran in containers.

---

Each phase of this lab is motivated by a real containerization problem that emerged as Nexio grew:

| Phase | Engineers | Problem |
|---|---|---|
| 0 | 1 | The app only runs on one laptop |
| 1 | 3 | Images are 2 GB — the CI pipeline takes 18 minutes |
| 2 | 5 | Running the full stack locally takes 6 manual commands |
| 3 | 10 | Security scan flagged 47 CVEs in our base image |
| 4 | 15 | BuildKit cache isn't shared — every build pulls dependencies from scratch |
| 5 | 25 | No one can prove a shipped image hasn't been tampered with |
| 6 | 40 | Images pile up in the registry — no lifecycle policy, $300/month in storage |
| 7 | 60 | Pentest report: containers run as root with full Linux capabilities |
| 8 | 80 | Local dev environment drifts from production — bugs only appear in CI |
| 9 | 100+ | App config is baked into images — can't promote the same artifact across environments |
| 10 | — | Full production pipeline: build → scan → sign → push → provenance |
