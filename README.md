# Containerization Lab

A hands-on, phase-by-phase lab for mastering container best practices — from your first `docker build` to production-grade images with multi-platform builds, security scanning, signing, and provenance attestation.

Built around **Nexio** — a fictional real-time event processing SaaS — where each phase is motivated by a real engineering problem the team hit as they grew.

> **New here?** Start with [STORY.md](./STORY.md) to understand the "why" behind every technical decision.

![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)

---

## Tech Stack

![Docker](https://img.shields.io/badge/Docker-2496ED?style=flat&logo=docker&logoColor=white)
![Python](https://img.shields.io/badge/Python-3776AB?style=flat&logo=python&logoColor=white)
![Node.js](https://img.shields.io/badge/Node.js-339933?style=flat&logo=node.js&logoColor=white)
![BuildKit](https://img.shields.io/badge/BuildKit-2496ED?style=flat&logo=docker&logoColor=white)
![Trivy](https://img.shields.io/badge/Trivy-1904DA?style=flat&logo=aquasec&logoColor=white)
![Cosign](https://img.shields.io/badge/Cosign-Sigstore-4285F4?style=flat)
![GitHub Actions](https://img.shields.io/badge/GitHub_Actions-2088FF?style=flat&logo=github-actions&logoColor=white)

---

## The Product — Nexio

Nexio is a B2B real-time event processing API. E-commerce companies send behavioural events (page views, cart updates, checkout starts) and receive enriched, structured payloads in milliseconds — driving personalisation, fraud detection, and live inventory signals.

Each phase is motivated by a real containerization pain that emerged as Nexio scaled:

| Phase | Engineers | Problem |
|---|---|---|
| 0 | 1 | The app only runs on one laptop |
| 1 | 3 | Images are 1 GB — CI pipeline takes 18 minutes |
| 2 | 5 | Running the full stack locally takes 6 manual commands |
| 3 | 10 | Security scan flagged 47 CVEs in the base image |
| 4 | 15 | BuildKit cache isn't shared — every build re-downloads dependencies |
| 5 | 25 | No one can prove a shipped image hasn't been tampered with |
| 6 | 40 | Images pile up in the registry — $300/month in storage |
| 7 | 60 | Pentest report: containers run as root with full Linux capabilities |
| 8 | 80 | Local dev drifts from production — bugs only appear in CI |
| 9 | 100+ | Config is baked into images — can't promote the same artifact across envs |
| 10 | — | Full production pipeline: build → scan → sign → push → provenance |

---

## Progress

| Phase | Topic | Skill Level | Est. Time | Status |
|---|---|---|---|---|
| 0 | Your First Container | Beginner | 1–2 hrs | ✅ Complete |
| 1 | Multi-Stage Builds & Image Optimization | Beginner | 2–3 hrs | ✅ Complete |
| 2 | Multi-Container Apps with Docker Compose | Beginner–Intermediate | 2–3 hrs | ✅ Complete |
| 3 | Production-Ready Images | Intermediate | 3–4 hrs | ✅ Complete |
| 4 | BuildKit & Advanced Build Patterns | Intermediate | 3–4 hrs | ✅ Complete |
| 5 | Container Security Scanning & Signing | Intermediate–Advanced | 3–4 hrs | ✅ Complete |
| 6 | Registry & Image Lifecycle Management | Advanced | 2–3 hrs | ✅ Complete |
| 7 | Runtime Security & Hardening | Advanced | 3–4 hrs | ✅ Complete |
| 8 | Advanced Compose Patterns | Advanced | 2–3 hrs | ✅ Complete |
| 9 | Container-Native Application Design | Expert | 3–4 hrs | ✅ Complete |
| 10 | Capstone — Production Pipeline | Expert | 4–6 hrs | ✅ Complete |

---

## Repository Structure

```
.
├── STORY.md
├── roadmap.md
├── phase-0-first-container/
│   ├── README.md
│   └── app/
│       ├── Dockerfile
│       ├── app.py
│       └── requirements.txt
├── phase-1-multistage-builds/
│   ├── README.md
│   └── app/
│       ├── Dockerfile
│       ├── .dockerignore
│       ├── app.py
│       └── requirements.txt
├── phase-2-compose/               (coming soon)
├── phase-2-compose/
│   ├── README.md
│   ├── docker-compose.yml
│   ├── .env.example
│   ├── api/
│   └── worker/
├── phase-3-production-ready/
│   ├── README.md
│   └── app/
├── phase-4-buildkit/
│   ├── README.md
│   └── app/
├── phase-5-scanning-signing/
│   ├── README.md
│   ├── .github/workflows/scan-sign.yml
│   └── app/
├── phase-6-registry/
│   ├── README.md
│   └── app/
├── phase-7-runtime-security/
│   ├── README.md
│   ├── seccomp/nexio-seccomp.json
│   └── app/
├── phase-8-advanced-compose/
│   ├── README.md
│   ├── docker-compose.yml
│   ├── docker-compose.override.yml
│   ├── docker-compose.prod.yml
│   ├── api/
│   └── worker/
├── phase-9-container-native/
│   ├── README.md
│   ├── docker-compose.yml
│   ├── config/
│   │   ├── dev.yaml
│   │   └── prod.yaml
│   ├── api/
│   └── worker/
└── phase-10-capstone/
    ├── README.md
    ├── .github/workflows/production-pipeline.yml
    └── app/
```

---

## Prerequisites

- Docker Desktop (or Docker Engine on Linux) installed and running
- Basic terminal / shell familiarity
- No prior Docker knowledge required for Phase 0

---

## How to use this lab

Each phase lives in its own directory with a self-contained `README.md` that includes:
- A short narrative putting the problem in context
- Concept explanations
- A step-by-step hands-on walkthrough
- A command reference
- A "what this doesn't do yet" section linking forward to the next phase
- Troubleshooting tips

Start at Phase 0 and work forward. Each phase builds directly on the previous one.
