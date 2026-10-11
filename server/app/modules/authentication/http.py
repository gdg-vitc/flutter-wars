"""The two outbound HTTP calls Module B makes to Google, behind one swappable client.

httpx by default. A runtime without sockets (a Cloudflare Python Worker) installs
its own fetch-based functions with `install(...)`.
"""

from collections.abc import Awaitable, Callable
from typing import Any


class HttpError(Exception):
    """Transport failure or non-2xx response; never carries provider error text."""


async def _httpx_get_json(url: str) -> Any:
    import httpx  # imported lazily: a Worker bundle installs its own client instead

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            response = await client.get(url)
            response.raise_for_status()
            return response.json()
    except (httpx.HTTPError, ValueError) as exc:
        raise HttpError(type(exc).__name__) from None


async def _httpx_post_form(url: str, data: dict[str, str]) -> Any:
    import httpx

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            response = await client.post(url, data=data)
            response.raise_for_status()
            return response.json()
    except (httpx.HTTPError, ValueError) as exc:
        raise HttpError(type(exc).__name__) from None


get_json: Callable[[str], Awaitable[Any]] = _httpx_get_json
post_form: Callable[[str, dict[str, str]], Awaitable[Any]] = _httpx_post_form


def install(
    *,
    get: Callable[[str], Awaitable[Any]],
    post: Callable[[str, dict[str, str]], Awaitable[Any]],
) -> None:
    global get_json, post_form
    get_json, post_form = get, post
