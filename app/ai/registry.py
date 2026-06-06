"""
Provider Registry — central factory that resolves the correct AI provider
based on runtime configuration stored in the database.

Usage:
    registry = ProviderRegistry(db_session)

    # Embedding (for document ingestion & search)
    emb = await registry.get_embedding()
    vectors = await emb.embed_batch(["hello", "world"])

    # Embedding for queries (with search_query task for Google)
    emb_query = await registry.get_embedding(task="search_query")
    query_vec = await emb_query.embed("what is the refund policy?")

    # LLM (for summarization, webhook gateway)
    llm = await registry.get_llm()
    summary = await llm.generate("Summarize this document...")

    # Vision (for image analysis during ingestion)
    vision = await registry.get_vision()
    if vision:
        caption = await vision.analyze_image(image_bytes)
"""

from typing import Optional

from loguru import logger
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.providers.base import (
    EmbeddingProvider,
    LLMProvider,
    ProviderConfig,
    ProviderType,
    VisionProvider,
)

# ---------------------------------------------------------------------------
# Provider class mappings — add new providers here
# ---------------------------------------------------------------------------


def _get_embedding_class(provider: ProviderType) -> type[EmbeddingProvider]:
    if provider == ProviderType.GOOGLE:
        from app.ai.providers.google import GoogleEmbedding

        return GoogleEmbedding
    elif provider == ProviderType.OPENAI:
        from app.ai.providers.openai_provider import OpenAIEmbedding

        return OpenAIEmbedding
    elif provider == ProviderType.LITELLM:
        from app.ai.providers.litellm_provider import LiteLLMEmbedding

        return LiteLLMEmbedding
    raise ValueError(f"Unsupported embedding provider: {provider}")


def _get_llm_class(provider: ProviderType) -> type[LLMProvider]:
    if provider == ProviderType.GOOGLE:
        from app.ai.providers.google import GoogleLLM

        return GoogleLLM
    elif provider == ProviderType.OPENAI:
        from app.ai.providers.openai_provider import OpenAILLM

        return OpenAILLM
    elif provider == ProviderType.ANTHROPIC:
        from app.ai.providers.anthropic_provider import AnthropicLLM

        return AnthropicLLM
    elif provider == ProviderType.LITELLM:
        from app.ai.providers.litellm_provider import LiteLLMChat

        return LiteLLMChat
    raise ValueError(f"Unsupported LLM provider: {provider}")


def _get_vision_class(provider: ProviderType) -> type[VisionProvider]:
    if provider == ProviderType.GOOGLE:
        from app.ai.providers.google import GoogleVision

        return GoogleVision
    elif provider == ProviderType.OPENAI:
        from app.ai.providers.openai_provider import OpenAIVision

        return OpenAIVision
    elif provider == ProviderType.LITELLM:
        from app.ai.providers.litellm_provider import LiteLLMVision

        return LiteLLMVision
    raise ValueError(f"Unsupported vision provider: {provider}")


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------


