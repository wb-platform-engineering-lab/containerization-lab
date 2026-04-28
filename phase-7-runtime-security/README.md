# Phase 7 — Runtime Security & Hardening

> **Concepts introduced:** Linux capabilities, `--cap-drop`, seccomp profiles, `--read-only`, `--tmpfs`, `--security-opt no-new-privileges`, rootless Docker, `docker-bench-security`

---

## Concepts introduced

| Concept | What it is | Why it matters |
|---|---|---|
| **Linux capabilities** | Fine-grained breakdown of root privilege into ~40 distinct permissions | Drop what you don't need — a compromised container with no capabilities can do very little |
| **`--cap-drop ALL`** | Removes every capability from the container | Fail-closed: grant only what is explicitly needed |
| **Seccomp profile** | A whitelist of Linux syscalls the container process is allowed to make | Blocks entire classes of exploits that rely on uncommon syscalls |
| **`--read-only`** | Mounts the container root filesystem as read-only | An attacker who achieves RCE cannot write malware, modify binaries, or establish persistence |
| **`--tmpfs`** | Mounts an in-memory writable filesystem at a specific path | Allows the app to write temporary files without making the whole filesystem writable |
| **`no-new-privileges`** | Prevents the process from gaining additional privileges via `setuid`/`setgid` | Closes a class of privilege escalation attacks even if a setuid binary exists in the image |
| **Rootless Docker** | Running the Docker daemon itself as a non-root user | A daemon compromise does not give an attacker root on the host |

---

## The problem

> *Nexio — 60 engineers. Seven months in.*
>
> The annual external penetration test came back with three critical findings. All three were about containers.
>
> ```
> CRIT-01: All containers run as root (UID 0)
> CRIT-02: Containers have full Linux capabilities including CAP_SYS_ADMIN
> CRIT-03: Container root filesystems are writable — persistence trivially established after RCE
> ```
>
> The pentesters demonstrated CRIT-03 in the debrief: they achieved RCE via a crafted JSON payload, wrote a reverse shell script to `/usr/local/bin/`, and set a cron job. The container kept running. The script survived a pod restart.
>
> *"None of this should have been possible,"* said the CISO. *"The filesystem should be read-only. The process should have no capabilities. Writing to `/usr/local/bin/` should fail at the kernel level."*
>
> The platform team spent the next sprint implementing the CIS Docker Benchmark. Every container in production ran hardened within two weeks.

---

## Architecture

```
Default container (Phase 0–5)
───────────────────────────────────────────────────────
  User: root (UID 0)
  Capabilities: 14 default caps (includes CAP_NET_ADMIN, CAP_SYS_CHROOT...)
  Filesystem: writable (attacker can write malware, install tools)
  Seccomp: Docker default profile (300+ allowed syscalls)
  Privileges: can gain new privileges via setuid binaries

  Attack surface: high. RCE → persistence is trivial.


Hardened container (Phase 7)
───────────────────────────────────────────────────────
  User: nexio (UID 999) — from Phase 3
  Capabilities: NONE  (--cap-drop ALL)
  Filesystem: read-only  (--read-only)
                 + /tmp writable in-memory  (--tmpfs /tmp)
  Seccomp: custom profile (78 allowed syscalls, all others → ERRNO)
  Privileges: --security-opt no-new-privileges

  Attack surface: minimal. RCE cannot write files, cannot make
  unexpected syscalls, cannot escalate to root.
```

---

## Repository structure

```
phase-7-runtime-security/
├── seccomp/
│   └── nexio-seccomp.json   ← custom seccomp allowlist for Flask
└── app/
    ├── Dockerfile            ← same as Phase 5 (non-root, HEALTHCHECK)
    ├── .dockerignore
    ├── app.py
    └── requirements.txt
```

---

## Challenge 1 — Audit the current container with docker-bench-security

`docker-bench-security` runs the CIS Docker Benchmark checks against your running containers and daemon configuration.

### Step 1: Build the Phase 7 image

```bash
docker build -t nexio-api:0.7 phase-7-runtime-security/app/
```

### Step 2: Run a container (default settings — unhardened)

```bash
docker run -d --name nexio -p 5000:5000 nexio-api:0.7
```

