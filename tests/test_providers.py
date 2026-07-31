# -*- coding: utf-8 -*-
"""Tests for the LLM compatibility layer.

These cover:
* The response-normalization helpers shared by all OpenAI-compatible providers.
* The Chat Completions payload shape and image encoding (OpenAI data-URL vs GLM
  raw-base64, auto-detected from the model name).
* The Responses API payload shape (``input`` / ``input_text`` / ``input_image`` /
  ``text.format``).
* The Anthropic Messages API payload shape (top-level ``system``, ``image``
  content with ``source.type=base64``, ``max_tokens``).
* The reasoning-tag stripper and upload-store cap.
"""

import json

import pytest
from hcaptcha_challenger.models import (
    ChallengeRouterResult,
    ImageAreaSelectChallenge,
    ImageDragDropChallenge,
)
from pydantic import BaseModel, SecretStr

from epic_free.llm.anthropic import _AnthropicModels
from epic_free.llm.base import _MAX_STORED_UPLOADS, _AsyncFiles, _strip_reasoning
from epic_free.llm.openai_compat import _AsyncModels
from epic_free.llm.openai_responses import _ResponsesModels
from epic_free.llm.parse import (
    _coerce_payload_for_schema,
    _extract_json_payload,
    _normalize_glm_payload,
)


# ---------------------------------------------------------------------------
# Shared response normalization (exercises the parsing path for all providers)
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
# Test fixtures: minimal settings stubs
# ---------------------------------------------------------------------------
class _FakeSettings:
    """Minimal settings stub so we can build model handlers without hcaptcha config."""

    OPENAI_API_KEY = SecretStr("sk-test")
    OPENAI_BASE_URL = "https://api.openai.com/v1"
    ANTHROPIC_API_KEY = SecretStr("sk-ant-test")
    ANTHROPIC_BASE_URL = "https://api.anthropic.com"


def _chat_models() -> _AsyncModels:
    return _AsyncModels(
        _FakeSettings(),
        {},
        provider_name="OpenAI",
        api_key_attr="OPENAI_API_KEY",
        base_url_attr="OPENAI_BASE_URL",
    )


def _responses_models() -> _ResponsesModels:
    return _ResponsesModels(
        _FakeSettings(),
        {},
        provider_name="OpenAI",
        api_key_attr="OPENAI_API_KEY",
        base_url_attr="OPENAI_BASE_URL",
    )


def _anthropic_models() -> _AnthropicModels:
    return _AnthropicModels(
        _FakeSettings(),
        {},
        provider_name="Anthropic",
        api_key_attr="ANTHROPIC_API_KEY",
        base_url_attr="ANTHROPIC_BASE_URL",
    )


def _text_content(prompt: str):
    class _Part:
        text = prompt

    class _Content:
        role = "user"
        parts = [_Part()]

    return _Content()


# ---------------------------------------------------------------------------
# Chat Completions: image encoding (GLM raw-base64 auto-detected from model name)
# ---------------------------------------------------------------------------
def test_openai_image_part_is_a_data_url():
    models = _chat_models()
    part = models._image_item(b"\x89PNG\r\n\x1a\n", "image/png")
    assert part["type"] == "image_url"
    assert part["image_url"]["url"].startswith("data:image/png;base64,")


def test_glm_image_part_uses_raw_base64():
    models = _chat_models()
    models._current_model = "glm-4.5v"
    part = models._image_item(b"\x89PNG\r\n\x1a\n", "image/png")
    assert part["type"] == "image_url"
    # GLM accepts the compact raw-base64 form (no data: prefix).
    assert not part["image_url"]["url"].startswith("data:")


# ---------------------------------------------------------------------------
# Chat Completions: payload shape
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("model", ["gpt-4.1-mini", "glm-4.5v"])
def test_chat_completions_payload_shape(model):
    models = _chat_models()

    class _Config:
        system_instruction = "You solve hCaptcha."

    payload = models._build_payload(
        model=model, contents=[_text_content("find the cat")], config=_Config(), kwargs={}
    )
    assert payload["model"] == model
    # system message comes first, then the user message
    assert payload["messages"][0]["role"] == "system"
    assert payload["messages"][1]["role"] == "user"


def test_glm_thinking_mode_is_emitted_for_glm_45_models():
    models = _chat_models()

    class _Config:
        thinking_config = {"include_thoughts": True}

    payload = models._build_payload(
        model="glm-4.5v", contents=[_text_content("solve")], config=_Config(), kwargs={}
    )
    assert payload["thinking"] == {"type": "enabled"}


def test_glm_thinking_mode_is_not_emitted_for_non_glm_models():
    models = _chat_models()

    class _Config:
        thinking_config = {"include_thoughts": True}

    payload = models._build_payload(
        model="gpt-4.1-mini", contents=[_text_content("solve")], config=_Config(), kwargs={}
    )
    assert "thinking" not in payload


