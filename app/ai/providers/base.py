"""
Abstract base classes for AI providers.

Every provider (Google, OpenAI, Anthropic, Ollama…) implements these
interfaces so the rest of the codebase never imports a specific SDK.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from app.ai.agent_protocol import AssistantTurn


# ---------------------------------------------------------------------------
# Provider enum — add new providers here
# ---------------------------------------------------------------------------


class ProviderType(str, Enum):
    GOOGLE = "google"
    OPENAI = "openai"
    ANTHROPIC = "anthropic"
    OLLAMA = "ollama"
    VOYAGE = "voyage"
    COHERE = "cohere"
    LITELLM = "litellm"


# ---------------------------------------------------------------------------
# Runtime config loaded from DB
# ---------------------------------------------------------------------------


@dataclass
class ProviderConfig:
    """
    Configuration for a single provider instance.
    Loaded from DB by ProviderRegistry at runtime.

    `spec` is a reference back to the catalog entry (LLMModelSpec /
    VisionModelSpec / EmbeddingModelSpec) when one applies. Callers that
    need capability metadata (context window, supports_tools, ...) read it
    from `config.spec` rather than hard-coding per model_id.
    """

    provider: ProviderType
    api_key: str = ""
    model_id: str = ""
    base_url: Optional[str] = None  # For Ollama, Azure, proxies
    dimensions: Optional[int] = None  # Embedding output dimensions
    extra: dict = field(default_factory=dict)  # Provider-specific params
    spec: Optional[object] = None  # LLMModelSpec | VisionModelSpec | EmbeddingModelSpec


# ---------------------------------------------------------------------------
# Embedding
# ---------------------------------------------------------------------------


class EmbeddingProvider(ABC):
    """Generate vector embeddings for text."""

    def __init__(self, config: ProviderConfig):
        self.config = config

    @abstractmethod
    async def embed(self, text: str) -> list[float]:
        """Embed a single text string."""
        ...

    @abstractmethod
    async def embed_batch(
        self, texts: list[str], concurrency: int = 5
    ) -> list[list[float]]:
        """Embed multiple texts with concurrency control."""
        ...

    @abstractmethod
    async def test_connection(self) -> tuple[bool, str]:
        """
        Test if the provider is reachable and credentials are valid.
        Returns (success, human-readable message).
        """
        ...

    @property
    def dimensions(self) -> int:
        """Output vector dimensions."""
        return self.config.dimensions or 768


# ---------------------------------------------------------------------------
# LLM (text generation)
# ---------------------------------------------------------------------------


class LLMProvider(ABC):
    """Generate text — used for summarization, webhook gateway, etc."""

    def __init__(self, config: ProviderConfig):
        self.config = config

    @abstractmethod
    async def generate(
        self,
        prompt: str,
        system: Optional[str] = None,
        max_tokens: Optional[int] = None,
        temperature: float = 0.7,
    ) -> str:
        """Generate a text completion."""
        ...

    async def generate_with_tools(
        self,
        messages: list[dict],
        tools: list[dict],
        system: Optional[str] = None,
        max_tokens: Optional[int] = None,
        temperature: float = 0.2,
    ) -> "AssistantTurn":
        """
        Multi-turn tool-calling. Messages use neutral format from agent_protocol.
        Returns AssistantTurn with tool_calls (if any) and finish_reason.
        Override in providers that support tool calling.
        """
        raise NotImplementedError(
            f"{type(self).__name__} does not support tool calling. "
            "Configure a provider that supports function calling (Anthropic, OpenAI, Google)."
        )

    @abstractmethod
    async def test_connection(self) -> tuple[bool, str]: ...


# ---------------------------------------------------------------------------
# Vision (image analysis)
# ---------------------------------------------------------------------------


class VisionProvider(ABC):
    """Analyze images — used during document ingestion for image captioning."""

    def __init__(self, config: ProviderConfig):
        self.config = config

    @abstractmethod
    async def analyze_image(
        self,
        image_data: bytes,
        mime_type: str = "image/jpeg",
        prompt: Optional[str] = None,
    ) -> str:
        """Analyze an image and return a text description."""
        ...

    @abstractmethod
    async def test_connection(self) -> tuple[bool, str]: ...
