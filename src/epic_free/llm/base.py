# -*- coding: utf-8 -*-
"""Shared utilities for LLM provider compatibility clients.

All provider clients (Chat Completions, Responses API, Anthropic Messages)
impersonate ``google.genai.Client`` because ``hcaptcha-challenger`` calls
``genai.Client().aio.models.generate_content(...)`` and
``genai.aio.files.upload(...)`` internally.

This module holds the shared building blocks: reasoning-tag stripping, the
in-process file-upload shim, the patched response object, and a base model
handler with the common message-assembly and response-parsing logic that every
wire format needs.
"""

from __future__ import annotations

import json
import re
from contextlib import suppress
from typing import Any

import httpx
from loguru import logger
from pydantic import BaseModel

from epic_free.http_client import get_async_client
from epic_free.llm.parse import (
    GLM_VISUAL_COORDINATE_INSTRUCTION,
    _coerce_payload_for_schema,
    _ensure_list,
    _extract_challenge_type,
    _extract_json_payload,
    _guess_mime_type,
    _load_binary,
    _normalize_glm_answer_value,
    _normalize_glm_payload,
    _normalize_glm_response_text,
)

# Reasoning models (kimi-k2, qwen-thinking, deepseek-r1, glm-4.5-thinking, …)
# prepend a chain-of-thought wrapped in <think>…</think> (sometimes unclosed).
# That reasoning breaks JSON extraction downstream — both our _extract_json_payload
# and hcaptcha-challenger's own ``extract_first_json_block`` — so strip it from the
# raw response text before anything parses it. (When a gateway returns the
# chain-of-thought in a separate ``reasoning_content`` field, ``_extract_text``
# already ignores it because it only reads ``message.content``.)
_REASONING_TAG_RE = re.compile(
    r"<\s*(?:think|thinking|reasoning|analysis)\b[^>]*>.*?<\s*/\s*(?:think|thinking|reasoning|analysis)\s*>",
    re.DOTALL | re.IGNORECASE,
)
_REASONING_UNCLOSED_RE = re.compile(
    r"<\s*(?:think|thinking|reasoning|analysis)\b[^>]*>.*",
    re.DOTALL | re.IGNORECASE,
)


def _strip_reasoning(text: str) -> str:
    """Remove ``<think>…</think>`` chain-of-thought blocks reasoning models emit."""
    text = _REASONING_TAG_RE.sub("", text)
    text = _REASONING_UNCLOSED_RE.sub("", text)
    return text.strip()


# The in-process upload store lives as long as the client. Cap it so a long-lived
# scheduler can't leak image bytes across captcha solves.
_MAX_STORED_UPLOADS = 64


class _UploadedFile:
    def __init__(self, uri: str, mime_type: str):
        self.name = uri
        self.uri = uri
        self.mime_type = mime_type


class _PatchedResponse:
    """Minimal stand-in for ``genai.types.GenerateContentResponse``."""

    def __init__(self, *, text: str, parsed: Any, raw: dict[str, Any]):
        self.text = text
        self.parsed = parsed
        self._raw = raw

    def model_dump(self, mode: str = "python") -> dict[str, Any]:
        parsed = self.parsed
        if hasattr(parsed, "model_dump"):
            parsed = parsed.model_dump(mode=mode)
        return {"text": self.text, "parsed": parsed, "raw": self._raw}


class _AsyncFiles:
    """In-process upload shim.

    hcaptcha-challenger uploads images then references them by URI. We stash the
    bytes under a synthetic URI and re-attach them inline at generate_content
    time, so no real file endpoint is needed.
    """

    def __init__(self, storage: dict[str, dict[str, Any]], uri_scheme: str):
        self._storage = storage
        self._uri_scheme = uri_scheme

    async def upload(self, file: Any, **kwargs) -> _UploadedFile:
        content = _load_binary(file)
        uri = f"{self._uri_scheme}://{id(content)}"
        mime_type = kwargs.get("mime_type") or _guess_mime_type(file)
        self._storage[uri] = {"content": content, "mime_type": mime_type}
        # Bound memory: evict oldest entries beyond the cap (dicts keep insertion order).
        while len(self._storage) > _MAX_STORED_UPLOADS:
            del self._storage[next(iter(self._storage))]
        return _UploadedFile(uri=uri, mime_type=mime_type)


