from __future__ import annotations

from enum import StrEnum
from functools import lru_cache
from pathlib import Path

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

BASE_DIR = Path(__file__).resolve().parents[2]


class TradingMode(StrEnum):
    PAPER = "PAPER"
    TESTNET = "TESTNET"
    MAINNET = "MAINNET"


class Environment(StrEnum):
    DEV = "dev"
    STAGING = "staging"
    PROD = "prod"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(BASE_DIR.parent / ".env", BASE_DIR / ".env"),
        env_file_encoding="utf-8",
        env_prefix="AION_",
        extra="ignore",
    )

    # --- identity ---
    app_name: str = "AION"
    environment: Environment = Environment.DEV
    api_prefix: str = "/api/v1"
    debug: bool = False

    # --- security ---
    secret_key: str = Field(default="", min_length=0)
    access_token_ttl_seconds: int = 60 * 30
    refresh_token_ttl_seconds: int = 60 * 60 * 24 * 30
    jwt_algorithm: str = "HS256"
    login_rate_limit_per_minute: int = 8
    cors_origins: list[str] = Field(default_factory=list)
    # Default to a loopback-only Host allowlist; set AION_TRUSTED_HOSTS for a
    # real domain. The old ["*"] wildcard disabled Host-header protection in
    # production by default (audit BUG-24). Tests rely on "testserver".
    trusted_hosts: list[str] = Field(
        default_factory=lambda: ["localhost", "127.0.0.1", "testserver"]
    )

    # --- storage ---
    database_url: str = "postgresql+asyncpg://aion:aion_dev_pw@127.0.0.1:5432/aion"
    redis_url: str = "redis://127.0.0.1:6379/0"
    db_pool_size: int = 10
    db_max_overflow: int = 5
    db_echo: bool = False

    # --- trading safety ---
    trading_mode: TradingMode = TradingMode.PAPER
    mainnet_enabled: bool = False
    require_totp_for_mainnet: bool = True
    # 公开注册：默认开启，适合面向大众的部署；私有部署可用环境变量关闭。
    registration_enabled: bool = True
    default_symbols: list[str] = Field(default_factory=lambda: ["BTCUSDT", "ETHUSDT", "SOLUSDT"])

    # --- binance ---
    binance_testnet_api_key: str = ""
    binance_testnet_api_secret: str = ""
    binance_mainnet_api_key: str = ""
    binance_mainnet_api_secret: str = ""
    binance_recv_window_ms: int = 5000
    binance_request_timeout_seconds: float = 10.0

    # --- model gateway ---
    anthropic_api_key: str = ""
    anthropic_base_url: str = "https://api.anthropic.com"
    openai_api_key: str = ""
    openai_base_url: str = "https://api.openai.com/v1"
    gemini_api_key: str = ""
    gemini_base_url: str = "https://generativelanguage.googleapis.com/v1beta"
    model_daily_budget_usd: float = 5.0
    model_run_budget_usd: float = 0.75
    agent_run_timeout_seconds: int = 180
    agent_max_concurrency: int = 3

    # --- market data ---
    market_stale_seconds: int = 90
    candle_retention_days: int = 90

    @field_validator("cors_origins", "default_symbols", mode="before")
    @classmethod
    def _split_csv(cls, v: object) -> object:
        if isinstance(v, str):
            return [item.strip() for item in v.split(",") if item.strip()]
        return v

    @field_validator("secret_key")
    @classmethod
    def _require_secret(cls, v: str) -> str:
        if v and len(v) < 32:
            raise ValueError("AION_SECRET_KEY must be at least 32 characters")
        return v

    @property
    def is_prod(self) -> bool:
        return self.environment is Environment.PROD

    def binance_credentials(self, mode: TradingMode) -> tuple[str, str]:
        if mode is TradingMode.MAINNET:
            return self.binance_mainnet_api_key, self.binance_mainnet_api_secret
        return self.binance_testnet_api_key, self.binance_testnet_api_secret

    def resolve_trading_mode(self) -> TradingMode:
        """Mainnet must be explicitly unlocked; otherwise degrade safely."""
        if self.trading_mode is TradingMode.MAINNET and not self.mainnet_enabled:
            return TradingMode.TESTNET
        return self.trading_mode


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    settings = Settings()
    if not settings.secret_key:
        raise RuntimeError(
            "AION_SECRET_KEY is not set. Copy .env.example to .env and generate one with "
            "`python -c \"import secrets; print(secrets.token_urlsafe(48))\"`."
        )
    return settings