# ---------------------------------------------------------------------------
# Responses API: payload shape
# ---------------------------------------------------------------------------
def test_responses_payload_uses_input_not_messages():
    models = _responses_models()

    class _Config:
        system_instruction = "You solve hCaptcha."

    payload = models._build_payload(
        model="gpt-4.1-mini", contents=[_text_content("find the cat")], config=_Config(), kwargs={}
    )
    assert "messages" not in payload
    assert "input" in payload
    # developer role for system, then user
    assert payload["input"][0]["role"] == "developer"
    assert payload["input"][1]["role"] == "user"


def test_responses_uses_input_text_and_input_image_content_types():
    models = _responses_models()

    class _InlineData:
        data = b"\x89PNG\r\n\x1a\n"
        mime_type = "image/png"

    class _ImagePart:
        inline_data = _InlineData()

    class _Content:
        role = "user"
        parts = [_ImagePart()]

    class _Config:
        pass

    payload = models._build_payload(
        model="gpt-4.1-mini", contents=[_Content()], config=_Config(), kwargs={}
    )
    # Find the user message (a system/developer message is prepended for the
    # coordinate hint when images are present).
    user_msg = next(item for item in payload["input"] if item["role"] == "user")
    user_content = user_msg["content"]
    assert user_content[0]["type"] == "input_image"
    assert user_content[0]["image_url"].startswith("data:image/png;base64,")


def test_responses_json_mode_uses_text_format():
    models = _responses_models()

    class _Schema(BaseModel):
        checkout_open: bool

    class _Config:
        response_schema = _Schema

    payload = models._build_payload(
        model="gpt-4.1-mini", contents=[_text_content("describe")], config=_Config(), kwargs={}
    )
    assert payload["text"] == {"format": {"type": "json_object"}}
    # should NOT have the chat-completions response_format key
    assert "response_format" not in payload


def test_responses_image_url_is_a_string_not_object():
    models = _responses_models()
    part = models._image_item(b"\x89PNG\r\n\x1a\n", "image/png")
    assert part["type"] == "input_image"
    # Responses API: image_url is a string, not {"url": "..."}
    assert isinstance(part["image_url"], str)
    assert part["image_url"].startswith("data:image/png;base64,")


# ---------------------------------------------------------------------------
# Responses API: response text extraction
# ---------------------------------------------------------------------------
def test_responses_extract_text_from_output_text_field():
    models = _responses_models()
    data = {"output_text": '{"answer": "yes"}'}
    assert models._extract_text(data) == '{"answer": "yes"}'


def test_responses_extract_text_fallback_to_output_array():
    models = _responses_models()
    data = {
        "output": [
            {
                "type": "message",
                "role": "assistant",
                "content": [{"type": "output_text", "text": '{"answer": 42}'}],
            }
        ]
    }
    assert models._extract_text(data) == '{"answer": 42}'


# ---------------------------------------------------------------------------
# Anthropic Messages API: payload shape
# ---------------------------------------------------------------------------
def test_anthropic_payload_has_system_and_max_tokens():
    models = _anthropic_models()

    class _Config:
        system_instruction = "You solve hCaptcha."

    payload = models._build_payload(
        model="claude-sonnet-5",
        contents=[_text_content("find the cat")],
        config=_Config(),
        kwargs={},
    )
    assert payload["model"] == "claude-sonnet-5"
    assert payload["system"] == "You solve hCaptcha."
    assert payload["max_tokens"] == 4096
    # system is NOT in messages (it's a top-level field)
    assert all(msg["role"] != "system" for msg in payload["messages"])
    assert payload["messages"][0]["role"] == "user"


def test_anthropic_uses_text_and_image_content_types():
    models = _anthropic_models()

    class _InlineData:
        data = b"\x89PNG\r\n\x1a\n"
        mime_type = "image/png"

    class _ImagePart:
        inline_data = _InlineData()

    class _Content:
        role = "user"
        parts = [_ImagePart()]

    class _Config:
        pass

    payload = models._build_payload(
        model="claude-sonnet-5", contents=[_Content()], config=_Config(), kwargs={}
    )
    image_item = payload["messages"][0]["content"][0]
    assert image_item["type"] == "image"
    assert image_item["source"]["type"] == "base64"
    assert image_item["source"]["media_type"] == "image/png"
    assert isinstance(image_item["source"]["data"], str)


def test_anthropic_has_no_response_format():
    models = _anthropic_models()

    class _Schema(BaseModel):
        checkout_open: bool

    class _Config:
        response_schema = _Schema

    payload = models._build_payload(
        model="claude-sonnet-5", contents=[_text_content("describe")], config=_Config(), kwargs={}
    )
    # Anthropic has no JSON-mode parameter — schema is injected via prompt only.
    assert "response_format" not in payload
    assert "text" not in payload
    # but the schema text should be in the system field
    assert "checkout_open" in payload["system"]


