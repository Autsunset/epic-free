# -*- coding: utf-8 -*-
"""Dispatch the LLM provider patch onto ``google.genai``.

hcaptcha-challenger calls ``genai.Client().aio.models.generate_content(...)`` and
``genai.aio.files.upload(...)``. We either:

* let the native GenAI SDK run (``gemini``, optionally via a relay base URL), or
* swap ``genai.Client`` for a provider-specific shim:

  - ``openai`` + ``OPENAI_API_FORMAT=chat``     → Chat Completions endpoint
  - ``openai`` + ``OPENAI_API_FORMAT=responses`` → Responses API endpoint
  - ``anthropic``                                → Anthropic Messages API endpoint
"""

from __future__ import annotations

from typing import Any

from loguru import logger

from epic_free.llm.anthropic import AnthropicClient
from epic_free.llm.gemini import apply_gemini_patch
from epic_free.llm.openai_compat import OpenAICompatibleClient
from epic_free.llm.openai_responses import ResponsesClient


def apply_openai_compatible_patch(settings: Any):
    """Route ``genai.Client`` to the Chat Completions client."""
    try:
        from google import genai

        genai.Client = OpenAICompatibleClient
        logger.info(
            "🚀 OpenAI Chat Completions 兼容补丁已应用 | 模型: {} | 地址: {}",
            settings.OPENAI_MODEL,
            settings.OPENAI_BASE_URL,
        )
    except Exception as exc:
        logger.error(f"❌ OpenAI Chat Completions 补丁加载失败: {exc}")


def apply_openai_responses_patch(settings: Any):
    """Route ``genai.Client`` to the Responses API client."""
    try:
        from google import genai

        genai.Client = ResponsesClient
        logger.info(
            "🚀 OpenAI Responses API 补丁已应用 | 模型: {} | 地址: {}",
            settings.OPENAI_MODEL,
            settings.OPENAI_BASE_URL,
        )
    except Exception as exc:
        logger.error(f"❌ OpenAI Responses API 补丁加载失败: {exc}")


def apply_anthropic_patch(settings: Any):
    """Route ``genai.Client`` to the Anthropic Messages API client."""
    try:
        from google import genai

        genai.Client = AnthropicClient
        logger.info(
            "🚀 Anthropic Messages API 兼容补丁已应用 | 模型: {} | 地址: {}",
            settings.ANTHROPIC_MODEL,
            settings.ANTHROPIC_BASE_URL,
        )
    except Exception as exc:
        logger.error(f"❌ Anthropic 兼容补丁加载失败: {exc}")


def apply_llm_patch(settings: Any):
    """Apply the provider patch matching ``settings.LLM_PROVIDER``."""
    provider = (settings.LLM_PROVIDER or "").strip().lower()

    if provider == "openai":
        if not settings.OPENAI_API_KEY:
            logger.error(
                "LLM provider misconfigured | LLM_PROVIDER=openai but OPENAI_API_KEY is empty"
            )
            return
        if (settings.OPENAI_API_FORMAT or "chat").strip().lower() == "responses":
            apply_openai_responses_patch(settings)
        else:
            apply_openai_compatible_patch(settings)
        return

    if provider == "anthropic":
        if not settings.ANTHROPIC_API_KEY:
            logger.error(
                "LLM provider misconfigured | LLM_PROVIDER=anthropic but ANTHROPIC_API_KEY is empty"
            )
            return
        apply_anthropic_patch(settings)
        return

    if provider == "gemini" and not settings.GEMINI_API_KEY:
        logger.error("LLM provider misconfigured | LLM_PROVIDER=gemini but GEMINI_API_KEY is empty")
        return

    apply_gemini_patch(settings)
