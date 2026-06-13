# -*- coding: utf-8 -*-
"""Tests for the unified LLM compatibility layer.

These cover the response-normalization helpers shared by the ``glm`` and
``openai`` providers, plus the image-part encoding difference between them
(OpenAI / OpenAI-compatible gateways need a ``data:`` URL; GLM accepts raw
base64).
"""
import json

import pytest
from hcaptcha_challenger.models import (
    ChallengeRouterResult,
    ImageAreaSelectChallenge,
    ImageDragDropChallenge,
)
from pydantic import SecretStr

from epic_free.llm.openai_compat import _AsyncModels
from epic_free.llm.parse import (
    _coerce_payload_for_schema,
    _extract_json_payload,
    _normalize_glm_payload,
)


# ---------------------------------------------------------------------------
# Shared response normalization (exercises glm + openai parsing path)
# ---------------------------------------------------------------------------
def test_area_select_box_answer_is_converted_to_click_points():
    text = '{"answer":[[781,525,889,624],[1031,525,1139,624]]}'
    payload = _coerce_payload_for_schema(
        _normalize_glm_payload(_extract_json_payload(text)), ImageAreaSelectChallenge, text
    )
    challenge = ImageAreaSelectChallenge(**payload)
    assert challenge.points[0].model_dump() == {"x": 835, "y": 574}
    assert challenge.points[1].model_dump() == {"x": 1085, "y": 574}


def test_area_select_dict_boxes_are_converted_to_click_points():
    payload = {
        "answer": [
            {"x_min": 10, "y_min": 20, "x_max": 30, "y_max": 60},
            {"x_min": 101, "y_min": 201, "x_max": 200, "y_max": 300},
        ]
    }
    text = json.dumps(payload)
    coerced = _coerce_payload_for_schema(
        _normalize_glm_payload(payload), ImageAreaSelectChallenge, text
    )
    challenge = ImageAreaSelectChallenge(**coerced)
    assert [point.model_dump() for point in challenge.points] == [
        {"x": 20, "y": 40},
        {"x": 150, "y": 250},
    ]


def test_area_select_coordinates_string_with_single_quotes_is_converted():
    text = (
        '{"Challenge Prompt":"","Coordinates":"['
        "{'x': 889, 'y': 613}, {'x': 996, 'y': 538}, {'x': 817, 'y': 761}"
        ']"}'
    )
    payload = _coerce_payload_for_schema(
        _normalize_glm_payload(_extract_json_payload(text)), ImageAreaSelectChallenge, text
    )
    challenge = ImageAreaSelectChallenge(**payload)
    assert challenge.challenge_prompt == ""
    assert [point.model_dump() for point in challenge.points] == [
        {"x": 889, "y": 613},
        {"x": 996, "y": 538},
        {"x": 817, "y": 761},
    ]


def test_drag_source_coordinates_are_converted_to_paths():
    payload = {
        "source_coordinates": {"x": 765, "y": 545},
        "target_coordinates": {"x": 960, "y": 545},
    }
    text = json.dumps(payload)
    coerced = _coerce_payload_for_schema(
        _normalize_glm_payload(payload), ImageDragDropChallenge, text
    )
    challenge = ImageDragDropChallenge(**coerced)
    assert challenge.challenge_prompt == ""
    assert challenge.paths[0].start_point.model_dump() == {"x": 765, "y": 545}
    assert challenge.paths[0].end_point.model_dump() == {"x": 960, "y": 545}


def test_router_answer_single_select_is_converted_to_challenge_type():
    text = '{"answer":"image_label_single_select"}'
    payload = _coerce_payload_for_schema(
        _normalize_glm_payload(_extract_json_payload(text)), ChallengeRouterResult, text
    )
    challenge = ChallengeRouterResult(**payload)
    assert challenge.challenge_prompt == ""
    assert challenge.challenge_type.value == "image_label_single_select"


def test_router_drag_multi_alias_matches_current_schema_enum():
    text = '{"answer":"image_drag_multi"}'
    payload = _coerce_payload_for_schema(
        _normalize_glm_payload(_extract_json_payload(text)), ChallengeRouterResult, text
    )
    challenge = ChallengeRouterResult(**payload)
    assert challenge.challenge_prompt == ""
    assert challenge.challenge_type.value == "image_drag_multi"


# ---------------------------------------------------------------------------
# Image-part encoding differs by provider (the OpenAI-merge key behavior)
# ---------------------------------------------------------------------------
class _FakeSettings:
    """Minimal settings stub so we can build _AsyncModels without hcaptcha config."""

    OPENAI_API_KEY = SecretStr("sk-test")
    OPENAI_BASE_URL = "https://api.openai.com/v1"
    GLM_API_KEY = SecretStr("glm-test")
    GLM_BASE_URL = "https://open.bigmodel.cn/api/paas/v4"


def _models(provider_name: str, data_url_images: bool) -> _AsyncModels:
    return _AsyncModels(
        _FakeSettings(),
        {},
        provider_name=provider_name,
        api_key_attr="OPENAI_API_KEY" if provider_name == "OpenAI" else "GLM_API_KEY",
        base_url_attr="OPENAI_BASE_URL" if provider_name == "OpenAI" else "GLM_BASE_URL",
        data_url_images=data_url_images,
    )


def test_openai_image_part_is_a_data_url():
    models = _models("OpenAI", data_url_images=True)
    part = models._to_image_part(b"\x89PNG\r\n\x1a\n", "image/png")
    assert part["type"] == "image_url"
    assert part["image_url"]["url"].startswith("data:image/png;base64,")


def test_glm_image_part_uses_raw_base64():
    models = _models("GLM", data_url_images=False)
    part = models._to_image_part(b"\x89PNG\r\n\x1a\n", "image/png")
    assert part["type"] == "image_url"
    # GLM accepts the compact raw-base64 form (no data: prefix).
    assert not part["image_url"]["url"].startswith("data:")


@pytest.mark.parametrize("provider_name,data_url", [("OpenAI", True), ("GLM", False)])
def test_chat_completions_payload_shape(provider_name, data_url):
    models = _models(provider_name, data_url)

    class _Part:
        text = "find the cat"

    class _Content:
        role = "user"
        parts = [_Part()]

    class _Config:
        system_instruction = "You solve hCaptcha."

    payload = models._build_payload(
        model="gpt-4.1-mini", contents=[_Content()], config=_Config(), kwargs={}
    )
    assert payload["model"] == "gpt-4.1-mini"
    # system message comes first, then the user message
    assert payload["messages"][0]["role"] == "system"
    assert payload["messages"][1]["role"] == "user"
