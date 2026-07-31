# -*- coding: utf-8 -*-
"""Anthropic Messages API compatible GenAI client.

When ``LLM_PROVIDER=anthropic``, this client impersonates ``google.genai.Client``
and routes ``generate_content`` calls to the Anthropic Messages API (``/v1/messages``)
using Anthropic's wire format (``text`` / ``image`` content parts, top-level
``system`` field, ``x-api-key`` + ``anthropic-version`` headers).

Anthropic has no native JSON-mode parameter, so structured output relies entirely
on the prompt-injected JSON schema (the same mechanism used by the Chat
Completions client for GLM). ``max_tokens`` is required by the API and defaults
to 4096.
"""

from __future__ import annotations

import base64
from typing import Any

from epic_free.llm.base import _AsyncNamespace, _BaseAsyncModels, _strip_reasoning

# Anthropic requires max_tokens; 4096 is generous for hCaptcha JSON answers.
_DEFAULT_MAX_TOKENS = 4096

# Stable API version header — pinned to the widely-supported date.
_ANTHROPIC_VERSION = "2023-06-01"


class _AnthropicModels(_BaseAsyncModels):
    """Anthropic Messages API model handler."""

    # ------------------------------------------------------------------ content items
    def _text_item(self, text: str) -> dict[str, Any]:
        return {"type": "text", "text": text}

    def _image_item(self, payload: bytes, mime_type: str) -> dict[str, Any]:
        encoded = base64.b64encode(payload).decode("utf-8")
        return {
            "type": "image",
            "source": {
                "type": "base64",
                "media_type": mime_type,
                "data": encoded,
            },
        }

    def _url_item(self, url: str) -> dict[str, Any]:
        return {"type": "image", "source": {"type": "url", "url": url}}

    # ------------------------------------------------------------------ payload
    def _build_payload(
        self, *, model: str, contents: Any, config: Any, kwargs: dict[str, Any]
    ) -> dict[str, Any]:
        system_text, messages, _ = self._build_message_list(contents, config)

        payload: dict[str, Any] = {
            "model": model,
            "max_tokens": _DEFAULT_MAX_TOKENS,
            "messages": messages,
        }
        if system_text:
            payload["system"] = system_text

        temperature = getattr(config, "temperature", None)
        if temperature is not None:
            payload["temperature"] = temperature

        # No response_format / text.format equivalent — the JSON schema is already
        # injected into the system prompt by _collect_system_text.

        payload.update({k: v for k, v in kwargs.items() if k != "config"})
        return payload

    # ------------------------------------------------------------------ response handling
    def _extract_text(self, data: dict[str, Any]) -> str:
        content = data.get("content") or []
        if not content:
            raise ValueError(f"{self._provider_name} response does not contain content")

        # Filter for text blocks (skip thinking blocks if extended thinking is enabled).
        parts = [
            block.get("text", "")
            for block in content
            if isinstance(block, dict) and block.get("type") == "text"
        ]

        if not parts:
            raise ValueError(f"{self._provider_name} response has no text content blocks")

        return _strip_reasoning("\n".join(parts))

    # ------------------------------------------------------------------ endpoint / headers
    def _get_endpoint(self) -> str:
        endpoint = getattr(self._settings, self._base_url_attr).rstrip("/")
        if not endpoint.endswith("/v1/messages"):
            if endpoint.endswith("/v1"):
                endpoint = f"{endpoint}/messages"
            else:
                endpoint = f"{endpoint}/v1/messages"
        return endpoint

    def _get_headers(self) -> dict[str, str]:
        api_key = getattr(self._settings, self._api_key_attr)
        return {
            "x-api-key": api_key.get_secret_value(),
            "anthropic-version": _ANTHROPIC_VERSION,
            "Content-Type": "application/json",
        }


class AnthropicClient:
    """GenAI-compatible client routing to the Anthropic Messages API endpoint.

    Used when ``LLM_PROVIDER=anthropic``.
    """

    def __init__(self, *args, **kwargs):
        from epic_free.config import settings

        self._storage: dict[str, dict[str, Any]] = {}
        self.aio = _AsyncNamespace(
            settings,
            self._storage,
            models_class=_AnthropicModels,
            uri_scheme="anthropic-local",
            provider_name="Anthropic",
            api_key_attr="ANTHROPIC_API_KEY",
            base_url_attr="ANTHROPIC_BASE_URL",
        )