### Step 3: Run docker-bench-security

```bash
docker run --rm \
  -v /var/run/docker.sock:/var/run/docker.sock \
  -v /etc:/etc:ro \
  -v /usr/lib/systemd:/usr/lib/systemd:ro \
  --pid=host \
  --label docker_bench_security \
  docker/docker-bench-security
```

Look for findings tagged `[WARN]` under sections 4 and 5 (Container Images and Runtime). Common findings before hardening:

```
[WARN] 4.1  - Ensure a user for the container has been created
[WARN] 5.1  - Ensure AppArmor Profile is applied
[WARN] 5.3  - Ensure Linux Kernel Capabilities are restricted
[WARN] 5.4  - Ensure privileged containers are not used
[WARN] 5.10 - Ensure the container's root filesystem is mounted read-only
[WARN] 5.21 - Ensure the default seccomp profile is applied
```

```bash
docker rm -f nexio
```

---

## Challenge 2 — Drop all Linux capabilities

### Step 1: See what capabilities a default container has

```bash
docker run --rm nexio-api:0.7 \
  python -c "import subprocess; subprocess.run(['cat', '/proc/1/status'])" \
  | grep -E "^Cap"
```

Expected (default Docker caps):
```
CapInh: 0000000000000000
CapPrm: 00000000a80425fb   ← capabilities currently held
CapEff: 00000000a80425fb
CapBnd: 00000000a80425fb
CapAmb: 0000000000000000
```

Decode what those capabilities are:

```bash
docker run --rm nexio-api:0.7 \
  python -c "import subprocess; subprocess.run(['capsh', '--decode=00000000a80425fb'])"
# cap_chown, cap_dac_override, cap_fowner, cap_fsetid, cap_kill,
# cap_setgid, cap_setuid, cap_setpcap, cap_net_bind_service,
# cap_net_raw, cap_sys_chroot, cap_mknod, cap_audit_write, cap_setfcap
```

A Flask API needs exactly **zero** of these. It does not bind to privileged ports, it does not change file ownership, it does not set UIDs.

### Step 2: Run with no capabilities

```bash
docker run -d --name nexio -p 5000:5000 \
  --cap-drop ALL \
  nexio-api:0.7
```

### Step 3: Verify the app still works

```bash
curl http://localhost:5000/health
# {"service": "nexio-api", "status": "healthy"}
```

Port 5000 is not a privileged port — `CAP_NET_BIND_SERVICE` is not needed. The app runs normally with zero capabilities.

### Step 4: Verify capabilities are gone

```bash
docker exec nexio python -c \
  "import subprocess; subprocess.run(['cat', '/proc/1/status'])" \
  | grep "^CapEff"
# CapEff: 0000000000000000   ← all zeros = no capabilities
```

### Step 5: Verify capability-dependent operations fail

```bash
# Try to change file ownership — requires CAP_CHOWN
docker exec nexio python -c \
  "import os; os.chown('/app/app.py', 0, 0)"
# PermissionError: [Errno 1] Operation not permitted
```

```bash
docker rm -f nexio
```

> **When you DO need a capability:** If your app must bind to port 80, add only `CAP_NET_BIND_SERVICE` back: `--cap-drop ALL --cap-add NET_BIND_SERVICE`. Grant the minimum required, nothing more.

---

## Challenge 3 — Mount the filesystem read-only

### Step 1: Run with `--read-only`

```bash
docker run -d --name nexio -p 5000:5000 \
  --cap-drop ALL \
  --read-only \
  nexio-api:0.7
```

### Step 2: Check if the app still works

```bash
curl http://localhost:5000/health
```

Flask itself does not write to the filesystem at runtime — it works.

### Step 3: Verify writes fail at the kernel level

```bash
docker exec nexio python -c "open('/tmp/test.txt', 'w').write('pwned')"
# PermissionError: [Errno 30] Read-only file system: '/tmp/test.txt'
```

An attacker who achieves RCE cannot write a reverse shell, cannot modify a binary, cannot create a cron entry.

### Step 4: Allow writes only where the app legitimately needs them

Some apps write to `/tmp` (temp files, locks, sockets). Use `--tmpfs` to mount an in-memory writable filesystem at exactly the paths the app needs:

