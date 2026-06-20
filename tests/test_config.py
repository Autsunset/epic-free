# -*- coding: utf-8 -*-
"""Tests for EpicSettings provider auto-detection, credential bridging, and the
configuration-error helpers (pure logic, no network)."""

import pytest

from epic_free.config import EpicSettings

# Env vars that could leak in from the host and skew detection; cleared per test.
_PROVIDER_ENV = [
    "LLM_PROVIDER",
    "GEMINI_API_KEY",
    "GLM_API_KEY",
    "OPENAI_API_KEY",
    "GEMINI_MODEL",
    "GLM_MODEL",
    "OPENAI_MODEL",
    "CHALLENGE_CLASSIFIER_MODEL",
    "IMAGE_CLASSIFIER_MODEL",
    "SPATIAL_POINT_REASONER_MODEL",
    "SPATIAL_PATH_REASONER_MODEL",
    "EPIC_EMAIL",
    "EPIC_PASSWORD",
]


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for name in _PROVIDER_ENV:
        monkeypatch.delenv(name, raising=False)


def _make(**kwargs) -> EpicSettings:
    # _env_file=None so a developer's real .env never bleeds into the assertions.
    return EpicSettings(_env_file=None, **kwargs)


# --------------------------------------------------------------------------- provider detection
def test_provider_autodetect_prefers_openai():
    s = _make(OPENAI_API_KEY="ok", GLM_API_KEY="gk", GEMINI_API_KEY="gemk")
    assert s.LLM_PROVIDER == "openai"


def test_provider_autodetect_glm_when_no_openai():
    s = _make(GLM_API_KEY="gk", GEMINI_API_KEY="gemk")
    assert s.LLM_PROVIDER == "glm"


def test_provider_autodetect_gemini_fallback():
    s = _make(GEMINI_API_KEY="gemk")
    assert s.LLM_PROVIDER == "gemini"


def test_explicit_provider_is_respected_over_detection():
    s = _make(LLM_PROVIDER="glm", OPENAI_API_KEY="ok", GLM_API_KEY="gk")
    assert s.LLM_PROVIDER == "glm"


def test_blank_provider_with_only_gemini_key_is_gemini():
    s = _make(GEMINI_API_KEY="gemk")
    assert s.LLM_PROVIDER == "gemini"


def test_no_keys_at_all_is_rejected_by_base_model():
    # The base AgentConfig requires GEMINI_API_KEY; seeding from openai/glm is
    # what makes a non-Gemini deploy constructible. With zero keys there is
    # nothing to seed, so construction must fail loudly.
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        _make()


# --------------------------------------------------------------------------- GEMINI_API_KEY seeding
def test_gemini_key_seeded_from_openai():
    s = _make(OPENAI_API_KEY="ok")
    assert s.GEMINI_API_KEY is not None
    assert s.GEMINI_API_KEY.get_secret_value() == "ok"


def test_gemini_key_seeded_from_glm_when_no_openai():
    s = _make(GLM_API_KEY="gk")
    assert s.GEMINI_API_KEY is not None
    assert s.GEMINI_API_KEY.get_secret_value() == "gk"


def test_existing_gemini_key_is_preserved():
    s = _make(OPENAI_API_KEY="ok", GEMINI_API_KEY="real-gem")
    assert s.GEMINI_API_KEY.get_secret_value() == "real-gem"


# --------------------------------------------------------------------------- per-task model defaults
def test_task_models_default_to_provider_model():
    s = _make(LLM_PROVIDER="openai", OPENAI_API_KEY="ok", OPENAI_MODEL="gpt-x")
    assert s.CHALLENGE_CLASSIFIER_MODEL == "gpt-x"
    assert s.IMAGE_CLASSIFIER_MODEL == "gpt-x"
    assert s.SPATIAL_POINT_REASONER_MODEL == "gpt-x"
    assert s.SPATIAL_PATH_REASONER_MODEL == "gpt-x"


def test_explicit_task_model_is_preserved():
    s = _make(
        LLM_PROVIDER="openai",
        OPENAI_API_KEY="ok",
        OPENAI_MODEL="gpt-x",
        CHALLENGE_CLASSIFIER_MODEL="custom",
    )
    assert s.CHALLENGE_CLASSIFIER_MODEL == "custom"
    # the other three still fall back to the provider default
    assert s.IMAGE_CLASSIFIER_MODEL == "gpt-x"


# --------------------------------------------------------------------------- configuration errors
def test_llm_configuration_error_when_provider_key_missing():
    # GEMINI key present so the base model validates, but the selected provider's
    # own key is absent -> our helper surfaces a clear, actionable error.
    s = _make(LLM_PROVIDER="openai", GEMINI_API_KEY="g")
    assert s.llm_configuration_error is not None
    assert "OPENAI_API_KEY" in s.llm_configuration_error


def test_epic_configuration_error_when_email_missing():
    s = _make(GEMINI_API_KEY="g")
    assert s.epic_configuration_error is not None
    assert "EPIC_EMAIL" in s.epic_configuration_error


def test_epic_configuration_error_when_password_missing():
    s = _make(GEMINI_API_KEY="g", EPIC_EMAIL="a@b.com")
    assert s.epic_configuration_error is not None
    assert "EPIC_PASSWORD" in s.epic_configuration_error


def test_no_configuration_errors_when_complete():
    s = _make(OPENAI_API_KEY="ok", EPIC_EMAIL="a@b.com", EPIC_PASSWORD="pw")
    assert s.llm_configuration_error is None
    assert s.epic_configuration_error is None
