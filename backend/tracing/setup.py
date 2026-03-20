"""
LangSmith tracing configuration.

THIS IS THE ONLY FILE that imports or configures LangSmith.
All other modules must not import langsmith directly.

LangChain/LangGraph traces automatically when LANGCHAIN_TRACING_V2=true
and LANGCHAIN_API_KEY are present in os.environ. This module reads those
values from our Settings object and exports them to os.environ so that
LangGraph picks them up regardless of how the process was started.
"""
import logging
import os

logger = logging.getLogger(__name__)


def init_tracing() -> None:
    """Initialize LangSmith tracing from app settings.

    Call once at app startup (from backend/main.py). Safe to call when
    tracing is disabled — it will simply log that tracing is off and return.
    """
    from backend.config import get_settings
    settings = get_settings()

    if not settings.langchain_tracing_v2:
        logger.info("LangSmith tracing disabled (LANGCHAIN_TRACING_V2=false)")
        return

    if not settings.langchain_api_key:
        logger.warning(
            "LangSmith tracing enabled but LANGCHAIN_API_KEY is not set — tracing will fail"
        )
        return

    # Export to os.environ so LangChain/LangGraph picks them up.
    os.environ["LANGCHAIN_TRACING_V2"] = "true"
    os.environ["LANGCHAIN_API_KEY"] = settings.langchain_api_key
    os.environ["LANGCHAIN_PROJECT"] = settings.langchain_project
    os.environ["LANGCHAIN_ENDPOINT"] = settings.langchain_endpoint
    os.environ["LANGSMITH_USE_MULTIPART_INGEST"] = "false"

    logger.info("LangSmith tracing enabled (project=%s)", settings.langchain_project)