class ProviderRegistry:
    """
    Resolves provider configs from DB and returns the correct implementation.

    Config keys in DB follow the pattern: {capability}_{field}
      - embedding_provider, embedding_model_id, embedding_api_key, ...
      - llm_provider, llm_model_id, llm_api_key, ...
      - vision_provider, vision_model_id, vision_api_key, ...
    """

    def __init__(self, db: AsyncSession):
        self.db = db

    async def get_embedding(
        self,
        task: str = "document",
        spec_id: Optional[str] = None,
    ) -> EmbeddingProvider:
        """
        Get an embedding provider for a specific catalog spec, or the active
        one configured in app_config.

        Args:
            task: Embedding task type (Google uses this for query vs document).
            spec_id: Override — load this specific catalog entry instead of
                     the system's active spec. Used by re-embed jobs that need
                     to embed against a NEW model while the active spec is
                     still pointing at the OLD one (atomic flip on completion).
        """
        config = await self._load_embedding_config(spec_id=spec_id)
        config.extra["task"] = task
        cls = _get_embedding_class(config.provider)
        return cls(config)

    async def get_active_embedding_spec_id(self) -> Optional[str]:
        """Return the spec_id currently active for search, or None if unset."""
        from app.ai.embedding_catalog import EMBEDDING_CATALOG
        from app.services.config_service import (
            ACTIVE_EMBEDDING_MODEL_KEY,
            ConfigService,
        )

        svc = ConfigService(self.db)
        spec_id = await svc.get(ACTIVE_EMBEDDING_MODEL_KEY)
        if spec_id and spec_id in EMBEDDING_CATALOG:
            return spec_id
        return None

    async def get_llm(self) -> LLMProvider:
        """Get the configured LLM provider."""
        config = await self._load_llm_config()
        cls = _get_llm_class(config.provider)
        return cls(config)

    async def get_active_llm_spec_id(self) -> Optional[str]:
        """Return the spec_id currently active for LLM, or None if unset."""
        from app.ai.llm_catalog import LLM_CATALOG, derive_spec_id
        from app.services.config_service import ACTIVE_LLM_MODEL_KEY, ConfigService

        svc = ConfigService(self.db)
        spec_id = await svc.get(ACTIVE_LLM_MODEL_KEY)
        if spec_id and spec_id in LLM_CATALOG:
            return spec_id
        # Backward-compat: derive from legacy provider+model_id pair.
        legacy_provider = await svc.get("llm_provider")
        legacy_model = await svc.get("llm_model_id")
        if legacy_provider and legacy_model:
            return derive_spec_id(legacy_provider, legacy_model)
        return None

    async def get_vision(self) -> Optional[VisionProvider]:
        """Get the configured vision provider. Returns None if not configured."""
        try:
            config = await self._load_vision_config()
        except ValueError:
            logger.debug("No vision provider configured, image analysis disabled")
            return None
        cls = _get_vision_class(config.provider)
        return cls(config)

    async def get_active_vision_spec_id(self) -> Optional[str]:
        """Return the spec_id currently active for vision, or None if unset."""
        from app.ai.vision_catalog import VISION_CATALOG, derive_spec_id
        from app.services.config_service import (
            ACTIVE_VISION_MODEL_KEY,
            ConfigService,
        )

        svc = ConfigService(self.db)
        spec_id = await svc.get(ACTIVE_VISION_MODEL_KEY)
        if spec_id and spec_id in VISION_CATALOG:
            return spec_id
        legacy_provider = await svc.get("vision_provider")
        legacy_model = await svc.get("vision_model_id")
        if legacy_provider and legacy_model:
            return derive_spec_id(legacy_provider, legacy_model)
        return None

    async def test_all(self) -> dict[str, tuple[bool, str]]:
        """
        Test all configured providers.
        Returns: {"embedding": (True, "OK"), "llm": (False, "error"), ...}
        """
        results: dict[str, tuple[bool, str]] = {}
        loaders = {
            "embedding": (self._load_embedding_config, _get_embedding_class),
            "llm": (self._load_llm_config, _get_llm_class),
            "vision": (self._load_vision_config, _get_vision_class),
        }

        for capability, (loader, cls_fn) in loaders.items():
            try:
                config = await loader()
            except ValueError as e:
                results[capability] = (False, f"Not configured: {e}")
                continue
            try:
                provider = cls_fn(config.provider)(config)
                results[capability] = await provider.test_connection()
            except Exception as e:
                results[capability] = (False, str(e))

        return results

    # --- Internal ---

    async def _load_embedding_config(
        self, spec_id: Optional[str] = None
    ) -> ProviderConfig:
        """
        Build a ProviderConfig for an embedding model from the catalog.

        Resolution order:
          1. Explicit spec_id argument (used by migration jobs).
          2. active_embedding_model_spec_id from app_config.

        API key is loaded from the per-provider key
        (`embedding_api_key__<provider>`); falls back to the legacy single-key
        `embedding_api_key` for in-place upgrades.
        """
        from app.ai.embedding_catalog import get_spec
        from app.services.config_service import (
            ACTIVE_EMBEDDING_MODEL_KEY,
            ConfigService,
            embedding_api_key_for,
        )

        svc = ConfigService(self.db)

        if spec_id is None:
            spec_id = await svc.get(ACTIVE_EMBEDDING_MODEL_KEY)
        if not spec_id:
            raise ValueError(
                "No active embedding model. Pick one in Settings → Embedding."
            )

        spec = get_spec(spec_id)  # raises UnknownEmbeddingModel if catalog miss
        api_key = (
            await svc.get(embedding_api_key_for(spec.provider))
            or await svc.get("embedding_api_key")  # legacy fallback
            or ""
        )
        base_url = await svc.get("embedding_base_url")

        return ProviderConfig(
            provider=ProviderType(spec.provider),
            api_key=api_key,
            model_id=spec.model_id,
            base_url=base_url,
            dimensions=spec.dimension,
            extra={"spec_id": spec.id},
        )

    async def _load_llm_config(self) -> ProviderConfig:
        """
        Build a ProviderConfig for the active LLM from LLM_CATALOG.

        Resolution: spec_id (new) → legacy provider+model_id derivation.
        Raises ValueError if no LLM is configured at all.
        """
        from app.ai.llm_catalog import get_spec
        from app.services.config_service import ConfigService

        svc = ConfigService(self.db)
        spec_id = await self.get_active_llm_spec_id()
        if not spec_id:
            raise ValueError("No active LLM. Pick one in Settings → LLM.")
        spec = get_spec(spec_id)

        api_key = await svc.get("llm_api_key") or ""
        base_url = await svc.get("llm_base_url")

        return ProviderConfig(
            provider=ProviderType(spec.provider),
            api_key=api_key,
            model_id=spec.model_id,
            base_url=base_url,
            extra={"spec_id": spec.id},
            spec=spec,
        )

    async def _load_vision_config(self) -> ProviderConfig:
        """Build a ProviderConfig for the active vision model from VISION_CATALOG."""
        from app.ai.vision_catalog import get_spec
        from app.services.config_service import ConfigService

        svc = ConfigService(self.db)
        spec_id = await self.get_active_vision_spec_id()
        if not spec_id:
            raise ValueError("No active vision model. Pick one in Settings → Vision.")
        spec = get_spec(spec_id)

        api_key = await svc.get("vision_api_key") or ""
        base_url = await svc.get("vision_base_url")

        return ProviderConfig(
            provider=ProviderType(spec.provider),
            api_key=api_key,
            model_id=spec.model_id,
            base_url=base_url,
            extra={"spec_id": spec.id},
            spec=spec,
        )


