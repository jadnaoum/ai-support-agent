from pydantic_settings import BaseSettings
from functools import lru_cache


class Settings(BaseSettings):
    # Database
    database_url: str = "postgresql+asyncpg://jad@localhost:5432/support_agent"
    database_url_sync: str = "postgresql+pg8000://jad@localhost:5432/support_agent"

    # LLM (via LiteLLM)
    litellm_model: str = "claude-sonnet-4-6"
    litellm_guard_model: str = "claude-sonnet-4-6"  # model for input/output guard LLM calls; swap to a cheaper model once validated
    litellm_embedding_model: str = "text-embedding-3-small"
    anthropic_api_key: str = ""
    openai_api_key: str = ""

    # Environment
    app_env: str = "development"  # set to "test" to enable test-only endpoints

    # Agent config
    confidence_threshold: float = 0.7
    max_context_messages: int = 50
    message_retention_days: int = 60

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
