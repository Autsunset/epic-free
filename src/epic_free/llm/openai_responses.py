# -*- coding: utf-8 -*-
"""OpenAI Responses API compatible GenAI client.

When ``LLM_PROVIDER=openai`` and ``OPENAI_API_FORMAT=responses``, this client
replaces :class:`epic_free.llm.openai_compat.OpenAICompatibleClient` and routes
``generate_content`` calls to the ``/responses`` endpoint using the newer
Responses API wire format (``input_text`` / ``input_image`` content parts,
``text.format`` for JSON mode, ``developer`` role for system instructions).

It impersonates ``google.genai.Client`` for the same reason as the Chat
Completions client — ``hcaptcha-challenger`` calls ``genai.Client()`` internally.
"""

from __future__ import annotations

import base64
from typing import Any

from epic_free.llm.base import _AsyncNamespace, _BaseAsyncModels, _strip_reasoning


class _ResponsesModels(_BaseAsyncModels):
    """OpenAI Responses API model handler."""

    # ------------------------------------------------------------------ content items
    def _text_item(self, text: str) -> dict[str, Any]:
        return {"type": "input_text", "text": text}

    def _image_item(self, payload: bytes, mime_type: str) -> dict[str, Any]:
        # Responses API: image_url is a string (data URL or HTTPS URL), not an object.
        encoded = base64.b64encode(payload).decode("utf-8")
        url = f"data:{mime_type};base64,{encoded}"
        return {"type": "input_image", "image_url": url}

    def _url_item(self, url: str) -> dict[str, Any]:
        return {"type": "input_image", "image_url": url}

    # ------------------------------------------------------------------ payload
    def _build_payload(
        self, *, model: str, contents: Any, config: Any, kwargs: dict[str, Any]
    ) -> dict[str, Any]:
        system_text, messages, _ = self._build_message_list(contents, config)

        input_items: list[dict[str, Any]] = []
        if system_text:
            # Responses API recommends ``developer`` over the legacy ``system`` role.
            input_items.append({"role": "developer", "content": system_text})
        input_items.extend(messages)

        payload: dict[str, Any] = {
            "model": model,
            "input": input_items,
        }

        temperature = getattr(config, "temperature", None)
        if temperature is not None:
            payload["temperature"] = temperature

        if getattr(config, "response_schema", None) is not None:
            payload["text"] = {"format": {"type": "json_object"}}

        payload.update({k: v for k, v in kwargs.items() if k != "config"})
        return payload

    # ------------------------------------------------------------------ response handling
    def _extract_text(self, data: dict[str, Any]) -> str:
        # Convenience field: concatenated output text (present in all non-empty responses).
        output_text = data.get("output_text")
        if isinstance(output_text, str) and output_text:
            return _strip_reasoning(output_text)

        # Fallback: walk the output array for message → output_text parts.
        output = data.get("output") or []
        if not output:
            raise ValueError(f"{self._provider_name} response does not contain output")

        parts: list[str] = []
        for item in output:
            if not isinstance(item, dict) or item.get("type") != "message":
                continue
            for block in item.get("content") or []:
                if isinstance(block, dict) and block.get("type") == "output_text":
                    parts.append(block.get("text", ""))

        if not parts:
            raise ValueError(f"{self._provider_name} response output_text is empty")

        return _strip_reasoning("\n".join(parts))

    # ------------------------------------------------------------------ endpoint / headers
    def _get_endpoint(self) -> str:
        endpoint = getattr(self._settings, self._base_url_attr).rstrip("/")
        if not endpoint.endswith("/responses"):
            endpoint = f"{endpoint}/responses"
        return endpoint

    def _get_headers(self) -> dict[str, str]:
        api_key = getattr(self._settings, self._api_key_attr)
        return {
            "Authorization": f"Bearer {api_key.get_secret_value()}",
            "Content-Type": "application/json",
        }


class ResponsesClient:
    """GenAI-compatible client routing to the OpenAI Responses API endpoint.

    Used when ``LLM_PROVIDER=openai`` and ``OPENAI_API_FORMAT=responses``.
    """

    def __init__(self, *args, **kwargs):
        from epic_free.config import settings

        self._storage: dict[str, dict[str, Any]] = {}
        self.aio = _AsyncNamespace(
            settings,
            self._storage,
            models_class=_ResponsesModels,
            uri_scheme="openai-responses-local",
            provider_name="OpenAI",
            api_key_attr="OPENAI_API_KEY",
            base_url_attr="OPENAI_BASE_URL",
        )
