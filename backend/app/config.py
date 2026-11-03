"""Central configuration. Every value can be overridden with an environment variable
(prefix-free, case-insensitive) or a .env file in the backend directory."""
from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # --- core -------------------------------------------------------------
    app_name: str = "Beacon Pulse"
    environment: str = "development"  # development | staging | production
    database_url: str = "sqlite:///./beacon_pulse.db"
    jwt_secret: str = "dev-only-secret-change-me-before-deploying-beacon-pulse"
    jwt_expiry_minutes: int = 60 * 12
    cors_origins: str = "http://localhost:5173,http://localhost:8080"
    seed_demo_data: bool = True

    # --- AI provider ------------------------------------------------------
    # groq | gemini | anthropic | openai | rules
    # groq and gemini have free tiers (no credit card); rules = built-in engine, no key at all
    ai_provider: str = "groq"
    groq_api_key: str | None = None
    groq_model: str = "openai/gpt-oss-120b"      # drafting
    groq_fast_model: str = "openai/gpt-oss-20b"  # classification
    gemini_api_key: str | None = None
    gemini_model: str = "gemini-2.5-flash"
    gemini_fast_model: str = "gemini-2.5-flash-lite"
    anthropic_api_key: str | None = None
    anthropic_model: str = "claude-sonnet-5-5"
    anthropic_fast_model: str = "claude-haiku-5-5"
    openai_api_key: str | None = None
    openai_model: str = "gpt-4.1"
    openai_fast_model: str = "gpt-4.1-mini"
    ai_timeout_seconds: float = 30.0
    # AI agents (tool-calling specialists) answer customers whenever an LLM key is configured.
    # false = classic mode: LLM only classifies and drafts; replies come from the rules engine.
    agent_mode: bool = True
    # Below this classifier confidence a ticket is never auto-resolved.
    auto_resolve_min_confidence: float = 0.80

    # --- automation controls (rollout switches, see docs/PILOT_PLAYBOOK) ---
    # Comma list of intents allowed to auto-resolve. Month-2 pilot = "order_status".
    auto_resolve_intents: str = "order_status,product_availability,store_policy,technical_support,other"
    # shadow = AI runs, drafts everything, sends nothing (parallel-run validation)
    # live   = AI sends auto-resolutions for allowed intents
    automation_mode: str = "live"
    refund_auto_approve_limit: float = 0.0  # $ amount; 0 = every refund needs a human

    # --- connectors -------------------------------------------------------
    # mock = seeded demo systems inside this app; magento = Magento 2 REST
    order_connector: str = "mock"
    magento_base_url: str | None = None
    magento_access_token: str | None = None
    # mock | http  (generic REST CRM: GET {crm_base_url}/customers?email=)
    crm_connector: str = "mock"
    crm_base_url: str | None = None
    crm_api_key: str | None = None
    inventory_connector: str = "mock"  # mock | magento (MSI salable qty)

    # --- outbound notifications --------------------------------------------
    email_transport: str = "console"  # console | smtp
    smtp_host: str | None = None
    smtp_port: int = 587
    smtp_user: str | None = None
    smtp_password: str | None = None
    smtp_from: str = "Beacon Lighting Support <support@beaconlighting.example>"
    sms_transport: str = "console"  # console | twilio
    twilio_account_sid: str | None = None
    twilio_auth_token: str | None = None
    twilio_from_number: str | None = None

    # --- inbound email ------------------------------------------------------
    imap_host: str | None = None
    imap_user: str | None = None
    imap_password: str | None = None
    imap_poll_seconds: int = 60
    inbound_webhook_secret: str = "change-me"

    # --- business model (drives the savings analytics) ---------------------
    baseline_annual_cost: float = 2_200_000
    baseline_annual_tickets: int = 40_000
    baseline_response_hours: float = 12.0
    baseline_csat: float = 3.2
    overhead_share: float = 0.07          # tools & admin share of the baseline (deck: 7%)
    assisted_effort_ratio: float = 0.35   # agent effort on an AI-drafted ticket vs a fully manual one
    platform_annual_cost: float = 97_000  # AI usage + hosting + support for Beacon Pulse
    safety_auto_ack: bool = True          # send fixed safety advice immediately on safety tickets

    @property
    def auto_resolve_intent_set(self) -> set[str]:
        return {i.strip() for i in self.auto_resolve_intents.split(",") if i.strip()}

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
