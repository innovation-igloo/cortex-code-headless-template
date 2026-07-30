# Cortex Code Headless Agent Template

A production-shaped starter for running the **Cortex Code Agent SDK** headlessly
(no interactive terminal) behind an HTTP + SSE API, ready to deploy to
**Snowpark Container Services (SPCS)**. Use it as the scaffold for a customer
POC: clone it, add your tools, deploy.

```
your client ──HTTP POST /chat──▶ FastAPI service ──SDK──▶ cortex CLI ──▶ Snowflake Cortex
      ◀──────── SSE stream ───────┘   (this repo)      (in the image)      (inference + SQL)
```

## Why this shape

- The Cortex Code Agent SDK is a thin supervisor that drives the `cortex` CLI in
  `stream-json` mode. The CLI is a **hard runtime dependency** and is **not**
  bundled with the SDK, so the image installs it explicitly.
- One long-lived agent session per `session_id` (one CLI subprocess), reused
  across turns to preserve context and avoid per-request spawn cost.
- In-process `@tool` MCP servers (a Python-only SDK capability) are the main
  extension point — see `src/headless_agent/tools.py`.

## Layout

| Path | Purpose |
|------|---------|
| `src/headless_agent/config.py` | Settings (env `HEADLESS_AGENT_*`) |
| `src/headless_agent/agent.py` | SDK wrapper: options + session lifecycle + streaming |
| `src/headless_agent/tools.py` | Example in-process `@tool` server (extend here) |
| `src/headless_agent/permissions.py` | `can_use_tool` allowlist policy |
| `src/headless_agent/hooks.py` | Pre/Post tool-use audit hooks |
| `src/headless_agent/auth.py` | SPCS OAuth token → cortex CLI connection bridge |
| `src/headless_agent/server.py` | FastAPI app: `/health`, `/ready`, `/chat` (SSE), `/sessions` |
| `scripts/run_local.py` | Minimal non-HTTP driver for quick testing |
| `scripts/entrypoint.sh` | Container start: bridge auth, launch uvicorn |
| `Dockerfile` | Image: cortex CLI + uv + service |
| `deploy/` | SPCS setup SQL, service spec, create-service SQL, runbook |

## Local development (dev-first)

