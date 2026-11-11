from app.core.config import Settings, get_settings
from app.investigation.providers.groq_provider import GroqProvider
from app.investigation.providers.llm_provider import (
    BaseLLMProvider,
    LLMConfigurationError,
    LLMInvocationError,
)


def get_llm_provider(settings: Settings | None = None) -> BaseLLMProvider:
    """Factory creating the configured LLM provider instance."""
    settings = settings or get_settings()
    provider_name = (settings.LLM_PROVIDER or "groq").lower().strip()

    if provider_name == "groq":
        return GroqProvider(settings=settings)

    # Future providers (e.g. anthropic, openai, local) can be plugged in here
    raise LLMConfigurationError(f"Unsupported LLM provider '{provider_name}'")


__all__ = [
    "BaseLLMProvider",
    "GroqProvider",
    "LLMConfigurationError",
    "LLMInvocationError",
    "get_llm_provider",
]

