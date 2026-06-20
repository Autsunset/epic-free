# -*- coding: utf-8 -*-
"""Tests for the promotions fetcher's payload parsing and URL construction.

The shared HTTP client is replaced with a fake so the tests run without network;
``RUNTIME_DIR`` is redirected to a tmp dir so the debug cache write stays clean.
"""

import pytest

import epic_free.config as config
import epic_free.http_client as http_client
from epic_free.epic.promotions import get_promotions


class _FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def json(self):
        return self._payload


class _FakeClient:
    def __init__(self, payload):
        self._payload = payload
        self.calls = []

    async def get(self, url, params=None):
        self.calls.append((url, params))
        return _FakeResponse(self._payload)


@pytest.fixture
def patch_client(monkeypatch):
    def _install(payload) -> _FakeClient:
        client = _FakeClient(payload)

        async def _get_client():
            return client

        monkeypatch.setattr(http_client, "get_async_client", _get_client)
        return client

    return _install


@pytest.fixture(autouse=True)
def _patch_runtime(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "RUNTIME_DIR", tmp_path / "runtime")


def _free_element(**overrides):
    """A single free (100%-off) storefront element."""
    element = {
        "title": "Cool Game",
        "id": "abc",
        "namespace": "ns",
        "description": "desc",
        "offerType": "OTHERS",
        "promotions": {
            "promotionalOffers": [
                {"promotionalOffers": [{"discountSetting": {"discountPercentage": 0}}]}
            ]
        },
        "productSlug": "cool-game",
    }
    element.update(overrides)
    return element


def _payload(elements):
    return {"data": {"Catalog": {"searchStore": {"elements": elements}}}}


async def test_single_product_url(patch_client):
    patch_client(_payload([_free_element()]))
    games = await get_promotions()
    assert len(games) == 1
    assert games[0].url == "https://store.epicgames.com/en-US/p/cool-game"


async def test_bundle_url_via_offer_type(patch_client):
    element = _free_element(offerType="BUNDLE", offerMappings=[{"pageSlug": "mega-bundle"}])
    patch_client(_payload([element]))
    games = await get_promotions()
    assert games[0].url == "https://store.epicgames.com/en-US/bundles/mega-bundle"


async def test_urlslug_fallback_when_no_other_slug(patch_client):
    element = _free_element()
    del element["productSlug"]
    element["urlSlug"] = "fallback-slug"
    patch_client(_payload([element]))
    games = await get_promotions()
    assert games[0].url.endswith("/fallback-slug")


async def test_non_free_game_is_skipped(patch_client):
    element = _free_element()
    offer = element["promotions"]["promotionalOffers"][0]["promotionalOffers"][0]
    offer["discountSetting"]["discountPercentage"] = 20
    patch_client(_payload([element]))
    assert await get_promotions() == []


async def test_unexpected_payload_shape_returns_empty(patch_client):
    patch_client({"data": {"unexpected": True}})
    assert await get_promotions() == []


async def test_locale_param_is_sent(patch_client):
    client = patch_client(_payload([]))
    await get_promotions()
    assert client.calls[0][1] == {"locale": "zh-CN"}


async def test_fetch_failure_returns_empty(monkeypatch):
    async def _boom():
        raise RuntimeError("network down")

    monkeypatch.setattr(http_client, "get_async_client", _boom)
    assert await get_promotions() == []
