# -*- coding: utf-8 -*-
"""OpenAI Chat Completions compatible GenAI client.

The ``openai`` provider (with ``OPENAI_API_FORMAT=chat``, the default) uses this
client. It speaks the standard Chat Completions wire format and works with any
OpenAI-compatible endpoint — OpenAI itself, GLM/ZhipuAI, Azure, third-party relays, etc.

GLM-specific quirks (raw-base64 image encoding, thinking mode for ``glm-4.5*``)
are auto-detected from the model name so no separate provider is needed.

When ``OPENAI_API_FORMAT=responses``, the Responses API client in
:mod:`epic_free.llm.openai_responses` is used instead.

It impersonates ``google.genai.Client`` because ``hcaptcha-challenger`` calls
``genai.Client().aio.models.generate_content`` and ``genai.aio.files.upload``
internally — we redirect those calls to the Chat Completions endpoint.

Performance note: requests go through the shared pooled HTTP client from
:mod:`epic_free.http_client` instead of opening a new connection per call.
"""

from __future__ import annotations

import base64
from typing import Any

from epic_free.llm.base import (
    _MAX_STORED_UPLOADS,
    # re-export for backward compatibility (tests import these from here)
    _AsyncFiles,
    _AsyncNamespace,
    _BaseAsyncModels,
    _PatchedResponse,
    _strip_reasoning,
)

__all__ = [
    "OpenAICompatibleClient",
    # re-exported shared symbols
    "_AsyncFiles",
    "_AsyncModels",
    "_MAX_STORED_UPLOADS",
    "_PatchedResponse",
    "_strip_reasoning",
]


class _AsyncModels(_BaseAsyncModels):
    """Chat Completions model handler.

    GLM-specific behaviour (raw-base64 images, thinking mode) is auto-detected
    from the model name so GLM does not need a separate provider.
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._current_model: str = ""

    # ------------------------------------------------------------------ content items
    def _text_item(self, text: str) -> dict[str, Any]:
        return {"type": "text", "text": text}

    def _image_item(self, payload: bytes, mime_type: str) -> dict[str, Any]:
        encoded = base64.b64encode(payload).decode("utf-8")
        # GLM models accept raw base64 without the data: prefix; other OpenAI-compatible
        # endpoints require a data URL.
        if (self._current_model or "").startswith("glm-"):
            url = encoded
        else:
            url = f"data:{mime_type};base64,{encoded}"
        return {"type": "image_url", "image_url": {"url": url}}

    def _url_item(self, url: str) -> dict[str, Any]:
        return {"type": "image_url", "image_url": {"url": url}}

    # ------------------------------------------------------------------ payload
    def _build_payload(
        self, *, model: str, contents: Any, config: Any, kwargs: dict[str, Any]
    ) -> dict[str, Any]:
        # Store the model name so _image_item can auto-detect GLM's raw-base64 format.
        self._current_model = model

        system_text, messages, _ = self._build_message_list(contents, config)

        all_messages: list[dict[str, Any]] = []
        if system_text:
            all_messages.append({"role": "system", "content": system_text})
        all_messages.extend(messages)

        payload: dict[str, Any] = {
            "model": model,
            "messages": all_messages,
        }

        temperature = getattr(config, "temperature", None)
        if temperature is not None:
            payload["temperature"] = temperature

        if getattr(config, "response_schema", None) is not None:
            payload["response_format"] = {"type": "json_object"}

        # GLM exposes a thinking mode for its 4.5 family — auto-detected from model name.
        if getattr(config, "thinking_config", None) is not None and model.startswith("glm-4.5"):
            payload["thinking"] = {"type": "enabled"}

        payload.update({k: v for k, v in kwargs.items() if k != "config"})
        return payload

    # ------------------------------------------------------------------ response handling
    def _extract_text(self, data: dict[str, Any]) -> str:
        choices = data.get("choices") or []
        if not choices:
            raise ValueError(f"{self._provider_name} response does not contain choices")

        message = choices[0].get("message") or {}
        content = message.get("content")

        if isinstance(content, str):
            return _strip_reasoning(content)

        if isinstance(content, list):
            parts = [
                item.get("text", "")
                for item in content
                if isinstance(item, dict) and item.get("type") == "text"
            ]
            return _strip_reasoning("\n".join(parts))

        raise ValueError(f"{self._provider_name} response content is empty")

    # ------------------------------------------------------------------ endpoint / headers
    def _get_endpoint(self) -> str:
        endpoint = getattr(self._settings, self._base_url_attr).rstrip("/")
        if not endpoint.endswith("/chat/completions"):
            endpoint = f"{endpoint}/chat/completions"
        return endpoint

    def _get_headers(self) -> dict[str, str]:
        api_key = getattr(self._settings, self._api_key_attr)
        return {
            "Authorization": f"Bearer {api_key.get_secret_value()}",
            "Content-Type": "application/json",
        }


class OpenAICompatibleClient:
    """GenAI-compatible client routing to an OpenAI Chat Completions endpoint.

    Serves the ``openai`` provider with ``OPENAI_API_FORMAT=chat`` (the default).
    Also handles GLM/ZhipuAI — just point ``OPENAI_BASE_URL`` at the GLM endpoint
    and set ``OPENAI_MODEL`` to a ``glm-*`` model.
    """

    def __init__(self, *args, **kwargs):
        from epic_free.config import settings

        self._storage: dict[str, dict[str, Any]] = {}
        self.aio = _AsyncNamespace(
            settings,
            self._storage,
            models_class=_AsyncModels,
            uri_scheme="openai-local",
            provider_name="OpenAI",
            api_key_attr="OPENAI_API_KEY",
            base_url_attr="OPENAI_BASE_URL",
        )
