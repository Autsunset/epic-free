# -*- coding: utf-8 -*-
"""LLM provider compatibility layer (merges OpenAI-compatible providers)."""
from epic_free.llm.patch import apply_llm_patch

__all__ = ["apply_llm_patch"]
