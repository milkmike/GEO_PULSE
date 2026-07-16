# Fast OpenRouter proxy through Finland

## Goal

Restore GEO_PULSE OpenRouter access as quickly as possible without moving the
database, public API, or web application. The proxy must not be usable by the
public and must be removable without touching production data.

## Chosen design

Run `tinyproxy` on the Finnish server `85.192.31.89`. It listens on a dedicated
TCP port and accepts connections only from the current GEO_PULSE production
egress IP `5.42.122.100`. Enforce the restriction twice: in the host firewall
and in `tinyproxy`'s `Allow` rules. Use a generated proxy credential; never
store or reuse the Finnish root password.

Set `HTTPS_PROXY` only for the `analyzer`, `briefs`, and `threads` services.
This is deliberately broader than proxying only the OpenRouter hostname: all
outbound HTTPS from those three containers will use Finland. It is the fastest
option because Python `httpx` already honours `HTTPS_PROXY` and no application
code needs to change. The collector, API, web, database, Redis, signals, and
temperature services remain on their existing network path.

HTTPS payloads remain end-to-end encrypted through HTTP CONNECT. The proxy sees
the destination hostname but not API keys or request content.

## Deployment sequence

1. Record current firewall, proxy, Compose, container, and brief timestamps.
2. Install and configure `tinyproxy` on the Finnish host.
3. Add the production IP allowlist and verify the proxy is inaccessible from a
   different source IP.
4. Put the generated proxy URL only in production `.env`/Compose configuration;
   never commit its credential.
5. Recreate only `analyzer`, `briefs`, and `threads`.
6. From an affected container, verify that OpenRouter's public models endpoint
   and authenticated key-status endpoint return successfully.
7. Generate one world brief and confirm a new `briefs(scope='world')` row.
8. Confirm new successful `analyze.py` and `build_threads.py` OpenRouter calls.

## Failure handling and rollback

If any verification fails, remove `HTTPS_PROXY` from the three services and
recreate only those containers. Stop/disable `tinyproxy` and close its firewall
port. No database rows are deleted or rewritten during rollback.

If Jina or another HTTPS dependency misbehaves through the proxy, follow up
with the slower explicit `OPENROUTER_PROXY_URL` application change; do not leave
the analyzer partially working.

## Acceptance criteria

- OpenRouter endpoints return non-403 responses from the production workload.
- A new world brief is stored with a current timestamp and shown by the public
  API.
- `analyze.py` and `build_threads.py` record successful OpenRouter calls.
- Only the three AI services are recreated.
- The Finnish proxy rejects connections not originating from production.
- Article, temperature, and existing brief counts remain intact.
