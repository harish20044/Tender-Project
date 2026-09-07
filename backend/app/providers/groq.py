"""Groq chat completions.

Groq exposes an OpenAI-compatible endpoint, so this is a thin HTTP client
rather than another SDK dependency. Three behaviours are worth noting.

Calls are throttled client-side before they are sent, because a rejected call
still costs the round trip and, mid-ingest, the retry latency compounds across
hundreds of chunks.

A 429 is retried with the server's own ``retry-after`` when it supplies one.
Guessing a backoff when the server has told you the answer wastes time.

Structured output is requested by JSON schema where the caller supplies one.
Extraction feeds a deterministic scoring engine, so a malformed object is not
a formatting nuisance but a re-run and a second charge.
"""

from __future__ import annotations

import json
from typing import Any

import httpx
from tenacity import (
    AsyncRetrying,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from app.core.config import get_settings
from app.core.logging import get_logger
from app.providers.base import (
    ChatMessage,
    Completion,
    ProviderError,
    ProviderRateLimited,
)
from app.providers.throttle import SlidingWindowLimiter, estimate_tokens

logger = get_logger(__name__)

GROQ_BASE_URL = "https://api.groq.com/openai/v1"


class GroqChatProvider:
    """Chat completions against Groq, rate-limited and retried."""

    def __init__(
        self,
        api_key: str | None = None,
        *,
        client: httpx.AsyncClient | None = None,
        limiter: SlidingWindowLimiter | None = None,
    ) -> None:
        settings = get_settings()

        self._api_key = api_key if api_key is not None else settings.groq_api_key
        if not self._api_key:
            raise ProviderError(
                "GROQ_API_KEY is not set. Generation cannot run without it."
            )

        self._default_model = settings.groq_model_primary
        self._fast_model = settings.groq_model_fast
        self._default_reasoning_effort = settings.groq_reasoning_effort
        self._max_retries = settings.groq_max_retries

        self._client = client or httpx.AsyncClient(
            base_url=GROQ_BASE_URL,
            timeout=httpx.Timeout(settings.groq_timeout_seconds),
            headers={
                "Authorization": f"Bearer {self._api_key}",
                "Content-Type": "application/json",
            },
        )
        self._limiter = limiter or SlidingWindowLimiter(
            max_requests_per_minute=settings.groq_max_rpm,
            max_tokens_per_minute=settings.groq_max_tpm,
        )

    @property
    def fast_model(self) -> str:
        """Cheaper model for classification and routing, where a large model
        buys nothing."""
        return self._fast_model

    async def complete(
        self,
        messages: list[ChatMessage],
        *,
        model: str | None = None,
        temperature: float = 0.0,
        max_tokens: int | None = None,
        json_schema: dict[str, Any] | None = None,
        reasoning_effort: str | None = None,
    ) -> Completion:
        chosen_model = model or self._default_model

        payload: dict[str, Any] = {
            "model": chosen_model,
            "messages": [{"role": m.role, "content": m.content} for m in messages],
            # Extraction must be reproducible. Callers that want variation ask
            # for it explicitly.
            "temperature": temperature,
            "reasoning_effort": reasoning_effort or self._default_reasoning_effort,
        }
        if max_tokens is not None:
            payload["max_tokens"] = max_tokens
        if json_schema is not None:
            payload["response_format"] = {
                "type": "json_schema",
                "json_schema": {"name": "extraction", "schema": json_schema, "strict": True},
            }

        prompt_estimate = sum(estimate_tokens(m.content) for m in messages)
        # Reserve the output allowance too, since it counts against the same
        # ceiling and is charged whether or not it is used.
        #
        # On a reasoning model, max_tokens caps reasoning and answer together,
        # and reasoning is emitted first. Set it too low and the call returns
        # an empty string having spent the whole budget thinking, so callers
        # must leave real headroom above the size of the answer they expect.
        reservation = prompt_estimate + (max_tokens or 1024)

        await self._limiter.acquire(reservation)

        completion = await self._post_with_retries(payload, chosen_model)

        await self._limiter.settle(reservation, completion.total_tokens)
        return completion

    async def _post_with_retries(self, payload: dict[str, Any], model: str) -> Completion:
        async for attempt in AsyncRetrying(
            stop=stop_after_attempt(self._max_retries),
            wait=wait_exponential(multiplier=1, min=1, max=30),
            retry=retry_if_exception_type((ProviderRateLimited, httpx.TransportError)),
            reraise=True,
        ):
            with attempt:
                response = await self._client.post("/chat/completions", json=payload)

                if response.status_code == 429:
                    retry_after = _parse_retry_after(response)
                    logger.warning(
                        "groq_rate_limited", model=model, retry_after_seconds=retry_after
                    )
                    raise ProviderRateLimited(
                        "Groq rate limit reached", retry_after_seconds=retry_after
                    )

                if response.status_code >= 500:
                    # Transient upstream failure; worth another attempt.
                    raise httpx.TransportError(
                        f"Groq returned {response.status_code}"
                    )

                if response.status_code >= 400:
                    raise ProviderError(
                        f"Groq rejected the request ({response.status_code}): {response.text}"
                    )

                return _parse_completion(response.json(), model)

        raise ProviderError("Groq call exhausted its retries")

    async def aclose(self) -> None:
        await self._client.aclose()


def _parse_retry_after(response: httpx.Response) -> float | None:
    raw = response.headers.get("retry-after")
    if raw is None:
        return None
    try:
        return float(raw)
    except ValueError:
        return None


def _parse_completion(body: dict[str, Any], model: str) -> Completion:
    try:
        text = body["choices"][0]["message"]["content"] or ""
    except (KeyError, IndexError) as exc:
        raise ProviderError(f"Unexpected Groq response shape: {json.dumps(body)[:400]}") from exc

    usage = body.get("usage") or {}
    return Completion(
        text=text,
        model=body.get("model", model),
        prompt_tokens=int(usage.get("prompt_tokens", 0)),
        completion_tokens=int(usage.get("completion_tokens", 0)),
    )