```bash
docker rm -f nexio
docker run -d --name nexio -p 5000:5000 \
  --cap-drop ALL \
  --read-only \
  --tmpfs /tmp:size=64m,noexec \
  nexio-api:0.7
```

`noexec` prevents executable files from running even in the tmpfs — so even if someone writes a binary to `/tmp`, they cannot execute it.

### Step 5: Verify /tmp is writable but the rest is not

```bash
docker exec nexio python -c "open('/tmp/test.txt', 'w').write('ok'); print('tmp ok')"
# tmp ok

docker exec nexio python -c "open('/app/pwned.py', 'w').write('x')"
# PermissionError: [Errno 30] Read-only file system: '/app/pwned.py'

docker rm -f nexio
```

---

## Challenge 4 — Apply a custom seccomp profile

Docker's default seccomp profile allows ~300 syscalls. A Python Flask app needs approximately 78. Every additional allowed syscall is a potential exploit surface.

### Step 1: Review the custom profile

```bash
cat phase-7-runtime-security/seccomp/nexio-seccomp.json | jq '{
  defaultAction: .defaultAction,
  allowed_count: (.syscalls[0].names | length)
}'
```

```json
{"defaultAction": "SCMP_ACT_ERRNO", "allowed_count": 78}
```

`SCMP_ACT_ERRNO` means any syscall not on the allowlist returns `EPERM` to the process — the syscall fails, it does not crash the kernel or escalate privileges.

### Step 2: Run with the custom seccomp profile

```bash
docker run -d --name nexio -p 5000:5000 \
  --cap-drop ALL \
  --read-only \
  --tmpfs /tmp:size=64m,noexec \
  --security-opt seccomp=phase-7-runtime-security/seccomp/nexio-seccomp.json \
  nexio-api:0.7
```

### Step 3: Verify the app works

```bash
curl http://localhost:5000/health
curl http://localhost:5000/event
```

### Step 4: Verify a blocked syscall is rejected

```bash
# ptrace is not in the allowlist — attempt to use it
docker exec nexio python -c "
import ctypes
libc = ctypes.CDLL(None)
# PTRACE_TRACEME = 0
result = libc.ptrace(0, 0, 0, 0)
print('ptrace result:', result)
"
# result: -1  ← EPERM — syscall was blocked by seccomp
```

```bash
docker rm -f nexio
```

> **How to build a custom seccomp profile:** Run the app under `strace -e trace=all` and collect all syscalls made during normal operation. That set becomes your allowlist. Add a small buffer for edge cases. The profile in this phase was derived this way.

---

## Challenge 5 — Combine all hardening flags

In production, all flags are applied together. The full `docker run` command for a hardened container:

### Step 1: Run fully hardened

```bash
docker run -d --name nexio-hardened -p 5000:5000 \
  --cap-drop ALL \
  --read-only \
  --tmpfs /tmp:size=64m,noexec \
  --security-opt no-new-privileges \
  --security-opt seccomp=phase-7-runtime-security/seccomp/nexio-seccomp.json \
  nexio-api:0.7
```

| Flag | What it prevents |
|---|---|
| `--cap-drop ALL` | Capability-based privilege escalation |
| `--read-only` | Writing malware, modifying binaries, cron persistence |
| `--tmpfs /tmp:noexec` | Executing uploaded binaries from /tmp |
| `--no-new-privileges` | setuid/setgid privilege escalation |
| `--seccomp=...` | 220+ uncommon syscalls used in kernel exploits |

### Step 2: Run the full verification suite

```bash
# App responds correctly
curl http://localhost:5000/health

# No capabilities
docker exec nexio-hardened python -c \
  "f=open('/proc/1/status'); [print(l.strip()) for l in f if 'Cap' in l]"

# Filesystem is read-only
docker exec nexio-hardened sh -c "echo test > /app/test.txt" 2>&1 || echo "BLOCKED: read-only"

# /tmp is writable
docker exec nexio-hardened python -c "open('/tmp/ok.txt','w').write('ok'); print('tmp writable')"

# /tmp is noexec
docker exec nexio-hardened sh -c "echo '#!/bin/sh' > /tmp/x.sh && chmod +x /tmp/x.sh && /tmp/x.sh" \
  2>&1 || echo "BLOCKED: noexec"

docker rm -f nexio-hardened
```

