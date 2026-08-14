"""Application settings loaded from environment / .env."""

from __future__ import annotations

from pathlib import Path
from typing import Literal

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

    # The kanban plugin talks to a *different* server than the gateway above:
    # the Hermes CLI's own dashboard web server, which uses cookie/password
    # auth rather than a bearer token. See `plugins/kanban/gateway.py`.
    kanban_url: str = ""
    kanban_username: str = ""
    kanban_password: str = ""

    ui_host: str = "0.0.0.0"
    ui_port: int = 8080
    ui_dark: bool = False
    ui_reload: bool = True

    default_model: str = "hermes-agent"
    default_provider: str = ""

    # How `sessions`/`cron` reach the `hermes` CLI for profile-scoped
    # commands (session export, chat, cron mutations): "local" runs it as a
    # subprocess on this host; "ssh" runs the same argv over `ssh
    # <ssh_target>` (only the interactive chat pty adds `-tt`; a forced
    # remote pty on a one-shot command hangs indefinitely). See
    # `hermes_nicegui/executor.py`.
    exec_mode: Literal["local", "ssh"] = "local"
    cli_bin: str = "hermes"
    ssh_target: str = ""
    ssh_options: str = ""

    # Where the daemon keeps its data (state.db, cron/jobs.json, kanban.db,
    # profiles/<name>/...) -- read directly (see `HermesExecutor.read_sqlite`/
    # `read_json`) rather than through the CLI. `~` is expanded locally for
    # "local" exec_mode, remotely (by the ssh-side script) for "ssh".
    hermes_home: str = "~/.hermes"

    plugins_disabled: str = ""

    log_level: str = "INFO"

    # Local username/password auth (see `hermes_nicegui/auth.py`). Disabling
    # is an escape hatch for an already-firewalled deployment -- the login
    # page and user store still exist either way, just ungated.
    auth_enabled: bool = True

    # Holds `users.db` (the admin account) and `storage_secret` (NiceGUI
    # session-cookie encryption key, generated once and persisted).
    data_dir: str = "~/.local/share/hermes-nicegui"

    # The `files` plugin gives the browser read/write access to everything
    # under this directory -- confining it here (default: the process's own
    # cwd, not `/`) is the only thing standing between that plugin and the
    # whole filesystem.
    files_root: str = "."
    files_edit_max_bytes: int = 2 * 1024 * 1024

    @property
    def disabled_plugins(self) -> list[str]:
        """Plugin names to skip at startup, split on commas."""
        return [name.strip() for name in self.plugins_disabled.split(",") if name.strip()]

    @property
    def ssh_options_list(self) -> list[str]:
        """Extra `ssh` args (e.g. `-o BatchMode=yes`), split on whitespace."""
        return self.ssh_options.split()

    @property
    def files_root_path(self) -> Path:
        return Path(self.files_root).expanduser().resolve()

    @property
    def data_dir_path(self) -> Path:
        return Path(self.data_dir).expanduser().resolve()

    def client_kwargs(self) -> dict:
        """Keyword arguments for the Hermes gateway client."""
        return {
            "base_url": self.gateway_url,
            "token": self.api_token,
        }
