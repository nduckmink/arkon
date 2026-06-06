"""
LiteLLM provider — embedding, LLM, and vision via the LiteLLM SDK.

Routes through LiteLLM's unified Python SDK to 100+ LLM providers
(Anthropic, OpenAI, Google, Bedrock, Azure, Ollama, etc.) using
provider-prefixed model IDs. ``drop_params=True`` is always passed so
provider-unsupported kwargs are silently dropped.

Works in two modes:
  - Direct SDK: set api_key per provider (ANTHROPIC_API_KEY, etc.)
  - Via proxy: set base_url to a running LiteLLM proxy
"""

import asyncio
import base64
import json
from typing import Optional

from loguru import logger

from app.ai.agent_protocol import (
    AssistantTurn,
    ToolCall,
)
from app.ai.providers.base import (
    EmbeddingProvider,
    LLMProvider,
    VisionProvider,
)


def _import_litellm():
    try:
        import litellm

        return litellm
    except ImportError as exc:
        raise ImportError(
            "litellm is not installed. Install with: pip install 'arkon[litellm]'"
        ) from exc


class LiteLLMEmbedding(EmbeddingProvider):
    """LiteLLM embedding provider."""

    async def embed(self, text: str) -> list[float]:
        litellm = _import_litellm()

        kwargs: dict = {
            "model": self.config.model_id,
            "input": [text],
            "drop_params": True,
        }
        if self.config.api_key:
            kwargs["api_key"] = self.config.api_key
        if self.config.base_url:
            kwargs["api_base"] = self.config.base_url

        response = await litellm.aembedding(**kwargs)
        embedding = response.data[0].embedding

        if self.config.dimensions and len(embedding) > self.config.dimensions:
            embedding = embedding[: self.config.dimensions]
        return embedding

    async def embed_batch(
        self, texts: list[str], concurrency: int = 5
    ) -> list[list[float]]:
        semaphore = asyncio.Semaphore(concurrency)

        async def _embed_one(text: str) -> list[float]:
            async with semaphore:
                return await self.embed(text)

        results = await asyncio.gather(*[_embed_one(t) for t in texts])
        return list(results)

    async def test_connection(self) -> tuple[bool, str]:
        try:
            result = await self.embed("test connection")
            dim = len(result)
            return True, f"OK — model={self.config.model_id}, dimensions={dim}"
        except Exception as e:
            return False, f"LiteLLM embedding error: {e}"


class LiteLLMChat(LLMProvider):
    """LiteLLM LLM provider."""

    async def generate(
        self,
        prompt: str,
        system: Optional[str] = None,
        max_tokens: Optional[int] = None,
        temperature: float = 0.7,
    ) -> str:
        litellm = _import_litellm()

        messages: list[dict] = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})

        kwargs: dict = {
            "model": self.config.model_id,
            "messages": messages,
            "temperature": temperature,
            "drop_params": True,
        }
        if self.config.api_key:
            kwargs["api_key"] = self.config.api_key
        if self.config.base_url:
            kwargs["api_base"] = self.config.base_url
        if max_tokens is not None:
            kwargs["max_tokens"] = max_tokens

        response = await litellm.acompletion(**kwargs)
        return response.choices[0].message.content or ""

    async def generate_with_tools(
        self,
        messages: list[dict],
        tools: list[dict],
        system: Optional[str] = None,
        max_tokens: Optional[int] = None,
        temperature: float = 0.2,
    ) -> AssistantTurn:
        litellm = _import_litellm()

        llm_messages: list[dict] = []
        if system:
            llm_messages.append({"role": "system", "content": system})
        llm_messages.extend(messages)

        kwargs: dict = {
            "model": self.config.model_id,
            "messages": llm_messages,
            "tools": tools,
            "temperature": temperature,
            "drop_params": True,
        }
        if self.config.api_key:
            kwargs["api_key"] = self.config.api_key
        if self.config.base_url:
            kwargs["api_base"] = self.config.base_url
        if max_tokens is not None:
            kwargs["max_tokens"] = max_tokens

        response = await litellm.acompletion(**kwargs)

        choice = response.choices[0]
        message = choice.message
        text = message.content
        tool_calls: list[ToolCall] = []

        if hasattr(message, "tool_calls") and message.tool_calls:
            for tc in message.tool_calls:
                args: dict = {}
                if tc.function.arguments:
                    try:
                        args = json.loads(tc.function.arguments)
                    except Exception:
                        pass
                tool_calls.append(
                    ToolCall(id=tc.id, name=tc.function.name, arguments=args)
                )

        reason_map = {
            "stop": "end_turn",
            "tool_calls": "tool_use",
            "length": "max_tokens",
        }
        finish_reason = reason_map.get(choice.finish_reason or "stop", "end_turn")

        return AssistantTurn(
            text=text or None,
            tool_calls=tool_calls,
            finish_reason=finish_reason,
        )

    async def test_connection(self) -> tuple[bool, str]:
        try:
            result = await self.generate("Say 'OK'", max_tokens=10, temperature=0)
            return True, f"OK — model={self.config.model_id}, response='{result[:50]}'"
        except Exception as e:
            return False, f"LiteLLM LLM error: {e}"


class LiteLLMVision(VisionProvider):
    """LiteLLM vision provider."""

    async def analyze_image(
        self,
        image_data: bytes,
        mime_type: str = "image/jpeg",
        prompt: Optional[str] = None,
    ) -> str:
        litellm = _import_litellm()

        if not prompt:
            prompt = (
                "Describe this image in detail. "
                "If it's a diagram, flowchart, or table, explain the meaning and steps. "
                "If it's a regular image, provide a concise description."
            )

        b64_image = base64.b64encode(image_data).decode("utf-8")
        data_url = f"data:{mime_type};base64,{b64_image}"

        kwargs: dict = {
            "model": self.config.model_id,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt},
                        {
                            "type": "image_url",
                            "image_url": {"url": data_url, "detail": "low"},
                        },
                    ],
                }
            ],
            "temperature": 0.2,
            "drop_params": True,
        }
        if self.config.api_key:
            kwargs["api_key"] = self.config.api_key
        if self.config.base_url:
            kwargs["api_base"] = self.config.base_url

        for attempt in range(3):
            try:
                response = await litellm.acompletion(**kwargs)
                return response.choices[0].message.content or ""
            except Exception as e:
                logger.warning(f"LiteLLM Vision attempt {attempt + 1} failed: {e}")
                if attempt < 2:
                    await asyncio.sleep(2)
        return ""

    async def test_connection(self) -> tuple[bool, str]:
        try:
            tiny_png = (
                b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01"
                b"\x00\x00\x00\x01\x08\x02\x00\x00\x00\x90wS\xde\x00"
                b"\x00\x00\x0cIDATx\x9cc\xf8\x0f\x00\x00\x01\x01\x00"
                b"\x05\x18\xd8N\x00\x00\x00\x00IEND\xaeB`\x82"
            )
            await self.analyze_image(tiny_png, "image/png", "What is this?")
            return True, f"OK — model={self.config.model_id}"
        except Exception as e:
            return False, f"LiteLLM Vision error: {e}"
