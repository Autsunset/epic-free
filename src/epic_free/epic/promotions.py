# -*- coding: utf-8 -*-
"""Fetch weekly free-game promotions from the Epic storefront backend.

Uses the shared pooled HTTP client so the fetch no longer blocks the event loop
on a synchronous ``httpx.get`` (the previous implementation ran a blocking call
inside an async context).
"""
from __future__ import annotations

import json
from contextlib import suppress
from typing import List

from loguru import logger

from epic_free.models import PromotionGame

URL_PROMOTIONS = "https://store-site-backend-static.ak.epicgames.com/freeGamesPromotions"
URL_PRODUCT_PAGE = "https://store.epicgames.com/en-US/p/"
URL_PRODUCT_BUNDLES = "https://store.epicgames.com/en-US/bundles/"


def _is_discount_game(prot: dict) -> bool | None:
    with suppress(KeyError, IndexError, TypeError):
        offers = prot["promotions"]["promotionalOffers"][0]["promotionalOffers"]
        return any(offer["discountSetting"]["discountPercentage"] == 0 for offer in offers)
    return None


async def get_promotions() -> List[PromotionGame]:
    """Return this week's free (100%-off) games, or an empty list on failure."""
    promotions: List[PromotionGame] = []

    try:
        # Imported lazily to avoid a config <-> promotions import cycle at module load.
        from epic_free.config import RUNTIME_DIR
        from epic_free.http_client import get_async_client

        client = await get_async_client()
        resp = await client.get(URL_PROMOTIONS, params={"local": "zh-CN"})
        data = resp.json()
    except Exception as err:
        logger.error("Failed to get promotions | err={!r}", err)
        return []

    with suppress(Exception):
        cache_key = RUNTIME_DIR.joinpath("promotions.json")
        cache_key.parent.mkdir(parents=True, exist_ok=True)
        cache_key.write_text(json.dumps(data, indent=2, ensure_ascii=False))

    try:
        elements = data["data"]["Catalog"]["searchStore"]["elements"]
    except (KeyError, TypeError):
        logger.error("Unexpected Epic promotions payload shape")
        return []

    for e in elements:
        if not _is_discount_game(e):
            continue

        # --- bundle vs single-product URL detection ---
        is_bundle = e.get("offerType") == "BUNDLE"
        if not is_bundle:
            for cat in e.get("categories", []):
                if "bundle" in cat.get("path", "").lower():
                    is_bundle = True
                    break
        if not is_bundle and "Collection" in e.get("title", ""):
            is_bundle = True

        base_url = URL_PRODUCT_BUNDLES if is_bundle else URL_PRODUCT_PAGE

        try:
            if e.get("offerMappings"):
                slug = e["offerMappings"][0]["pageSlug"]
                e["url"] = f"{base_url.rstrip('/')}/{slug}"
            elif e.get("productSlug"):
                e["url"] = f"{base_url.rstrip('/')}/{e['productSlug']}"
            else:
                e["url"] = f"{base_url.rstrip('/')}/{e.get('urlSlug', 'unknown')}"
        except (KeyError, IndexError):
            logger.info(f"Failed to get URL: {e}")
            continue

        logger.info(e["url"])
        promotions.append(PromotionGame(**e))

    return promotions
