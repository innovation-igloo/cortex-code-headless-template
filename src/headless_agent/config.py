"""Application configuration.

Settings are loaded from environment variables (prefix ``HEADLESS_AGENT_``) and
an optional ``.env`` file. See ``.env.example`` for the full list.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Annotated, Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

AuthMode = Literal["dev", "pat", "spcs_oauth"]
PermissionMode = Literal["default", "plan", "acceptEdits", "bypassPermissions"]


class Settings(BaseSettings):
    """Runtime settings for the headless agent service."""

    model_config = SettingsConfigDict(
        env_prefix="HEADLESS_AGENT_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # --- Auth ---
    # dev        : named connection from ~/.snowflake/config.toml (local dev).
    # pat        : env-var connection using a PAT (SNOWFLAKE_CONNECTIONS_<NAME>_*),
    #              injected in SPCS via a Snowflake Secret. Recommended for SPCS.
    # spcs_oauth : bridge the SPCS-injected OAuth token at /snowflake/session/token
    #              into a config.toml connection (no secret to manage).
    auth_mode: AuthMode = "dev"
    # Connection name the CLI selects with --connection. For pat mode this must
    # match the SNOWFLAKE_CONNECTIONS_<NAME>_* env vars (case-insensitive).
    connection: str = "my-connection"

    # --- Agent ---
    model: str = "auto"
    workdir: str = "."
    system_prompt: str | None = None
    max_turns: int | None = None

    # --- Permissions ---
    # NoDecode: skip pydantic-settings' JSON decoding so the CSV validator below
    # can accept "SQL,Read,Glob" from env.
    allowed_tools: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: ["SQL", "Read", "Grep", "Glob"]
    )
    disallowed_tools: Annotated[list[str], NoDecode] = Field(default_factory=list)
    permission_mode: PermissionMode = "default"
    # bypassPermissions is dangerous: it auto-approves every tool call. It is
    # only honored when this flag is also true (defense in depth).
    allow_bypass: bool = False

    # --- HTTP server ---
    host: str = "0.0.0.0"
    port: int = 8000

    # --- Logging ---
    log_level: str = "INFO"

    @field_validator("allowed_tools", "disallowed_tools", mode="before")
    @classmethod
    def _split_csv(cls, value: object) -> object:
        """Accept comma-separated env strings as lists (e.g. ``SQL,Read``)."""
        if isinstance(value, str):
            return [item.strip() for item in value.split(",") if item.strip()]
        return value

    @property
    def effective_permission_mode(self) -> PermissionMode:
        """Permission mode with the bypass safety gate applied."""
        if self.permission_mode == "bypassPermissions" and not self.allow_bypass:
            return "default"
        return self.permission_mode


@lru_cache
def get_settings() -> Settings:
    """Return cached settings (parsed once per process)."""
    return Settings()
