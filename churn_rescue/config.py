"""Application configuration loaded from environment variables.

Centralizing settings keeps secrets out of source code and makes the
server easy to run in local, staging, and hackathon demo environments.
"""
from pydantic import Field
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    app_env: str = Field(default="development")
    log_level: str = Field(default="INFO")

    database_path: str = Field(default="data/churn_rescue.db")

    # CALL-E Developer API credentials.
    # Empty by default so the server can boot without a real key for local
    # health checks, but the outbound endpoint will refuse to work until set.
    calle_api_key: str = Field(default="")
    calle_base_url: str = Field(default="https://api.heycall-e.com")
    calle_webhook_url: str | None = Field(default=None)

    # Twilio credentials for WhatsApp confirmations.
    # Empty by default so the server can boot; confirmations are skipped
    # until a real SID / Auth Token / sandbox number are provided.
    twilio_account_sid: str = Field(default="")
    twilio_auth_token: str = Field(default="")
    twilio_whatsapp_from: str = Field(default="")  # e.g. whatsapp:+14155238886

    # Retention discount knobs. LTV is bucketed by `ltv_tier_step` dollars and
    # each step adds `discount_step_percent` to the base 20% offer, capped at
    # `discount_max_percent`.
    discount_min_percent: float = Field(default=20.0)
    discount_max_percent: float = Field(default=50.0)
    discount_step_percent: float = Field(default=5.0)
    ltv_tier_step: float = Field(default=500.0)

    # HTTP / polling behavior.
    call_timeout_seconds: float = Field(default=30.0)
    call_poll_interval_seconds: float = Field(default=5.0)
    call_max_poll_seconds: float = Field(default=300.0)

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"
        case_sensitive = False


settings = Settings()
