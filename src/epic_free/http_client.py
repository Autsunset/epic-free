# -*- coding: utf-8 -*-
"""Shared async HTTP client with connection pooling.

A single long-lived ``httpx.AsyncClient`` is reused across the LLM
compatibility layer and the Epic promotions fetch. The previous implementation
opened a *fresh* client (new TCP+TLS handshake) for every captcha-solve call;
reusing one client with keep-alive connections is the main I/O performance win
here.
"""
from __future__ import annotations

import httpx

# Module-level singleton, lazily created on first use inside an event loop.
_client: httpx.AsyncClient | None = None


async def get_async_client() -> httpx.AsyncClient:
    """Return the shared async client, creating it on first call."""
    global _client
    if _client is None or _client.is_closed:
        _client = httpx.AsyncClient(
            timeout=httpx.Timeout(120.0, connect=30.0),
            limits=httpx.Limits(max_connections=30, max_keepalive_connections=10),
        )
    return _client


async def close_async_client() -> None:
    """Close the shared client. Safe to call from shutdown handlers."""
    global _client
    if _client is not None and not _client.is_closed:
        await _client.aclose()
    _client = None
