# Deploying to Snowpark Container Services

This runbook takes the headless agent from a built image to a running SPCS
service with a public HTTPS endpoint.

## Prerequisites

- Snowflake CLI (`snow`) configured with a connection that can assume a role
  with `ACCOUNTADMIN` (for the one-time account grants in `00_setup.sql`).
- Docker with buildx, able to build `linux/amd64` images.
- Account has SPCS enabled and access to a Cortex model (`auto` resolves one).

## 1. Create infrastructure (one time)

Edit the `SET` values at the top of `00_setup.sql` if you want different names,
then run it:

```sh
snow sql -f deploy/00_setup.sql
```

Note the `repository_url` printed by `SHOW IMAGE REPOSITORIES` — call it
`<REPO_URL>`. It looks like:

```
<org>-<account>.registry.snowflakecomputing.com/cortex_headless_db/app/images
```

## 2. Build and push the image

```sh
# Authenticate Docker with the Snowflake image registry.
snow spcs image-registry login

# Build for the SPCS node architecture and push.
export REPO_URL=<REPO_URL>
docker build --platform linux/amd64 -t "$REPO_URL/cortex-headless-agent:latest" .
docker push "$REPO_URL/cortex-headless-agent:latest"
```

The `make build push` targets wrap these (set `REPO_URL` first).

## 3. Create the service

Replace `<REPO_URL>` in `deploy/20_create_service.sql` (the `USING (image_url => ...)`
line), then:

```sh
snow sql -f deploy/20_create_service.sql
```

Wait for the service to become `READY`:

```sql
SHOW SERVICE CONTAINERS IN SERVICE CORTEX_HEADLESS_AGENT;
SHOW ENDPOINTS IN SERVICE CORTEX_HEADLESS_AGENT;  -- ingress_url appears when ready
```

## 4. Call the endpoint

The public endpoint uses the SPCS ingress OAuth flow. Send the PAT as
`Snowflake Token="..."` and follow the redirect with a cookie jar to establish
the session (a raw `Bearer` token or not following the redirect returns a 302 /
`500 FAULT`). The PAT's user must hold the `...!api_user` service role.

```sh
URL="https://<ingress_url>"
# 1) prime the ingress OAuth session cookie
curl -sL -c cj.txt -b cj.txt "$URL/ready" -H "Authorization: Snowflake Token=\"$SNOWFLAKE_PAT\""
# 2) call the streaming chat API
curl -N -b cj.txt -X POST "$URL/chat" \
  -H "Authorization: Snowflake Token=\"$SNOWFLAKE_PAT\"" \
  -H "Content-Type: application/json" \
  -d '{"prompt":"List the tables in SALES.PUBLIC with row counts.","session_id":"demo"}'
```

Responses stream as Server-Sent Events (`data: {...}` frames). After rotating the
PAT secret, run `ALTER SERVICE ... SUSPEND; RESUME;` so the container reloads it.

## 5. Grant access to a consumer

```sql
GRANT SERVICE ROLE CORTEX_HEADLESS_AGENT!api_user TO ROLE <consumer_role>;
GRANT USAGE ON DATABASE CORTEX_HEADLESS_DB TO ROLE <consumer_role>;
GRANT USAGE ON SCHEMA CORTEX_HEADLESS_DB.APP TO ROLE <consumer_role>;
```

## Known risk to validate first: the OAuth auth bridge

The container turns the SPCS-injected token into a cortex CLI connection using
`authenticator = "oauth"` + `token_file_path = /snowflake/session/token`
(written by `src/headless_agent/auth.py`). **Confirm the CLI honors
`token_file_path` on your account before relying on it.** Quick check inside the
running container:

```sh
snow sql -q "SELECT SYSTEM\$GET_SERVICE_LOGS('CORTEX_HEADLESS_AGENT', 0, 'agent', 200)"
# then hit /ready and /chat; a working /chat proves the connection authenticates.
```

If the CLI does **not** support `token_file_path`, fall back to inlining the
token: change `render_spcs_config()` to read `/snowflake/session/token` and emit
`token = "<contents>"`. The token is valid ~1 hour, but a long-lived CLI
connection survives past expiry once established (SPCS refreshes the file; a new
CLI subprocess would need a fresh read).

## Egress

Cortex inference and the SQL tool stay inside Snowflake, so no external access
integration (EAI) is required for the base template. If your POC tools call
external APIs, create an EAI and add `EXTERNAL_ACCESS_INTEGRATIONS = (...)` to
the `CREATE SERVICE` statement.
