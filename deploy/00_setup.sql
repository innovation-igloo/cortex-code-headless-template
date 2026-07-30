-- =============================================================================
-- 00_setup.sql  —  One-time infrastructure for the Cortex Code Headless Agent.
--
-- Validated working recipe for running the Cortex Code Agent SDK in SPCS:
--   * PAT auth (the SPCS OAuth token is NOT supported for Cortex inference).
--   * A dedicated service user whose auth policy bypasses the account network
--     policy for PAT (NETWORK_POLICY_EVALUATION = NOT_ENFORCED) — otherwise the
--     compute-pool IP is rejected by an account IP allowlist.
--   * The EXTERNAL account host (not the SPCS-internal host) + an External
--     Access Integration with wide egress (0.0.0.0:80/443) so the Cortex
--     inference stream can flow.
--
-- Most object creation is SYSADMIN. Identity objects (user, auth policy) and the
-- EAI require higher roles — those steps are marked. This is a one-time setup.
-- =============================================================================

SET app_db        = 'CORTEX_HEADLESS_DB';
SET app_schema    = 'APP';
SET app_repo      = 'IMAGES';
SET compute_pool  = 'CORTEX_HEADLESS_POOL';
SET warehouse     = 'CORTEX_HEADLESS_WH';
SET svc_user      = 'CORTEX_HEADLESS_SVC';
SET svc_role      = 'SYSADMIN';   -- role the agent's SQL runs as (scope down for prod)

-- ---------------------------------------------------------------------------
-- 1. Core objects (SYSADMIN).  Needs CREATE DATABASE / WAREHOUSE / COMPUTE POOL.
-- ---------------------------------------------------------------------------
USE ROLE SYSADMIN;
CREATE DATABASE IF NOT EXISTS IDENTIFIER($app_db);
USE DATABASE IDENTIFIER($app_db);
CREATE SCHEMA IF NOT EXISTS IDENTIFIER($app_schema);
USE SCHEMA IDENTIFIER($app_schema);
CREATE IMAGE REPOSITORY IF NOT EXISTS IDENTIFIER($app_repo);
CREATE COMPUTE POOL IF NOT EXISTS IDENTIFIER($compute_pool)
  MIN_NODES = 1 MAX_NODES = 1 INSTANCE_FAMILY = CPU_X64_XS
  AUTO_SUSPEND_SECS = 3600 COMMENT = 'Cortex Code headless agent POC';
CREATE WAREHOUSE IF NOT EXISTS IDENTIFIER($warehouse)
  WAREHOUSE_SIZE = XSMALL AUTO_SUSPEND = 60 AUTO_RESUME = TRUE
  INITIALLY_SUSPENDED = TRUE COMMENT = 'SQL tool warehouse for the agent POC';

-- ---------------------------------------------------------------------------
-- 2. Dedicated service user + PAT auth policy (ACCOUNTADMIN / SECURITYADMIN).
--    The auth policy lets this user's PAT logins skip the account network
--    policy IP check (required so SPCS compute-pool IPs can authenticate).
-- ---------------------------------------------------------------------------
USE ROLE ACCOUNTADMIN;
CREATE USER IF NOT EXISTS IDENTIFIER($svc_user)
  TYPE = SERVICE
  DEFAULT_ROLE = IDENTIFIER($svc_role)
  DEFAULT_WAREHOUSE = IDENTIFIER($warehouse)
  COMMENT = 'Headless Cortex Code agent service user (POC)';
GRANT ROLE IDENTIFIER($svc_role) TO USER IDENTIFIER($svc_user);
GRANT DATABASE ROLE SNOWFLAKE.CORTEX_USER TO ROLE IDENTIFIER($svc_role);

CREATE AUTHENTICATION POLICY IF NOT EXISTS CORTEX_HEADLESS_DB.APP.SVC_PAT_POLICY
  AUTHENTICATION_METHODS = ('PROGRAMMATIC_ACCESS_TOKEN')
  PAT_POLICY = ( NETWORK_POLICY_EVALUATION = NOT_ENFORCED );
ALTER USER IDENTIFIER($svc_user)
  SET AUTHENTICATION POLICY CORTEX_HEADLESS_DB.APP.SVC_PAT_POLICY;

-- Mint a PAT for the service user, then store it in a secret (next step).
-- Run this and copy the token_secret value:
--   ALTER USER CORTEX_HEADLESS_SVC ADD PROGRAMMATIC ACCESS TOKEN COCO_PAT
--     ROLE_RESTRICTION = SYSADMIN DAYS_TO_EXPIRY = 90;

-- ---------------------------------------------------------------------------
-- 3. Secret holding the PAT (SYSADMIN owns it; service reads it).
--    Replace <PAT_VALUE> with the token_secret from the ADD PAT command above.
-- ---------------------------------------------------------------------------
USE ROLE SYSADMIN;
CREATE SECRET IF NOT EXISTS CORTEX_HEADLESS_DB.APP.AGENT_PAT
  TYPE = GENERIC_STRING
  SECRET_STRING = '<PAT_VALUE>'
  COMMENT = 'PAT for the headless agent service user';

-- ---------------------------------------------------------------------------
-- 4. External Access Integration for egress (ACCOUNTADMIN).
--    The agent connects to the EXTERNAL account host and the Cortex inference
--    response streams from a Snowflake-managed host; allow-all on 80/443 is the
--    reliable setting (narrow later if your security team requires it).
-- ---------------------------------------------------------------------------
USE ROLE ACCOUNTADMIN;
CREATE NETWORK RULE IF NOT EXISTS CORTEX_HEADLESS_DB.APP.SNOWFLAKE_EGRESS
  TYPE = HOST_PORT MODE = EGRESS
  VALUE_LIST = ('0.0.0.0:80', '0.0.0.0:443');
CREATE EXTERNAL ACCESS INTEGRATION IF NOT EXISTS CORTEX_HEADLESS_EAI
  ALLOWED_NETWORK_RULES = (CORTEX_HEADLESS_DB.APP.SNOWFLAKE_EGRESS)
  ENABLED = TRUE;
GRANT USAGE ON INTEGRATION CORTEX_HEADLESS_EAI TO ROLE SYSADMIN;

-- ---------------------------------------------------------------------------
-- 5. Image repository URL — you push the image here (see deploy/README.md).
-- ---------------------------------------------------------------------------
SHOW IMAGE REPOSITORIES IN SCHEMA IDENTIFIER($app_db) . IDENTIFIER($app_schema);
