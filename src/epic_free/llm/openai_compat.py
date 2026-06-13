# -*- coding: utf-8 -*-
"""Unified OpenAI-compatible GenAI client.

Both the ``glm`` and ``openai`` providers speak the OpenAI Chat Completions wire
format, so a single client serves them both. It impersonates ``google.genai.Client``
because ``hcaptcha-challenger`` calls ``genai.Client().aio.models.generate_content``
and ``genai.aio.files.upload`` internally — we redirect those calls to an
OpenAI-compatible endpoint.

This merges the previously-separate OpenAI feature branch into the mainline:
``LLM_PROVIDER`` ∈ {gemini, glm, openai}, where glm/openai share this client.

Performance note: requests go through the shared pooled HTTP client from
:mod:`epic_free.http_client` instead of opening a new connection per call.
"""

from __future__ import annotations

import base64
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
        return _UploadedFile(uri=uri, mime_type=mime_type)


class _AsyncModels:
    def __init__(
        self,
        settings: Any,
        storage: dict[str, dict[str, Any]],
        *,
        provider_name: str,
        api_key_attr: str,
        base_url_attr: str,
        data_url_images: bool,
    ):
        self._settings = settings
        self._storage = storage
        self._provider_name = provider_name
        self._api_key_attr = api_key_attr
        self._base_url_attr = base_url_attr
        self._data_url_images = data_url_images

    # ------------------------------------------------------------------ message assembly
    def _to_image_part(self, payload: bytes, mime_type: str) -> dict[str, Any]:
        encoded = base64.b64encode(payload).decode("utf-8")
        url = f"data:{mime_type};base64,{encoded}" if self._data_url_images else encoded
        return {"type": "image_url", "image_url": {"url": url}}

    def _part_to_content_item(self, part: Any) -> dict[str, Any] | None:
        text = getattr(part, "text", None)
        if text:
            return {"type": "text", "text": text}

        inline_data = getattr(part, "inline_data", None)
        if inline_data and getattr(inline_data, "data", None):
            mime_type = getattr(inline_data, "mime_type", None) or "image/png"
            return self._to_image_part(inline_data.data, mime_type)

        file_data = getattr(part, "file_data", None)
        if not file_data:
            return None

        file_uri = getattr(file_data, "file_uri", None) or getattr(file_data, "uri", None)
        mime_type = getattr(file_data, "mime_type", None) or "image/png"
        if not file_uri:
            return None

        if file_uri in self._storage:
            blob = self._storage[file_uri]
            return self._to_image_part(blob["content"], blob["mime_type"])

        if str(file_uri).startswith(("http://", "https://", "data:")):
            return {"type": "image_url", "image_url": {"url": str(file_uri)}}

        return None

    def _build_messages(self, contents: Any, config: Any) -> list[dict[str, Any]]:
        messages: list[dict[str, Any]] = []
        system_messages: list[str] = []
        has_image = False

        system_instruction = getattr(config, "system_instruction", None)
        if system_instruction:
            system_messages.append(str(system_instruction))

        # The native Gemini SDK sends response_schema in the request body, so the
        # model is forced to emit the exact field names. The OpenAI Chat
        # Completions wire format has no such slot that every gateway honours, so
        # we MUST spell the schema out in the prompt — otherwise the model invents
        # its own field names (e.g. "checkout_modal_open" vs "checkout_open") and
        # the downstream response_schema(**payload) validation fails. Verified
        # necessary against kimi-k2.5.
        response_schema = getattr(config, "response_schema", None)
        if isinstance(response_schema, type) and issubclass(response_schema, BaseModel):
            try:
                schema_json = response_schema.model_json_schema()
            except Exception:
                schema_json = None
            if schema_json:
                system_messages.append(
                    "Respond ONLY with a JSON object that EXACTLY matches this JSON "
                    "schema — use ONLY these field names, include every required field, "
                    "and add nothing else:\n" + json.dumps(schema_json, ensure_ascii=False)
                )

        for content in _ensure_list(contents):
            role = getattr(content, "role", None) or "user"
            items = []
            for part in _ensure_list(getattr(content, "parts", None)):
                item = self._part_to_content_item(part)
                if not item:
                    continue
                if item.get("type") == "image_url":
                    has_image = True
                items.append(item)
            if items:
                messages.append({"role": role, "content": items})

        # GLM benefits from an explicit hint to read the printed coordinate grid
        # on coordinate challenges. Harmless for OpenAI-compatible endpoints.
        if has_image:
            system_messages.append(GLM_VISUAL_COORDINATE_INSTRUCTION)

        if system_messages:
            messages.insert(0, {"role": "system", "content": "\n\n".join(system_messages)})

        return messages

    def _build_payload(
        self, *, model: str, contents: Any, config: Any, kwargs: dict[str, Any]
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": model,
            "messages": self._build_messages(contents, config),
        }

        temperature = getattr(config, "temperature", None)
        if temperature is not None:
            payload["temperature"] = temperature

        if getattr(config, "response_schema", None) is not None:
            payload["response_format"] = {"type": "json_object"}

        # GLM exposes a thinking mode for its 4.5 family.
        if (
            self._provider_name == "GLM"
            and getattr(config, "thinking_config", None) is not None
            and model.startswith("glm-4.5")
        ):
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

    async def generate_content(self, model: str, contents: Any, **kwargs) -> _PatchedResponse:
        config = kwargs.pop("config", None)
        if config is None:
            raise ValueError(f"config is required for {self._provider_name} compatibility mode")

        endpoint = getattr(self._settings, self._base_url_attr).rstrip("/")
        if not endpoint.endswith("/chat/completions"):
            endpoint = f"{endpoint}/chat/completions"

        payload = self._build_payload(model=model, contents=contents, config=config, kwargs=kwargs)
        api_key = getattr(self._settings, self._api_key_attr)
        headers = {
            "Authorization": f"Bearer {api_key.get_secret_value()}",
            "Content-Type": "application/json",
        }

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
    def __init__(
        self,
        settings: Any,
        storage: dict[str, dict[str, Any]],
        *,
        provider_name: str,
        api_key_attr: str,
        base_url_attr: str,
        uri_scheme: str,
        data_url_images: bool,
    ):
        self.files = _AsyncFiles(storage, uri_scheme)
        self.models = _AsyncModels(
            settings,
            storage,
            provider_name=provider_name,
            api_key_attr=api_key_attr,
            base_url_attr=base_url_attr,
            data_url_images=data_url_images,
        )


class OpenAICompatibleClient:
    """GenAI-compatible client routing to an OpenAI Chat Completions endpoint.

    A single class serves both the ``glm`` and ``openai`` providers; which one
    is active is decided from ``settings.LLM_PROVIDER`` at construction time.
    """

    def __init__(self, *args, **kwargs):
        from epic_free.config import settings

        provider = (getattr(settings, "LLM_PROVIDER", "") or "").strip().lower()
        if provider == "glm":
            provider_name = "GLM"
            api_key_attr = "GLM_API_KEY"
            base_url_attr = "GLM_BASE_URL"
            uri_scheme = "glm-local"
            data_url_images = False
        else:
            provider_name = "OpenAI"
            api_key_attr = "OPENAI_API_KEY"
            base_url_attr = "OPENAI_BASE_URL"
            uri_scheme = "openai-local"
            data_url_images = True

        self._storage: dict[str, dict[str, Any]] = {}
        self.aio = _AsyncNamespace(
            settings,
            self._storage,
            provider_name=provider_name,
            api_key_attr=api_key_attr,
            base_url_attr=base_url_attr,
            uri_scheme=uri_scheme,
            data_url_images=data_url_images,
        )
