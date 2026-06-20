# -*- coding: utf-8 -*-
"""Application configuration.

Merges three LLM providers into one settings model:

* ``gemini`` — native Google GenAI SDK (optionally via a Gemini-compatible relay)
* ``glm``    — OpenAI-compatible Chat Completions endpoint (ZhipuAI / BigModel)
* ``openai`` — OpenAI-compatible Chat Completions endpoint (OpenAI or any relay)

``glm`` and ``openai`` share the same wire format and are served by a single
client (:class:`epic_free.llm.openai_compat.OpenAICompatibleClient`).
"""

import os
from pathlib import Path

os.environ.setdefault("MPLBACKEND", "Agg")

from hcaptcha_challenger.agent import AgentConfig
from pydantic import Field, SecretStr, model_validator
from pydantic_settings import SettingsConfigDict

from epic_free.llm import apply_llm_patch

# --- core paths (resolved relative to the project root, two levels up) ---
PROJECT_ROOT = Path(__file__).resolve().parents[2]
VOLUMES_DIR = PROJECT_ROOT.joinpath("volumes")
LOG_DIR = VOLUMES_DIR.joinpath("logs")
USER_DATA_DIR = VOLUMES_DIR.joinpath("user_data")
RUNTIME_DIR = VOLUMES_DIR.joinpath("runtime")
SCREENSHOTS_DIR = VOLUMES_DIR.joinpath("screenshots")
RECORD_DIR = VOLUMES_DIR.joinpath("record")
HCAPTCHA_DIR = VOLUMES_DIR.joinpath("hcaptcha")

SUPPORTED_PROVIDERS = ("gemini", "glm", "openai")


def _env(name: str, default: str | None = None) -> str | None:
    value = os.getenv(name)
    if value is None:
        return default
    value = value.strip()
    return value or default


def _coerce_secret(value: object) -> str | None:
    if value is None:
        return None
    if isinstance(value, SecretStr):
        value = value.get_secret_value()
    value = str(value).strip()
    return value or None


def _resolve_provider(provider: object, openai_key: object, glm_key: object) -> str:
    """Return the LLM provider, auto-detecting from the first present key.

    Detection order is ``openai → glm → gemini``. Shared by both the before- and
    after-validators so the fallback logic lives in exactly one place.
    """
    provider = str(provider or "").strip().lower()
    if provider in SUPPORTED_PROVIDERS:
        return provider
    return "openai" if openai_key else ("glm" if glm_key else "gemini")


def _seed_gemini_key(gemini_key, openai_key, glm_key):
    """Return the value to use for ``GEMINI_API_KEY``.

    ``hcaptcha-challenger`` reads ``GEMINI_API_KEY`` off its base model, so a
    non-Gemini deploy still needs it populated. Keeps the existing key when set,
    otherwise falls back to the openai/glm credential (in that order).
    """
    if gemini_key is not None:
        return gemini_key
    if openai_key is not None:
        return openai_key
    return glm_key