Requires the [cortex CLI](https://docs.snowflake.com/en/user-guide/cortex-code/cortex-code-cli)
on your PATH and a Snowflake connection in `~/.snowflake/config.toml`.

```sh
curl -LsS https://ai.snowflake.com/static/cc-scripts/install.sh | sh  # cortex CLI
cp .env.example .env            # set HEADLESS_AGENT_CONNECTION to your connection
make sync                       # uv sync (installs the SDK + deps)

make run PROMPT="What tables are in SALES.PUBLIC?"   # one-shot driver
make dev                        # or run the API with reload
```

Then stream a turn:

```sh
curl -N localhost:8000/chat -H 'Content-Type: application/json' \
  -d '{"prompt":"List tables in SALES.PUBLIC with row counts.","session_id":"demo"}'
```

Each SSE frame is a JSON event: `{"type": "text"|"thinking"|"tool_use"|"result"|"error", ...}`.

## Deploy to SPCS

See [`deploy/README.md`](deploy/README.md). Summary:

```sh
snow sql -f deploy/00_setup.sql            # role, db/schema, image repo, compute pool
export REPO_URL=<repository_url>           # from SHOW IMAGE REPOSITORIES
snow spcs image-registry login
make build push                            # build linux/amd64, push
# edit <REPO_URL> in deploy/20_create_service.sql, then:
snow sql -f deploy/20_create_service.sql
```

## Configuration

All settings are `HEADLESS_AGENT_*` env vars (see `.env.example`). Key ones:

| Var | Default | Notes |
|-----|---------|-------|
| `HEADLESS_AGENT_AUTH_MODE` | `dev` (`spcs` in image) | `dev` uses a named connection; `spcs` bridges the injected token |
| `HEADLESS_AGENT_CONNECTION` | `my-connection` | Connection name in `config.toml` |
| `HEADLESS_AGENT_MODEL` | `auto` | Cortex picks the best available model |
| `HEADLESS_AGENT_ALLOWED_TOOLS` | `SQL,Read,Grep,Glob` | Auto-approved tools; the SQL tool is literally `SQL` |
| `HEADLESS_AGENT_PERMISSION_MODE` | `default` | `default`/`plan`/`acceptEdits`/`bypassPermissions` |
| `HEADLESS_AGENT_ALLOW_BYPASS` | `false` | `bypassPermissions` is ignored unless this is `true` |

## Security defaults

- Non-allowlisted **state-changing tools** (`Bash`, `Write`, `Edit`, notebook
  tools) are denied by the `can_use_tool` policy. Add them to
  `HEADLESS_AGENT_ALLOWED_TOOLS` to enable.
- `bypassPermissions` is double-gated (mode **and** `ALLOW_BYPASS=true`).
- CLI `stderr` is routed to the logger (the SDK silences it by default).

## SPCS auth: what actually works (validated)

Getting the Cortex Code CLI to run headlessly in SPCS has several non-obvious
requirements. All of the following are needed together (validated end-to-end —
the agent returned a real Cortex completion from inside a compute pool):

1. **Use a PAT, not the SPCS OAuth token.** The SPCS-injected token at
   `/snowflake/session/token` is **not supported for Cortex inference** (fails
   with error `395092`). `auth_mode=pat` is the supported path; `spcs_oauth`
   is included only for completeness and will fail on the first turn.
2. **Bypass the network policy for the service user's PAT.** If your account has
   an IP-based network policy, the compute-pool IP is rejected
   ("Incoming request with IP ... is not allowed"). Attach an authentication
   policy to a dedicated service user:
   `PAT_POLICY = ( NETWORK_POLICY_EVALUATION = NOT_ENFORCED )`. Self-service;
   does not modify the account network policy.
3. **Use the external account host, not the internal `SNOWFLAKE_HOST`.** PAT auth
   over the SPCS-internal host fails with "Session no longer exists". The
   template writes the connection with the account only (external).
4. **Attach an External Access Integration with wide egress.** The Cortex
   inference response *streams* from a Snowflake-managed host; a hand-picked
   allowlist causes "Network connection interrupted while streaming". Use a
   network rule with `VALUE_LIST = ('0.0.0.0:80', '0.0.0.0:443')`.
5. The CLI needs a connection **file** (`config.toml`) and an inference
   connection (`cortex/settings.json` → `cortexAgentConnectionName`). The
   container writes both at startup from env/secret (`auth.py`). It does **not**
   read `SNOWFLAKE_CONNECTIONS_*` env vars on its own.

`deploy/00_setup.sql` and `deploy/20_create_service.sql` encode all of this.

## Calling the public endpoint

The service's public endpoint requires Snowflake auth via the SPCS ingress OAuth
flow. For a programmatic client, send the token as `Snowflake Token="<PAT>"` and
**follow the ingress redirect with a cookie jar** to establish the session, then
call the API:

```bash
URL="https://<ingress-host>.snowflakecomputing.app"
PAT="<a PAT for a user granted the ...!api_user service role>"
# 1) prime the ingress OAuth session cookie
curl -sL -c cj.txt -b cj.txt "$URL/ready" -H "Authorization: Snowflake Token=\"$PAT\""
# 2) call the streaming chat API
curl -N -b cj.txt -X POST "$URL/chat" \
  -H "Authorization: Snowflake Token=\"$PAT\"" -H "Content-Type: application/json" \
  -d '{"prompt":"List tables in SALES.PUBLIC with row counts.","session_id":"demo"}'
```

Gotchas that will make it look broken:
- A raw `Authorization: Bearer <PAT>` (no cookie/redirect) returns a 302 redirect.
- Not following the redirect (no `-L`/cookie jar) returns `500 {"responseType":"FAULT"}`.
- After rotating the service's PAT secret, `ALTER SERVICE ... SUSPEND; RESUME;`
  so the container reloads it (env-injected secrets don't hot-reload).

## Operational notes

- **Run uvicorn with `--loop asyncio`.** The SDK spawns the `cortex` CLI with a
  `user` kwarg that `uvloop` (bundled with `uvicorn[standard]`) rejects. The
  entrypoint and `make dev` already set this; keep it if you change how the
  service starts.
- **Keep the CLI current.** The CLI is server-version-gated and will refuse to
  run stream-json mode when out of date (`cortex update`). Pin the CLI in the
  Dockerfile and bump it alongside the SDK.

## Version pinning

The SDK and CLI are tightly coupled (the SDK enforces a minimum CLI version).
Bump `cortex-code-agent-sdk` in `pyproject.toml` and the CLI install in the
`Dockerfile` together.
