import logging
from abc import ABC, abstractmethod
from typing import Any, TypeVar

from pydantic import BaseModel

logger = logging.getLogger("opspilot.investigation.llm")

T = TypeVar("T", bound=BaseModel)


class LLMConfigurationError(Exception):
    """Raised when an LLM provider is requested but required configuration or credentials are missing."""


class LLMInvocationError(Exception):
    """Raised when an invocation to an external LLM fails due to timeout, rate limits, or network errors."""


class BaseLLMProvider(ABC):
    """Abstract provider interface for LLM reasoning and structured telemetry extraction."""

    def __init__(
        self,
        model_name: str,
        temperature: float = 0.0,
        timeout_seconds: float = 45.0,
        max_retries: int = 2,
    ) -> None:
        self.model_name = model_name
        self.temperature = temperature
        self.timeout_seconds = timeout_seconds
        self.max_retries = max_retries

    @abstractmethod
    def is_configured(self) -> bool:
        """Return True if credentials and endpoints are validly configured, False otherwise."""
        ...

    @abstractmethod
    def get_provider_name(self) -> str:
        """Return unique identifier of provider e.g. 'groq'."""
        ...

    @abstractmethod
    async def invoke_structured(
        self,
        schema: type[T],
        messages: list[Any],
        temperature: float | None = None,
    ) -> T:
        """
        Invoke the language model ensuring structured output matching the provided Pydantic schema.
        Raises LLMConfigurationError if credentials missing, or LLMInvocationError on execution failure.
        """
        ...
