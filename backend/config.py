from pydantic_settings import BaseSettings
from functools import lru_cache


class Settings(BaseSettings):
    # Database
    database_url: str = "postgresql+asyncpg://jad@localhost:5432/support_agent"
    database_url_sync: str = "postgresql+pg8000://jad@localhost:5432/support_agent"

    # LLM (via LiteLLM)
    litellm_model: str = "claude-sonnet-4-6"
    litellm_guard_model: str = "claude-sonnet-4-6"  # model for input/output guard LLM calls; swap to a cheaper model once validated
    litellm_classifier_model: str = "claude-sonnet-4-6"  # intent classifier + loop decision; swap to Haiku only after an eval baseline validates it
    litellm_embedding_model: str = "text-embedding-3-small"
    anthropic_api_key: str = ""
    openai_api_key: str = ""

    # Environment
    app_env: str = "development"  # set to "test" to enable test-only endpoints

    # Search config
    hybrid_search_enabled: bool = True

    # Agent config
    confidence_threshold: float = 0.7
    max_context_messages: int = 50
    message_retention_days: int = 60
    service_call_limit: int = 3        # max service calls per turn before forcing response generation

    # Public demo rails — Tier-1 rate limits allow only a few agent turns per
    # minute across ALL visitors, so the demo needs hard caps to stay alive.
    demo_max_concurrent_turns: int = 2          # agent turns running at once; others queue
    demo_max_messages_per_conversation: int = 10
    demo_max_message_chars: int = 500
    demo_max_customers_per_ip_per_day: int = 20
    demo_customer_ttl_hours: int = 24           # purge_demo_customers.py default

    # LangSmith tracing
    langchain_tracing_v2: bool = False
    langchain_api_key: str = ""
    langchain_project: str = "ai-support-agent"
    langchain_endpoint: str = "https://api.smith.langchain.com"

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"
        extra = "ignore"


@lru_cache
def get_settings() -> Settings:
    return Settings()