def test_anthropic_headers_use_api_key_not_bearer():
    models = _anthropic_models()
    headers = models._get_headers()
    assert headers["x-api-key"] == "sk-ant-test"
    assert headers["anthropic-version"] == "2023-06-01"
    assert "Authorization" not in headers


def test_anthropic_endpoint_appends_v1_messages():
    models = _anthropic_models()
    assert models._get_endpoint() == "https://api.anthropic.com/v1/messages"


# ---------------------------------------------------------------------------
# Anthropic: response text extraction
# ---------------------------------------------------------------------------
def test_anthropic_extract_text_from_content_blocks():
    models = _anthropic_models()
    data = {"content": [{"type": "text", "text": '{"answer": "yes"}'}]}
    assert models._extract_text(data) == '{"answer": "yes"}'


def test_anthropic_extract_text_skips_thinking_blocks():
    models = _anthropic_models()
    data = {
        "content": [
            {"type": "thinking", "thinking": "let me think..."},
            {"type": "text", "text": '{"answer": 42}'},
        ]
    }
    assert models._extract_text(data) == '{"answer": 42}'


# ---------------------------------------------------------------------------
# Reasoning models (kimi-k2.5, deepseek-r1, glm-4.5-thinking, …) wrap a
# chain-of-thought in <think>…</think>; it must be stripped before JSON parsing.
# ---------------------------------------------------------------------------
def test_strip_reasoning_removes_closed_think_block():
    text = '<think>The grid shows two cats, top-left and center.</think>{"answer": [[1, 2, 3, 4]]}'
    assert _strip_reasoning(text) == '{"answer": [[1, 2, 3, 4]]}'


def test_strip_reasoning_leaves_plain_json_untouched():
    text = '{"answer": "image_label_single_select"}'
    assert _strip_reasoning(text) == text


def test_strip_reasoning_handles_alternate_tag_names_and_spacing():
    assert _strip_reasoning("<thinking>z</thinking>{}") == "{}"
    assert _strip_reasoning("<reasoning>x</reasoning>{}") == "{}"
    assert _strip_reasoning("<analysis>y</analysis>{}") == "{}"
    # tolerant of whitespace / attributes in the tag and a trailing newline
    assert _strip_reasoning("< think >\nlong cot\n</ think >\n{}\n") == "{}"


def test_strip_reasoning_drops_unclosed_think_tail():
    # A truncated / never-closed reasoning block: everything from the open tag on
    # is chain-of-thought and must go, leaving only what preceded it.
    text = '{"answer": "yes"}\n<think>wait, on reflection I should reconsider'
    assert _strip_reasoning(text) == '{"answer": "yes"}'


# ---------------------------------------------------------------------------
# response_schema must be spelled out in the prompt (none of the OpenAI/Anthropic
# wire formats have a universally honoured schema slot), or the model invents its
# own field names and downstream validation fails.
# ---------------------------------------------------------------------------
class _Desc(BaseModel):
    checkout_open: bool
    captcha_visible: bool
    summary: str


def test_chat_response_schema_field_names_injected_into_prompt():
    models = _chat_models()

    class _Config:
        system_instruction = "You solve hCaptcha."
        response_schema = _Desc

    payload = models._build_payload(
        model="kimi-k2.5",
        contents=[_text_content("describe the page")],
        config=_Config(),
        kwargs={},
    )
    system_message = payload["messages"][0]
    assert system_message["role"] == "system"
    # the exact schema field names must reach the model
    for field in ("checkout_open", "captcha_visible", "summary"):
        assert field in system_message["content"]
    # and a JSON object is requested back
    assert payload["response_format"] == {"type": "json_object"}


def test_chat_no_response_schema_means_no_schema_prompt_or_response_format():
    models = _chat_models()

    class _Config:
        system_instruction = "You solve hCaptcha."

    payload = models._build_payload(
        model="gpt-4.1-mini", contents=[_text_content("find the cat")], config=_Config(), kwargs={}
    )
    assert "JSON schema" not in payload["messages"][0]["content"]
    assert "response_format" not in payload


# ---------------------------------------------------------------------------
# The in-process upload store must stay bounded (a long-lived scheduler reuses
# the client across many captcha solves).
# ---------------------------------------------------------------------------
async def test_uploaded_files_storage_is_capped():
    storage: dict = {}
    files = _AsyncFiles(storage, "test-local")
    for i in range(_MAX_STORED_UPLOADS + 10):
        await files.upload(f"img-{i}".encode())

    assert len(storage) == _MAX_STORED_UPLOADS