# ---------------------------------------------------------------------------
# Catalog-derived listings (for admin UI dropdowns)
# ---------------------------------------------------------------------------

_PROVIDER_LABELS = {
    "google": "Google Gemini",
    "openai": "OpenAI",
    "anthropic": "Anthropic",
    "ollama": "Ollama",
    "voyage": "Voyage AI",
    "cohere": "Cohere",
    "litellm": "LiteLLM",
}


def supported_providers() -> dict:
    """
    Build the {capability: [{id, name, models}]} dict from the catalogs so
    the admin UI never sees a stale hard-coded model list. Each model entry
    carries enough metadata (label, cost, context window) for the UI to
    render an informed dropdown.
    """
    from app.ai.embedding_catalog import EMBEDDING_CATALOG
    from app.ai.llm_catalog import LLM_CATALOG
    from app.ai.vision_catalog import VISION_CATALOG

    def _group(catalog: dict) -> list[dict]:
        by_provider: dict[str, list[dict]] = {}
        for spec in catalog.values():
            entry: dict = {
                "id": spec.id,
                "model_id": spec.model_id,
                "label": spec.label,
            }
            # Add capability-specific fields when present on the spec.
            for field_name in (
                "context_window_tokens",
                "max_output_tokens",
                "supports_tools",
                "supports_vision",
                "dimension",
                "max_input_tokens",
                "cost_per_1m_input_tokens",
                "cost_per_1m_output_tokens",
                "cost_per_1m_tokens",
                "cost_per_image",
                "notes",
            ):
                if hasattr(spec, field_name):
                    entry[field_name] = getattr(spec, field_name)
            by_provider.setdefault(spec.provider, []).append(entry)
        return [
            {
                "id": pid,
                "name": _PROVIDER_LABELS.get(pid, pid.title()),
                "models": models,
            }
            for pid, models in by_provider.items()
        ]

    return {
        "embedding": _group(EMBEDDING_CATALOG),
        "llm": _group(LLM_CATALOG),
        "vision": _group(VISION_CATALOG),
    }


# Backward-compat alias for any external code that imported SUPPORTED_PROVIDERS
# as a constant. Equivalent to calling supported_providers() once at import,
# but callers that need fresh data should call supported_providers() directly.
SUPPORTED_PROVIDERS = supported_providers()
