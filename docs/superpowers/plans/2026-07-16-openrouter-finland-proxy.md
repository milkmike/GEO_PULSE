# OpenRouter Finland Proxy Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Restore production OpenRouter calls by routing the three GEO_PULSE AI services through an IP-restricted Finnish HTTPS CONNECT proxy.

**Architecture:** `tinyproxy` runs on the Finnish server and accepts connections only from the GEO_PULSE production IP, enforced by both proxy configuration and the host firewall. Production injects one secret proxy URL as `HTTPS_PROXY` into `analyzer`, `briefs`, and `threads`; all other services and all production data remain untouched.

**Tech Stack:** Ubuntu/Debian systemd, tinyproxy, host firewall, Docker Compose, Python httpx, PostgreSQL.

## Global Constraints

- Never persist or reuse the Finnish root password.
- Never commit the generated proxy credential.
- Do not restart the database, Redis, collectors, API, web, signals, or temperature services.
- Do not delete or rewrite articles, temperature history, signals, stories, or historical briefs.
- Record rollback copies before changing production configuration.
- Abort and roll back if the proxy is reachable from a non-production IP.

---

### Task 1: Install the restricted Finnish proxy

**Files:**
- Modify on Finland: `/etc/tinyproxy/tinyproxy.conf`
- Create on Finland: `/etc/tinyproxy/geopulse-auth.conf`
- Modify on Finland: active host firewall rules

**Interfaces:**
- Consumes: production egress IP `5.42.122.100`
- Produces: authenticated HTTP CONNECT endpoint `http://85.192.31.89:18888`

- [ ] **Step 1: Capture the current host state**

Run read-only checks for OS, active firewall, port `18888`, installed proxy packages, and current public egress IP. Save command output in the task log; do not change services.

- [ ] **Step 2: Install tinyproxy**

Run the distribution package installation non-interactively. Expected: `tinyproxy --version` succeeds and the packaged service exists.

- [ ] **Step 3: Generate a dedicated credential**

Generate the password on the Finnish host with `openssl rand -hex 24`, store it root-readable only, and do not print it in logs. Use proxy username `geopulse`.

- [ ] **Step 4: Configure the proxy**

Configure exactly:

```text
Port 18888
Listen 0.0.0.0
Allow 5.42.122.100
BasicAuth geopulse <generated-runtime-secret>
ConnectPort 443
ConnectPort 563
DisableViaHeader Yes
```

Retain the package's safe defaults for timeouts, logging, and privilege drop. Validate the configuration before restarting the service.

- [ ] **Step 5: Restrict the firewall and start**

Allow TCP `18888` only from `5.42.122.100`, explicitly reject the port from other sources, restart tinyproxy, and verify it listens on the expected port.

- [ ] **Step 6: Verify isolation**

From GEO_PULSE production, the proxy must return HTTP `200` for `https://openrouter.ai/api/v1/models`. From the local workstation, the same proxy endpoint must fail before authentication or CONNECT succeeds.

---

### Task 2: Connect only the production AI services

**Files:**
- Modify on production: `/opt/geopulse/.env`
- Modify on production: `/opt/geopulse/docker-compose.override.yml`
- Create on production: timestamped rollback copies of both files

**Interfaces:**
- Consumes: authenticated proxy URL from Task 1 as `OPENROUTER_HTTPS_PROXY`
- Produces: `HTTPS_PROXY=${OPENROUTER_HTTPS_PROXY}` in `analyzer`, `briefs`, and `threads`

- [ ] **Step 1: Capture the current production state**

Record Git HEAD/deploy marker, Compose service configuration, container IDs/start times, latest world brief timestamp, article count, and temperature count.

- [ ] **Step 2: Save rollback copies**

Create root-readable timestamped copies of `.env` and `docker-compose.override.yml` next to the originals. Do not copy them into Git.

- [ ] **Step 3: Add the secret environment value**

Append or replace one `OPENROUTER_HTTPS_PROXY` line in `.env`. URL-encode the generated password before placing it in the URL. Confirm only the variable name, never its value.

- [ ] **Step 4: Add service-scoped proxy environment**

Merge this structure into the existing override without removing unrelated settings:

```yaml
services:
  analyzer:
    environment:
      HTTPS_PROXY: ${OPENROUTER_HTTPS_PROXY}
  briefs:
    environment:
      HTTPS_PROXY: ${OPENROUTER_HTTPS_PROXY}
  threads:
    environment:
      HTTPS_PROXY: ${OPENROUTER_HTTPS_PROXY}
```

Run `docker compose config --quiet`. Expected: exit code `0`.

- [ ] **Step 5: Recreate only three services**

Run `docker compose up -d --no-deps --force-recreate analyzer briefs threads`. Verify that only these three container IDs/start times changed.

---

### Task 3: Prove recovery and preserve data

**Files:**
- No file changes

**Interfaces:**
- Consumes: proxied AI containers from Task 2
- Produces: current world brief and successful OpenRouter usage records

- [ ] **Step 1: Verify unauthenticated and authenticated OpenRouter endpoints**

Inside `briefs`, request `/api/v1/models` and `/api/v1/key`. Expected: neither response is `403`; the authenticated endpoint returns key metadata without exposing the key.

- [ ] **Step 2: Generate one world brief**

Run the existing generator once with `--world-only --force`. Expected: exit code `0` and a new `briefs(scope='world')` row newer than the pre-deploy timestamp.

- [ ] **Step 3: Verify analyzer and threads**

Wait for one normal processing cycle. Query `api_usage` for fresh `status='ok'` rows from `analyze.py` and `build_threads.py`; inspect logs for absence of new OpenRouter `403` errors.

- [ ] **Step 4: Verify public output and data counts**

Confirm `/api/v2/brief` returns the new timestamp, all services are running, and article/temperature counts are greater than or equal to their pre-change values.

- [ ] **Step 5: Roll back on any failure**

Restore the saved production files, recreate only `analyzer`, `briefs`, and `threads`, disable tinyproxy, and close TCP `18888`. Confirm the original container configuration is restored and report the failed acceptance check.
