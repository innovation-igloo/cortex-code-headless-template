"""Snowflake auth bridging for the container runtime.

The Cortex Code CLI authenticates using a named connection in
``$SNOWFLAKE_HOME/config.toml`` (default ``~/.snowflake/config.toml``), selected
by the SDK via ``--connection <name>``. It does not natively read the SPCS
OAuth token.

In ``spcs`` mode this module writes a connection that points the CLI at the
SPCS-injected OAuth token:

    [connections.<name>]
    account = "$SNOWFLAKE_ACCOUNT"
    host = "$SNOWFLAKE_HOST"
    authenticator = "oauth"
    token_file_path = "/snowflake/session/token"

SPCS refreshes the token file every few minutes; ``token_file_path`` lets the
connector re-read it, which is why we point at the file rather than inlining the
token string.

In ``dev`` mode this is a no-op: the developer's existing connection file is
used as-is.

KNOWN RISK: whether the CLI honors ``token_file_path`` with
``authenticator = oauth`` must be validated against a live SPCS deployment. If
it does not, fall back to inlining ``token = <contents>`` (valid ~1 hour; the
long-lived CLI connection survives past expiry once established). See
deploy/README.md.
"""

from __future__ import annotations

import os
from pathlib import Path

from .config import Settings, get_settings
from .logging import configure_logging, get_logger

log = get_logger(__name__)

DEFAULT_TOKEN_PATH = "/snowflake/session/token"


def _snowflake_home() -> Path:
    return Path(os.environ.get("SNOWFLAKE_HOME", str(Path.home() / ".snowflake")))


def _toml_escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')


def render_spcs_config(settings: Settings) -> str:
    """Build config.toml text for the SPCS OAuth connection."""
    account = os.environ.get("SNOWFLAKE_ACCOUNT", "")
    host = os.environ.get("SNOWFLAKE_HOST", "")
    if not account or not host:
        raise RuntimeError(
            "SPCS auth mode requires SNOWFLAKE_ACCOUNT and SNOWFLAKE_HOST env vars "
            "(injected by Snowpark Container Services)."
        )
    token_path = os.environ.get("SNOWFLAKE_TOKEN_PATH", DEFAULT_TOKEN_PATH)
    name = settings.connection

    lines = [
        f'default_connection_name = "{_toml_escape(name)}"',
        "",
        f"[connections.{name}]",
        f'account = "{_toml_escape(account)}"',
        f'host = "{_toml_escape(host)}"',
        'authenticator = "oauth"',
        f'token_file_path = "{_toml_escape(token_path)}"',
    ]
    # Optional context, provided by SPCS or the service spec.
    for env_key, toml_key in (
        ("SNOWFLAKE_DATABASE", "database"),
        ("SNOWFLAKE_SCHEMA", "schema"),
        ("SNOWFLAKE_WAREHOUSE", "warehouse"),
        ("SNOWFLAKE_ROLE", "role"),
    ):
        value = os.environ.get(env_key)
        if value:
            lines.append(f'{toml_key} = "{_toml_escape(value)}"')
    return "\n".join(lines) + "\n"


def render_pat_config(settings: Settings) -> str:
    """Build config.toml text for a PAT connection.

    The cortex CLI reads connections from a file (config.toml / connections.toml);
    it does NOT resolve SNOWFLAKE_CONNECTIONS_<NAME>_* env vars on its own inside
    a container. We read those env vars here and materialize a file.

    IMPORTANT: use the EXTERNAL account (account.snowflakecomputing.com), NOT the
    SPCS-injected internal SNOWFLAKE_HOST. PAT auth over the internal host fails
    with "Session no longer exists" (the internal host only accepts the SPCS
    service OAuth token). Reaching the external host from SPCS requires an EAI
    with wide egress; see deploy/README.md. Only set an explicit host if the
    operator provided SNOWFLAKE_CONNECTIONS_<NAME>_HOST.
    """
    name = settings.connection
    conn = name.upper()

    def _get(key: str, *, required: bool = False) -> str:
        val = os.environ.get(f"SNOWFLAKE_CONNECTIONS_{conn}_{key}", "")
        if required and not val:
            raise RuntimeError(f"pat auth: SNOWFLAKE_CONNECTIONS_{conn}_{key} is not set")
        return val

    account = _get("ACCOUNT", required=True)
    host = _get("HOST")  # optional; leave unset to use the external account host
    pat = _get("PASSWORD", required=True)

    lines = [
        f'default_connection_name = "{_toml_escape(name)}"',
        "",
        f"[connections.{name}]",
        f'account = "{_toml_escape(account)}"',
        'authenticator = "programmatic_access_token"',
        f'password = "{_toml_escape(pat)}"',
    ]
    if host:
        lines.append(f'host = "{_toml_escape(host)}"')
    for key, toml_key in (("USER", "user"), ("ROLE", "role"), ("WAREHOUSE", "warehouse")):
        val = _get(key)
        if val:
            lines.append(f'{toml_key} = "{_toml_escape(val)}"')
    return "\n".join(lines) + "\n"


def _write_config(text: str, settings: Settings, label: str) -> None:
    home = _snowflake_home()
    home.mkdir(parents=True, exist_ok=True)
    config_path = home / "config.toml"
    config_path.write_text(text)
    config_path.chmod(0o600)
    log.info("auth_mode=%s: wrote connection '%s' to %s", label, settings.connection, config_path)
    _write_cortex_agent_connection(settings)


def _write_cortex_agent_connection(settings: Settings) -> None:
    """Point Cortex Code's *inference* connection at our connection.

    The CLI reads the LLM/agent connection from
    ``$SNOWFLAKE_HOME/cortex/settings.json`` (``cortexAgentConnectionName``),
    which is separate from the SQL ``--connection``. In a fresh container this
    file doesn't exist, so inference has no valid connection and fails with
    "Session no longer exists. New login required to access the service."
    """
    import json

    cortex_dir = _snowflake_home() / "cortex"
    cortex_dir.mkdir(parents=True, exist_ok=True)
    settings_path = cortex_dir / "settings.json"
    data: dict[str, object] = {}
    if settings_path.exists():
        try:
            data = json.loads(settings_path.read_text())
        except (json.JSONDecodeError, OSError):
            data = {}
    data["cortexAgentConnectionName"] = settings.connection
    settings_path.write_text(json.dumps(data, indent=2))
    log.info("set cortexAgentConnectionName='%s' in %s", settings.connection, settings_path)


def ensure_connection(settings: Settings | None = None) -> None:
    """Prepare the cortex CLI connection for the active auth mode.

    - dev        : no-op; uses the developer's local config.toml.
    - pat        : write config.toml from SNOWFLAKE_CONNECTIONS_<NAME>_* env vars
      + injected host/account. This is the recommended SPCS mode.
    - spcs_oauth : write config.toml pointing at the SPCS token file. NOTE: the
      SPCS OAuth token is NOT supported for the Cortex inference path; prefer pat.
    """
    settings = settings or get_settings()

    if settings.auth_mode == "dev":
        log.info("auth_mode=dev: using local connection '%s'", settings.connection)
        return

    if settings.auth_mode == "pat":
        _write_config(render_pat_config(settings), settings, "pat")
        return

    # spcs_oauth
    log.warning(
        "auth_mode=spcs_oauth: the SPCS OAuth token is not supported for Cortex "
        "inference and will likely fail with error 395092. Prefer auth_mode=pat."
    )
    _write_config(render_spcs_config(settings), settings, "spcs_oauth")


def main() -> None:
    settings = get_settings()
    configure_logging(settings.log_level)
    ensure_connection(settings)


if __name__ == "__main__":
    main()
