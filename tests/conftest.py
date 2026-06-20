# -*- coding: utf-8 -*-
"""Shared test setup.

``hcaptcha_challenger``'s ``AgentConfig`` marks ``GEMINI_API_KEY`` required, and
``epic_free.config`` instantiates ``settings`` at import time. pytest imports
this conftest before any test module, so seeding placeholder credentials here
guarantees ``import epic_free.config`` (and anything that pulls it in) works in a
network-less environment regardless of test collection order. Production seeds
``GEMINI_API_KEY`` from the openai/glm key via ``_bridge_provider_credentials``.
"""

import os

os.environ.setdefault("LLM_PROVIDER", "openai")
os.environ.setdefault("OPENAI_API_KEY", "fake-test-key")
os.environ.setdefault("GEMINI_API_KEY", "fake-test-key")
