"""Minimal async Cliniko REST client.

Handles exactly what this agent needs: shard-aware auth, a shared rate-limit
budget (Cliniko allows 200 req/min per user), simple pagination, and mapping
error responses onto the small set of domain errors the tools care about.
"""
from __future__ import annotations

import asyncio
import base64
import time
from collections import deque
from typing import Any, AsyncIterator

import httpx

from clinic_agent.cliniko.errors import (
    ClinikoBadRequest,
    ClinikoConflict,
    ClinikoNotFound,
    ClinikoRateLimited,
    ClinikoValidationError,
)

RATE_LIMIT_PER_MINUTE = 200
# When we're mid-call and hit a 429, we won't make the caller wait indefinitely.
MAX_RATE_LIMIT_WAIT_SECONDS = 2.0


class _RateLimiter:
    """Sliding-window limiter shared across all requests this process makes."""

    def __init__(self, max_per_minute: int = RATE_LIMIT_PER_MINUTE) -> None:
        self._max_per_minute = max_per_minute
        self._calls: deque[float] = deque()
        self._lock = asyncio.Lock()

    async def acquire(self) -> None:
        async with self._lock:
            now = time.monotonic()
            while self._calls and now - self._calls[0] > 60:
                self._calls.popleft()
            if len(self._calls) >= self._max_per_minute:
                wait = 60 - (now - self._calls[0])
                if wait > 0:
                    await asyncio.sleep(wait)
            self._calls.append(time.monotonic())


def _parse_shard(api_key: str) -> str:
    """Cliniko API keys embed their shard as a trailing `-<shard>`, e.g. a key
    ending in `...-au4` lives at api.au4.cliniko.com."""
    if "-" not in api_key:
        raise ValueError("Cliniko API key doesn't look right: no shard suffix found")
    return api_key.rsplit("-", 1)[-1]


class ClinikoClient:
    def __init__(self, api_key: str, contact_email: str, app_name: str = "ClinicVoiceAgent") -> None:
        shard = _parse_shard(api_key)
        self._base_url = f"https://api.{shard}.cliniko.com/v1"
        token = base64.b64encode(f"{api_key}:".encode()).decode()
        self._headers = {
            "Authorization": f"Basic {token}",
            "Accept": "application/json",
            "Content-Type": "application/json",
            "User-Agent": f"{app_name} ({contact_email})",
        }
        self._limiter = _RateLimiter()
        self._http = httpx.AsyncClient(base_url=self._base_url, headers=self._headers, timeout=10.0)

    async def aclose(self) -> None:
        await self._http.aclose()

    async def _request(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        await self._limiter.acquire()
        response = await self._http.request(method, path, **kwargs)

        if response.status_code == 429:
            reset_at = response.headers.get("X-RateLimit-Reset")
            wait = min(MAX_RATE_LIMIT_WAIT_SECONDS, max(0.0, float(reset_at) - time.time())) if reset_at else MAX_RATE_LIMIT_WAIT_SECONDS
            await asyncio.sleep(wait)
            response = await self._http.request(method, path, **kwargs)
            if response.status_code == 429:
                raise ClinikoRateLimited(f"Still rate limited after waiting {wait:.1f}s for {method} {path}")

        if response.status_code == 404:
            raise ClinikoNotFound(f"{method} {path} -> 404")
        if response.status_code == 409:
            raise ClinikoConflict(f"{method} {path} -> 409: {response.text}")
        if response.status_code == 400:
            raise ClinikoBadRequest(f"{method} {path} -> 400: {response.text}")
        if response.status_code == 422:
            raise ClinikoValidationError(f"{method} {path} -> 422: {response.text}", body=response.text)
        response.raise_for_status()
        return response

    async def get(self, path: str, params: dict | None = None) -> dict:
        return (await self._request("GET", path, params=params)).json()

    async def post(self, path: str, json: dict) -> dict:
        return (await self._request("POST", path, json=json)).json()

    async def patch(self, path: str, json: dict | None = None) -> dict:
        return (await self._request("PATCH", path, json=json or {})).json()

    async def delete(self, path: str) -> None:
        await self._request("DELETE", path)

    async def paginate(self, path: str, key: str, params: dict | None = None) -> AsyncIterator[dict]:
        """Yield individual items from a paginated Cliniko list endpoint,
        following `links.next` until exhausted."""
        params = {"per_page": 100, **(params or {})}
        next_path: str | None = path
        next_params: dict | None = params
        while next_path:
            page = await self.get(next_path, params=next_params)
            for item in page.get(key, []):
                yield item
            next_url = page.get("links", {}).get("next")
            if not next_url:
                break
            # `next` is a fully-qualified URL; strip the base so httpx treats
            # it as relative to our configured base_url.
            next_path = next_url.removeprefix(self._base_url)
            next_params = None
