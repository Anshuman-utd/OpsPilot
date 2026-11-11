import logging
from typing import Any, TypeVar

from pydantic import BaseModel

from app.core.config import Settings, get_settings
from app.investigation.providers.llm_provider import (
    BaseLLMProvider,
    LLMConfigurationError,
    LLMInvocationError,
)

logger = logging.getLogger("opspilot.investigation.groq")

T = TypeVar("T", bound=BaseModel)


class GroqProvider(BaseLLMProvider):
    """Groq Cloud provider leveraging LangChain Groq chat model integration."""

    def __init__(self, settings: Settings | None = None) -> None:
        settings = settings or get_settings()
        super().__init__(
            model_name=settings.LLM_MODEL or "llama-3.3-70b-versatile",
            temperature=settings.LLM_TEMPERATURE,
            timeout_seconds=settings.LLM_TIMEOUT_SECONDS,
            max_retries=settings.LLM_MAX_RETRIES,
        )
        self.api_key = settings.GROQ_API_KEY

    def is_configured(self) -> bool:
        """Return True only if a non-empty GROQ_API_KEY is configured."""
        return bool(self.api_key and self.api_key.strip())

    def get_provider_name(self) -> str:
        return "groq"

    async def invoke_structured(
        self,
        schema: type[T],
        messages: list[Any],
        temperature: float | None = None,
    ) -> T:
        """Execute structured LLM invocation via ChatGroq with Pydantic output schema."""
        if not self.is_configured():
            raise LLMConfigurationError(
                "Groq LLM provider is not configured. Please set GROQ_API_KEY in environment."
            )

        try:
            from langchain_groq import ChatGroq

            temp = self.temperature if temperature is None else temperature

            llm = ChatGroq(
                model=self.model_name,
                groq_api_key=self.api_key,
                temperature=temp,
                timeout=self.timeout_seconds,
                max_retries=self.max_retries,
            )

            structured_llm = llm.with_structured_output(schema)
            result = await structured_llm.ainvoke(messages)
            return result
        except Exception as exc:
            # Never expose secrets in log or exception
            sanitized_msg = str(exc)
            if self.api_key:
                sanitized_msg = sanitized_msg.replace(self.api_key, "***")
            logger.error("Groq structured invocation failed: %s", sanitized_msg)
            raise LLMInvocationError(f"Groq API call failed: {sanitized_msg}") from exc
