-- =============================================================================
-- 20_create_service.sql  —  Create (or upgrade) the headless agent service.
--
-- Prereqs: 00_setup.sql done (svc user + auth policy + AGENT_PAT secret + EAI),
-- image pushed. auth_mode=pat: the container writes config.toml from the
-- SNOWFLAKE_CONNECTIONS_POC_* env vars + the injected PAT secret, using the
-- EXTERNAL account host, and reaches Cortex via the EAI.
-- Replace <REPO_URL> and <ORG-ACCOUNT> below.
-- =============================================================================

USE ROLE SYSADMIN;
USE DATABASE CORTEX_HEADLESS_DB;
USE SCHEMA APP;

CREATE SERVICE IF NOT EXISTS CORTEX_HEADLESS_AGENT
  IN COMPUTE POOL CORTEX_HEADLESS_POOL
  EXTERNAL_ACCESS_INTEGRATIONS = (CORTEX_HEADLESS_EAI)
  FROM SPECIFICATION_TEMPLATE $$
spec:
  containers:
    - name: agent
      image: {{ image_url }}
      env:
        HEADLESS_AGENT_AUTH_MODE: pat
        HEADLESS_AGENT_CONNECTION: poc
        HEADLESS_AGENT_MODEL: auto
        HEADLESS_AGENT_PERMISSION_MODE: default
        HEADLESS_AGENT_ALLOWED_TOOLS: "SQL,Read,Grep,Glob"
        HEADLESS_AGENT_LOG_LEVEL: INFO
        # Env-var connection 'poc' — EXTERNAL account (org-account form), no host.
        SNOWFLAKE_CONNECTIONS_POC_ACCOUNT: {{ sf_account }}
        SNOWFLAKE_CONNECTIONS_POC_USER: CORTEX_HEADLESS_SVC
        SNOWFLAKE_CONNECTIONS_POC_ROLE: SYSADMIN
        SNOWFLAKE_CONNECTIONS_POC_WAREHOUSE: CORTEX_HEADLESS_WH
        SNOWFLAKE_CONNECTIONS_POC_AUTHENTICATOR: programmatic_access_token
      secrets:
        - snowflakeSecret: CORTEX_HEADLESS_DB.APP.AGENT_PAT
          secretKeyRef: secret_string
          envVarName: SNOWFLAKE_CONNECTIONS_POC_PASSWORD
      readinessProbe:
        port: 8000
        path: /ready
      resources:
        requests:
          cpu: "1"
          memory: 2Gi
        limits:
          cpu: "2"
          memory: 4Gi
  endpoints:
    - name: api
      port: 8000
      public: true
  serviceRoles:
    - name: api_user
      endpoints:
        - api
$$
  USING (
    image_url  => '"<REPO_URL>/cortex-headless-agent:latest"',
    sf_account => 'ORG-ACCOUNT'
  )
  QUERY_WAREHOUSE = CORTEX_HEADLESS_WH
  COMMENT = 'Cortex Code headless agent POC';

SHOW SERVICE CONTAINERS IN SERVICE CORTEX_HEADLESS_AGENT;
SHOW ENDPOINTS IN SERVICE CORTEX_HEADLESS_AGENT;

-- Logs (agent output + CLI stderr):
-- SELECT SYSTEM$GET_SERVICE_LOGS('CORTEX_HEADLESS_AGENT', 0, 'agent', 200);
-- Grant endpoint access:
-- GRANT SERVICE ROLE CORTEX_HEADLESS_AGENT!api_user TO ROLE <consumer_role>;
-- Redeploy after a new image:  ALTER SERVICE CORTEX_HEADLESS_AGENT FROM SPECIFICATION_TEMPLATE $$...$$ USING (...);
-- Tear down:  DROP SERVICE IF EXISTS CORTEX_HEADLESS_AGENT;