class EpicSettings(AgentConfig):
    model_config = SettingsConfigDict(env_file=".env", env_ignore_empty=True, extra="ignore")

    # ------------------------------------------------------------------ LLM provider
    LLM_PROVIDER: str = Field(default="", description="Supported values: gemini, glm, openai")

    # --- Gemini (native GenAI SDK) ---
    GEMINI_API_KEY: SecretStr | None = Field(default=None, description="Gemini / AiHubMix API key")
    GEMINI_BASE_URL: str = Field(default="", description="Optional Gemini-compatible base URL")
    GEMINI_MODEL: str = Field(default="gemini-2.5-pro", description="Gemini default model")

    # --- GLM (OpenAI-compatible) ---
    GLM_API_KEY: SecretStr | None = Field(default=None, description="GLM API key")
    GLM_BASE_URL: str = Field(
        default="https://open.bigmodel.cn/api/paas/v4",
        description="GLM OpenAI-compatible base URL",
    )
    GLM_MODEL: str = Field(default="glm-4.5v", description="GLM vision-capable default model")

    # --- OpenAI (OpenAI-compatible) ---
    OPENAI_API_KEY: SecretStr | None = Field(default=None, description="OpenAI API key")
    OPENAI_BASE_URL: str = Field(
        default="https://api.openai.com/v1", description="OpenAI API base URL"
    )
    OPENAI_MODEL: str = Field(default="gpt-4.1-mini", description="OpenAI vision-capable model")

    # ------------------------------------------------------------------ browser
    BROWSER_BACKEND: str = Field(
        default="auto", description="Supported values: auto, camoufox, playwright"
    )
    # Default to empty (not a None-returning factory) so a missing credential does
    # not raise a cryptic pydantic error at import; deploy() reports it clearly via
    # ``epic_configuration_error`` instead.
    EPIC_EMAIL: str = Field(default="")
    EPIC_PASSWORD: SecretStr = Field(default=SecretStr(""))
    DISABLE_BEZIER_TRAJECTORY: bool = Field(default=False)
    WAIT_FOR_CHALLENGE_VIEW_TO_RENDER_MS: int = Field(default=3000)

    # ------------------------------------------------------------------ per-task models
    CHALLENGE_CLASSIFIER_MODEL: str = Field(default="")
    IMAGE_CLASSIFIER_MODEL: str = Field(default="")
    SPATIAL_POINT_REASONER_MODEL: str = Field(default="")
    SPATIAL_PATH_REASONER_MODEL: str = Field(default="")

    # ------------------------------------------------------------------ hcaptcha-challenger storage roots
    cache_dir: Path = HCAPTCHA_DIR.joinpath(".cache")
    challenge_dir: Path = HCAPTCHA_DIR.joinpath(".challenge")
    captcha_response_dir: Path = HCAPTCHA_DIR.joinpath(".captcha")

    # ------------------------------------------------------------------ scheduling / runtime
    ENABLE_APSCHEDULER: bool = Field(default=True)
    TASK_TIMEOUT_SECONDS: int = Field(
        default=900,
        description=(
            "Hard cap (seconds) for a single collection run; a hung browser is "
            "aborted so the scheduler can retry on its next tick. 0 disables the cap."
        ),
    )

    # ------------------------------------------------------------------ disk-artifact retention
    # On each run, delete files older than N days under the volume dirs below.
    # 0 = keep forever. Bounds unbounded growth on always-on deployments.
    RECORD_RETENTION_DAYS: int = Field(
        default=30, description="Prune browser video recordings (volumes/record) older than N days"
    )
    RUNTIME_RETENTION_DAYS: int = Field(
        default=30,
        description="Prune debug screenshots + hcaptcha caches older than N days",
    )

    # ------------------------------------------------------------------ validators
    @model_validator(mode="before")
    @classmethod
    def _bridge_provider_credentials(cls, raw_data):
        data = dict(raw_data) if isinstance(raw_data, dict) else {}

        glm_key = _coerce_secret(data.get("GLM_API_KEY"))
        openai_key = _coerce_secret(data.get("OPENAI_API_KEY"))
        gemini_key = _coerce_secret(data.get("GEMINI_API_KEY"))

        data["LLM_PROVIDER"] = _resolve_provider(data.get("LLM_PROVIDER"), openai_key, glm_key)

        # hcaptcha-challenger still reads GEMINI_API_KEY from its base model, so
        # seed it before field validation for non-Gemini environments.
        if gemini_key is None:
            seeded = _seed_gemini_key(gemini_key, openai_key, glm_key)
            if seeded is not None:
                data["GEMINI_API_KEY"] = seeded

        return data

    @model_validator(mode="after")
    def _apply_runtime_defaults(self):
        for field_name in (
            "GEMINI_BASE_URL",
            "GEMINI_MODEL",
            "LLM_PROVIDER",
            "GLM_BASE_URL",
            "GLM_MODEL",
            "OPENAI_BASE_URL",
            "OPENAI_MODEL",
            "BROWSER_BACKEND",
            "EPIC_EMAIL",
            "CHALLENGE_CLASSIFIER_MODEL",
            "IMAGE_CLASSIFIER_MODEL",
            "SPATIAL_POINT_REASONER_MODEL",
            "SPATIAL_PATH_REASONER_MODEL",
        ):
            value = getattr(self, field_name, None)
            if isinstance(value, str):
                setattr(self, field_name, value.strip())

        provider = _resolve_provider(self.LLM_PROVIDER, self.OPENAI_API_KEY, self.GLM_API_KEY)
        self.LLM_PROVIDER = provider

        self.GEMINI_API_KEY = _seed_gemini_key(
            self.GEMINI_API_KEY, self.OPENAI_API_KEY, self.GLM_API_KEY
        )

        provider_default = {
            "openai": self.OPENAI_MODEL,
            "glm": self.GLM_MODEL,
            "gemini": self.GEMINI_MODEL,
        }[provider]
        for attr in (
            "CHALLENGE_CLASSIFIER_MODEL",
            "IMAGE_CLASSIFIER_MODEL",
            "SPATIAL_POINT_REASONER_MODEL",
            "SPATIAL_PATH_REASONER_MODEL",
        ):
            if not getattr(self, attr):
                setattr(self, attr, provider_default)

        self.BROWSER_BACKEND = (self.BROWSER_BACKEND or "").strip().lower() or "auto"
        return self

    # ------------------------------------------------------------------ helpers
    @property
    def user_data_dir(self) -> Path:
        return self.user_data_dir_for("camoufox")

    def user_data_dir_for(self, backend: str) -> Path:
        backend = (backend or "camoufox").strip().lower()
        suffix = f".{backend}"
        target = USER_DATA_DIR.joinpath(f"{self.EPIC_EMAIL}{suffix}")
        target.mkdir(parents=True, exist_ok=True)
        return target

    @property
    def llm_configuration_error(self) -> str | None:
        provider = (self.LLM_PROVIDER or "").strip().lower()
        if provider == "openai" and self.OPENAI_API_KEY is None:
            return (
                "Invalid LLM configuration: LLM_PROVIDER=openai but OPENAI_API_KEY is empty. "
                "Set OPENAI_API_KEY, or switch LLM_PROVIDER to glm/gemini."
            )
        if provider == "glm" and self.GLM_API_KEY is None:
            return (
                "Invalid LLM configuration: LLM_PROVIDER=glm but GLM_API_KEY is empty. "
                "Set GLM_API_KEY, or switch LLM_PROVIDER to openai/gemini."
            )
        if provider == "gemini" and self.GEMINI_API_KEY is None:
            return (
                "Invalid LLM configuration: LLM_PROVIDER=gemini but GEMINI_API_KEY is empty. "
                "Set GEMINI_API_KEY, or switch LLM_PROVIDER to openai/glm."
            )
        return None

    @property
    def epic_configuration_error(self) -> str | None:
        if not (self.EPIC_EMAIL or "").strip():
            return (
                "Invalid Epic configuration: EPIC_EMAIL is empty. "
                "Set EPIC_EMAIL and EPIC_PASSWORD (the Epic account to claim with)."
            )
        if not self.EPIC_PASSWORD.get_secret_value().strip():
            return "Invalid Epic configuration: EPIC_PASSWORD is empty. Set EPIC_PASSWORD."
        return None


settings = EpicSettings()
settings.ignore_request_questions = ["Please drag the crossing to complete the lines"]
apply_llm_patch(settings)