---

## Challenge 6 — (Advanced) Enable rootless Docker

Rootless Docker runs the Docker daemon itself as a non-root user. A daemon compromise does not yield root on the host.

### Step 1: Check if rootless mode is available

```bash
docker info | grep -i "rootless"
# Rootless: true   ← if already running rootless
```

### Step 2: Install rootless Docker (Linux only)

On macOS, Docker Desktop already runs in a VM — the equivalent concern is different. On Linux:

```bash
# As a non-root user
dockerd-rootless-setuptool.sh install

# Or with the convenience script
curl -fsSL https://get.docker.com/rootless | sh
```

### Step 3: Verify the daemon runs as your user

```bash
ps aux | grep dockerd
# your_user   12345  ... dockerd
```

### Step 4: Understand the trade-offs

| Feature | Rootless Docker | Standard Docker |
|---|---|---|
| Daemon runs as | Your user | root |
| `--cap-add` | Not supported (no root to grant from) | Supported |
| Port < 1024 | Requires `sysctl net.ipv4.ip_unprivileged_port_start` | Supported |
| Overlay filesystem | May require fuse-overlayfs | Native overlayfs |
| Security gain | Daemon compromise ≠ host root | Daemon compromise = host root |

---

## Command reference

| Command | What it does |
|---|---|
| `--cap-drop ALL` | Remove all Linux capabilities |
| `--cap-add CAP_NAME` | Add a specific capability back |
| `--read-only` | Mount root filesystem read-only |
| `--tmpfs /path:opts` | Mount in-memory writable filesystem |
| `--security-opt no-new-privileges` | Prevent setuid privilege escalation |
| `--security-opt seccomp=profile.json` | Apply a custom seccomp filter |
| `docker run --privileged` | Grant ALL capabilities + disable seccomp (never in prod) |
| `capsh --decode=HEX` | Decode a capabilities bitmask |

---

## Production considerations

### 1. Encode hardening flags in Compose or Kubernetes, not in scripts
`docker run` flags are forgotten. In `docker-compose.yml`:
```yaml
security_opt:
  - no-new-privileges:true
  - seccomp:seccomp/nexio-seccomp.json
cap_drop: [ALL]
read_only: true
tmpfs:
  - /tmp:size=64m,noexec
```
In Kubernetes, the equivalent lives in `securityContext`. Never rely on humans to remember runtime flags.

### 2. Test seccomp profiles thoroughly before production
An incomplete seccomp allowlist silently breaks the application — the process receives `EPERM` and may fail without a clear error. Test under production load patterns. Monitor for `EPERM` errors in application logs after switching to a custom profile.

### 3. The CIS Docker Benchmark is a floor, not a ceiling
Running `docker-bench-security` after hardening should yield zero `[WARN]` items for sections 4 and 5. Treat this as the minimum acceptable posture, not the goal. Additional controls (Falco for runtime anomaly detection, network policies) layer on top.

### 4. Rootless mode is the correct production default on Linux
All new Linux deployments should run the Docker daemon rootless. The main blockers — port 80, overlayfs — have workarounds. The security gain (daemon compromise ≠ host root) is substantial. On Kubernetes, `runAsNonRoot: true` and `seccompProfile: RuntimeDefault` achieve similar containment at the pod level.

### 5. `--privileged` is never acceptable in production
`--privileged` disables all security controls: all capabilities are granted, seccomp is disabled, AppArmor/SELinux is disabled. A privileged container is effectively running as root on the host with no isolation. If you see `--privileged` in a production deployment, treat it as a critical finding.

---

## Outcome

The container now runs with zero capabilities, a read-only root filesystem, a custom seccomp allowlist of 78 syscalls, and no-new-privileges protection. An attacker who achieves RCE in this container has no capabilities to abuse, cannot write files, cannot execute uploaded binaries, and cannot make unexpected kernel calls. The CIS Docker Benchmark reports zero critical warnings.

---

[Back to Phase 6](../phase-6-registry/README.md) | [Next: Phase 8 — Advanced Compose Patterns →](../phase-8-advanced-compose/README.md)
