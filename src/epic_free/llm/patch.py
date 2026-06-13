# -*- coding: utf-8 -*-
"""Dispatch the LLM provider patch onto ``google.genai``.

hcaptcha-challenger calls ``genai.Client().aio.models.generate_content(...)`` and
``genai.aio.files.upload(...)``. We either:

* let the native GenAI SDK run (``gemini``, optionally via a relay base URL), or
* swap ``genai.Client`` for our OpenAI-compatible shim (``glm`` / ``openai``).
"""
from __future__ import annotations

from typing import Any

from loguru import logger

from epic_free.llm.gemini import apply_gemini_patch
from epic_free.llm.openai_compat import OpenAICompatibleClient


def apply_openai_compatible_patch(settings: Any):
    """Route ``genai.Client`` to the unified OpenAI-compatible client."""
    try:
        from google import genai

        genai.Client = OpenAICompatibleClient
        provider = (settings.LLM_PROVIDER or "").lower()
        if provider == "glm":
            logger.info(
                "🚀 GLM 兼容补丁已应用 | 模型: {} | 地址: {}",
                settings.GLM_MODEL,
                settings.GLM_BASE_URL,
            )
        else:
            logger.info(
                "🚀 OpenAI 兼容补丁已应用 | 模型: {} | 地址: {}",
                settings.OPENAI_MODEL,
                settings.OPENAI_BASE_URL,
            )
    except Exception as exc:
        logger.error(f"❌ OpenAI-compatible 补丁加载失败: {exc}")


def apply_llm_patch(settings: Any):
    """Apply the provider patch matching ``settings.LLM_PROVIDER``."""
    provider = (settings.LLM_PROVIDER or "").strip().lower()

    if provider in {"openai", "glm"}:
        if provider == "glm" and not settings.GLM_API_KEY:
            logger.error("LLM provider misconfigured | LLM_PROVIDER=glm but GLM_API_KEY is empty")
            return
        if provider == "openai" and not settings.OPENAI_API_KEY:
            logger.error(
                "LLM provider misconfigured | LLM_PROVIDER=openai but OPENAI_API_KEY is empty"
            )
            return
        apply_openai_compatible_patch(settings)
        return

    if provider == "gemini" and not settings.GEMINI_API_KEY:
        logger.error("LLM provider misconfigured | LLM_PROVIDER=gemini but GEMINI_API_KEY is empty")
        return

    apply_gemini_patch(settings)
