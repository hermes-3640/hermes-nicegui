"""Application settings loaded from environment / .env."""

from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime configuration for the Hermes NiceGUI app.

    All values can be overridden through environment variables with the
    ``HERMES_`` prefix or a ``.env`` file at the project root.
    """

    model_config = SettingsConfigDict(
        env_prefix="HERMES_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    gateway_url: str = "http://127.0.0.1:8443"
    api_token: str = ""

    ui_host: str = "0.0.0.0"
    ui_port: int = 8080
    ui_dark: bool = False
    ui_reload: bool = True

    default_model: str = "hermes-agent"
    default_provider: str = ""

    plugins_disabled: str = ""

    log_level: str = "INFO"

    @property
    def disabled_plugins(self) -> list[str]:
        """Plugin names to skip at startup, split on commas."""
        return [name.strip() for name in self.plugins_disabled.split(",") if name.strip()]

    def client_kwargs(self) -> dict:
        """Keyword arguments for the Hermes gateway client."""
        return {
            "base_url": self.gateway_url,
            "token": self.api_token,
        }
