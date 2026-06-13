# -*- coding: utf-8 -*-
"""Gemini (native GenAI SDK) patch.

When ``LLM_PROVIDER=gemini`` we keep the real ``google.genai`` SDK but pin the
API key and optionally override the base URL (e.g. for the AiHubMix relay), and
bypass the File API by inlining uploaded images.
"""

from __future__ import annotations

from typing import Any

from loguru import logger

from epic_free.llm.parse import _ensure_list, _guess_mime_type, _load_binary

# The in-process upload cache lives for the whole run (the scheduler stays up for
# weeks), so cap it to avoid an unbounded memory leak across captcha solves.
_GEMINI_FILE_CACHE_MAX = 64


def apply_gemini_patch(settings: Any):
    if not settings.GEMINI_API_KEY:
        return

    try:
        from google import genai
        from google.genai import types

        orig_init = genai.Client.__init__

        def new_init(self, *args, **kwargs):
            kwargs["api_key"] = settings.GEMINI_API_KEY.get_secret_value()

            base_url = settings.GEMINI_BASE_URL.rstrip("/")
            if base_url:
                if base_url.endswith("/v1"):
                    base_url = base_url[:-3]
                if not base_url.endswith("/gemini"):
                    base_url = f"{base_url}/gemini"

                kwargs["http_options"] = types.HttpOptions(base_url=base_url)
                logger.info(
                    f"🚀 Gemini 兼容补丁已应用 | 模型: {settings.GEMINI_MODEL} | 地址: {base_url}"
                )
            else:
                logger.info(f"🚀 Gemini 官方接口已应用默认配置 | 模型: {settings.GEMINI_MODEL}")
            orig_init(self, *args, **kwargs)

        genai.Client.__init__ = new_init

        file_cache: dict[str, bytes] = {}

        async def patched_upload(self_files, file, **kwargs):
            content = _load_binary(file)
            file_id = f"bypass_{id(content)}"
            file_cache[file_id] = content
            # Bound memory: evict oldest entries beyond the cap (dicts keep insertion order).
            while len(file_cache) > _GEMINI_FILE_CACHE_MAX:
                del file_cache[next(iter(file_cache))]
            return types.File(name=file_id, uri=file_id, mime_type=_guess_mime_type(file))

        orig_generate = genai.models.AsyncModels.generate_content

        async def patched_generate(self_models, model, contents, **kwargs):
            normalized = _ensure_list(contents)
            for content in normalized:
                for index, part in enumerate(_ensure_list(getattr(content, "parts", None))):
                    file_data = getattr(part, "file_data", None)
                    file_uri = getattr(file_data, "file_uri", None) if file_data else None
                    if file_uri in file_cache:
                        content.parts[index] = types.Part.from_bytes(
                            data=file_cache[file_uri], mime_type=_guess_mime_type(file_uri)
                        )

            return await orig_generate(self_models, model=model, contents=normalized, **kwargs)

        genai.files.AsyncFiles.upload = patched_upload
        genai.models.AsyncModels.generate_content = patched_generate
        logger.info("🚀 Gemini 文件上传兼容补丁加载成功")
    except Exception as exc:
        logger.error(f"❌ Gemini 兼容补丁加载失败: {exc}")
