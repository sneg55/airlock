"""Single env boundary. See guides/zod-at-the-boundary.md (Python section).

Rules:
  1. This file is the ONLY place environment variables are read.
     (Grep for `os.environ` in review; nothing outside this file may use it.)
  2. The Env model is the source of truth for the config type.
  3. Validation happens at import time - fail fast on misconfiguration.
  4. Add new vars here, declare their shape, provide a default where sensible.

Consumers:
    from app.env import env
    client.get(env.api_url, timeout=env.timeout_s)

Requires: pydantic >= 2, pydantic-settings.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr, ValidationError
from pydantic_settings import BaseSettings, SettingsConfigDict


class Env(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # ── Runtime ──────────────────────────────────────────────────────────────
    app_env: Literal["development", "test", "production"] = "development"
    port: int = Field(default=3000, gt=0)
    log_level: Literal["debug", "info", "warning", "error"] = "info"

    # Checkpoint service
    operator_token: SecretStr = Field(default=SecretStr("local-operator-token"), min_length=16)
    operator_identity: str = "local-operator"
    signing_key_path: Path = Path(".airlock/signing.key")
    public_key_path: Path = Path(".airlock/executor-public.pem")
    key_id: str = "airlock-local-1"
    approval_store_path: Path = Path(".airlock/approvals.sqlite3")
    bound_account_id: str = "account-a"
    checkpoint_url: str = "http://127.0.0.1:8000"

    # Cloud target. The region is a single configurable knob, not hard-coded per site.
    cloud_region: str = "ap-southeast-1"
    executor_poll_interval_s: float = Field(default=5.0, gt=0)
    agent_poll_interval_s: float = Field(default=300.0, gt=0)

    # Qwen planner and isolated Alibaba Cloud MCP endpoints.
    # The jury is deliberately heterogeneous across vendors (Qwen, DeepSeek, GLM) so that
    # disagreement reflects genuinely different model families, not one family's variance.
    # Model IDs verified against Qwen Cloud's live catalog (2026-07); qwen3.7-max is the
    # flagship "agent frontier" arbiter, glm-5.1 is the current GLM (glm-4.x is retired).
    qwen_api_key: SecretStr = SecretStr("")
    qwen_base_url: str = "https://dashscope-intl.aliyuncs.com/compatible-mode/v1"
    qwen_planner_model: str = "qwen3.7-max"
    jury_models: list[str] = Field(
        default_factory=lambda: ["qwen3.7-plus", "deepseek-v4-pro", "glm-5.1"]
    )
    jury_disagreement_threshold: float = Field(default=0.5, ge=0.0, le=1.0)
    memory_max_receipt_age_seconds: int = Field(default=3600, gt=0)
    read_mcp_sse_url: str = ""
    write_mcp_sse_url: str = ""
    write_mcp_bearer: SecretStr = SecretStr("")

    # ── External services (examples - replace with your own) ─────────────────
    # (noqa ERA001: these are intentional commented examples, not dead code)
    # database_url: PostgresDsn  # noqa: ERA001
    # redis_url: RedisDsn | None = None  # noqa: ERA001
    # anthropic_api_key: SecretStr  # noqa: ERA001
    # sentry_dsn: HttpUrl | None = None  # noqa: ERA001


def _load_env() -> Env:
    try:
        return Env()
    except ValidationError as e:
        # Render a readable error at startup. One bad env var should surface the
        # exact field and reason, not crash 10 stack frames deep.
        lines = [f"  {'.'.join(str(p) for p in err['loc'])}: {err['msg']}" for err in e.errors()]
        print(  # noqa: T201 - startup error; must reach stderr.
            "[env] invalid configuration:\n" + "\n".join(lines),
            file=sys.stderr,
        )
        raise SystemExit(1) from e


env = _load_env()