class _BaseAsyncModels:
    """Shared base for provider-specific model handlers.

    Each subclass implements the content-item formatters (``_text_item``,
    ``_image_item``, ``_url_item``), the payload builder, the response text
    extractor, and the endpoint/header getters. Everything else — part-to-item
    conversion, system-text collection, response parsing, error logging, and the
    HTTP call itself — is shared here.
    """

    def __init__(
        self,
        settings: Any,
        storage: dict[str, dict[str, Any]],
        *,
        provider_name: str,
        api_key_attr: str,
        base_url_attr: str,
        data_url_images: bool = True,
    ):
        self._settings = settings
        self._storage = storage
        self._provider_name = provider_name
        self._api_key_attr = api_key_attr
        self._base_url_attr = base_url_attr
        self._data_url_images = data_url_images

    # ------------------------------------------------------------------ content items
    def _text_item(self, text: str) -> dict[str, Any]:
        """Format a text part for this provider's wire format."""
        raise NotImplementedError

    def _image_item(self, payload: bytes, mime_type: str) -> dict[str, Any]:
        """Format an inline-image part for this provider's wire format."""
        raise NotImplementedError

    def _url_item(self, url: str) -> dict[str, Any]:
        """Format a URL-referenced image for this provider's wire format."""
        raise NotImplementedError

    def _part_to_content_item(self, part: Any) -> dict[str, Any] | None:
        """Convert a GenAI ``Part`` into a provider-specific content item.

        The extraction logic (text → inline_data → file_data → upload store →
        URL fallback) is identical across providers; only the content-item
        formatting differs, which is delegated to ``_text_item`` etc.
        """
        text = getattr(part, "text", None)
        if text:
            return self._text_item(text)

        inline_data = getattr(part, "inline_data", None)
        if inline_data and getattr(inline_data, "data", None):
            mime_type = getattr(inline_data, "mime_type", None) or "image/png"
            return self._image_item(inline_data.data, mime_type)

        file_data = getattr(part, "file_data", None)
        if not file_data:
            return None

        file_uri = getattr(file_data, "file_uri", None) or getattr(file_data, "uri", None)
        mime_type = getattr(file_data, "mime_type", None) or "image/png"
        if not file_uri:
            return None

        if file_uri in self._storage:
            blob = self._storage[file_uri]
            return self._image_item(blob["content"], blob["mime_type"])

        if str(file_uri).startswith(("http://", "https://", "data:")):
            return self._url_item(str(file_uri))

        return None

    def _is_image_item(self, item: dict[str, Any]) -> bool:
        """Return True if the content item represents an image."""
        return item.get("type") in ("image_url", "input_image", "image")

    # ------------------------------------------------------------------ system text
    def _collect_system_text(self, config: Any, *, has_image: bool) -> str:
        """Build the system message text from instruction + schema + hints.

        Shared across all providers. The native Gemini SDK sends response_schema
        in the request body, so the model is forced to emit the exact field names.
        None of the other wire formats (Chat / Responses / Anthropic) have a
        universally honoured schema slot, so we MUST spell the schema out in the
        prompt — otherwise the model invents its own field names and downstream
        validation fails.
        """
        parts: list[str] = []

        system_instruction = getattr(config, "system_instruction", None)
        if system_instruction:
            parts.append(str(system_instruction))

        response_schema = getattr(config, "response_schema", None)
        if isinstance(response_schema, type) and issubclass(response_schema, BaseModel):
            try:
                schema_json = response_schema.model_json_schema()
            except Exception:
                schema_json = None
            if schema_json:
                parts.append(
                    "Respond ONLY with a JSON object that EXACTLY matches this JSON "
                    "schema — use ONLY these field names, include every required field, "
                    "and add nothing else:\n" + json.dumps(schema_json, ensure_ascii=False)
                )

        if has_image:
            parts.append(GLM_VISUAL_COORDINATE_INSTRUCTION)

        return "\n\n".join(parts) if parts else ""

    # ------------------------------------------------------------------ message list
    def _build_message_list(
        self, contents: Any, config: Any
    ) -> tuple[str, list[dict[str, Any]], bool]:
        """Return (system_text, messages, has_image).

        ``messages`` is a list of ``{"role": ..., "content": [item, ...]}`` where
        each item is already in this provider's wire format. The system text is
        returned separately so each provider can place it in the right slot
        (inline message vs top-level field).
        """
        messages: list[dict[str, Any]] = []
        has_image = False

        for content in _ensure_list(contents):
            role = getattr(content, "role", None) or "user"
            items: list[dict[str, Any]] = []
            for part in _ensure_list(getattr(content, "parts", None)):
                item = self._part_to_content_item(part)
                if not item:
                    continue
                if self._is_image_item(item):
                    has_image = True
                items.append(item)
            if items:
                messages.append({"role": role, "content": items})

        system_text = self._collect_system_text(config, has_image=has_image)
        return system_text, messages, has_image

    # ------------------------------------------------------------------ response parsing
    def _parse_response(self, text: str, config: Any) -> Any:
        schema = getattr(config, "response_schema", None)
        if not schema:
            return None

        try:
            payload = _coerce_payload_for_schema(
                _normalize_glm_payload(_extract_json_payload(text)), schema, text
            )
        except Exception:
            normalized = _normalize_glm_answer_value(text)
            if normalized:
                payload = _coerce_payload_for_schema(normalized, schema, text)
            else:
                challenge_type = _extract_challenge_type(text)
                if challenge_type:
                    payload = _coerce_payload_for_schema(
                        {"challenge_type": challenge_type, "request_type": challenge_type},
                        schema,
                        text,
                    )
                else:
                    logger.warning(
                        "{} structured parse fallback failed | raw_text={}",
                        self._provider_name,
                        text[:500],
                    )
                    return None

        if isinstance(schema, type) and issubclass(schema, BaseModel):
            return schema(**payload)

        return payload

    # ------------------------------------------------------------------ error logging
    def _log_error(self, response: httpx.Response):
        body = response.text[:2000]
        code = ""
        message = ""
        with suppress(Exception):
            error = (response.json() or {}).get("error") or {}
            code = str(error.get("code") or "")
            message = str(error.get("message") or "")

        if response.status_code == 429 or code in {"1302", "1303", "1304", "1308", "1113"}:
            logger.error(
                "{} quota/rate limit issue | http_status={} | code={} | message={}",
                self._provider_name,
                response.status_code,
                code,
                message or body,
            )
            return

        if response.status_code in {401, 403} or code in {
            "1000",
            "1001",
            "1002",
            "1003",
            "1004",
        }:
            logger.error(
                "{} auth issue | http_status={} | code={} | message={}",
                self._provider_name,
                response.status_code,
                code,
                message or body,
            )
            return

        logger.error(
            "{} request failed | status={} | code={} | body={}",
            self._provider_name,
            response.status_code,
            code,
            body,
        )

    # ------------------------------------------------------------------ abstract
    def _build_payload(
        self, *, model: str, contents: Any, config: Any, kwargs: dict[str, Any]
    ) -> dict[str, Any]:
        raise NotImplementedError

    def _extract_text(self, data: dict[str, Any]) -> str:
        raise NotImplementedError

    def _get_endpoint(self) -> str:
        raise NotImplementedError

    def _get_headers(self) -> dict[str, str]:
        raise NotImplementedError

    async def generate_content(self, model: str, contents: Any, **kwargs) -> _PatchedResponse:
        config = kwargs.pop("config", None)
        if config is None:
            raise ValueError(f"config is required for {self._provider_name} compatibility mode")

        endpoint = self._get_endpoint()
        payload = self._build_payload(model=model, contents=contents, config=config, kwargs=kwargs)
        headers = self._get_headers()

        client = await get_async_client()
        response = await client.post(endpoint, headers=headers, json=payload)
        if response.is_error:
            self._log_error(response)
            response.raise_for_status()
        data = response.json()

        text = _normalize_glm_response_text(self._extract_text(data))
        parsed = self._parse_response(text, config)
        return _PatchedResponse(text=text, parsed=parsed, raw=data)


class _AsyncNamespace:
    """Provides ``.files`` and ``.models`` attributes like ``genai.Client.aio``."""

    def __init__(
        self,
        settings: Any,
        storage: dict[str, dict[str, Any]],
        *,
        models_class: type[_BaseAsyncModels],
        uri_scheme: str,
        **model_kwargs: Any,
    ):
        self.files = _AsyncFiles(storage, uri_scheme)
        self.models = models_class(settings, storage, **model_kwargs)
