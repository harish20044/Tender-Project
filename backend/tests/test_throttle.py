"""Tests for the client-side rate limiter.

This is the piece most likely to fail quietly. If it lets calls through too
fast the provider rejects them mid-ingest; if it is too conservative every
document takes far longer than it needs to.
"""

import asyncio
import time

import pytest

from app.providers.throttle import SlidingWindowLimiter, estimate_tokens


async def test_calls_within_both_ceilings_do_not_block() -> None:
    limiter = SlidingWindowLimiter(max_requests_per_minute=10, max_tokens_per_minute=10_000)

    started = time.monotonic()
    for _ in range(5):
        await limiter.acquire(100)
    elapsed = time.monotonic() - started

    assert elapsed < 0.1


async def test_request_ceiling_blocks_the_next_call() -> None:
    limiter = SlidingWindowLimiter(max_requests_per_minute=2, max_tokens_per_minute=100_000)

    await limiter.acquire(10)
    await limiter.acquire(10)

    # The third call cannot fit until one of the first two ages out, which is
    # a minute away, so it must still be waiting.
    with pytest.raises(asyncio.TimeoutError):
        await asyncio.wait_for(limiter.acquire(10), timeout=0.3)


async def test_token_ceiling_blocks_independently_of_request_count() -> None:
    # Generous request allowance, tight token allowance: the token ceiling is
    # the one that bites on long tender documents.
    limiter = SlidingWindowLimiter(max_requests_per_minute=1_000, max_tokens_per_minute=1_000)

    await limiter.acquire(900)

    with pytest.raises(asyncio.TimeoutError):
        await asyncio.wait_for(limiter.acquire(500), timeout=0.3)


async def test_settle_corrects_an_underestimate() -> None:
    limiter = SlidingWindowLimiter(max_requests_per_minute=1_000, max_tokens_per_minute=1_000)

    # Reserve conservatively, then discover the call actually cost most of the
    # budget. Without settle, the limiter would keep admitting calls.
    await limiter.acquire(100)
    await limiter.settle(100, 950)

    with pytest.raises(asyncio.TimeoutError):
        await asyncio.wait_for(limiter.acquire(100), timeout=0.3)


async def test_settle_releases_an_overestimate() -> None:
    limiter = SlidingWindowLimiter(max_requests_per_minute=1_000, max_tokens_per_minute=1_000)

    await limiter.acquire(900)
    await limiter.settle(900, 100)

    # The budget freed by the correction must become usable immediately.
    await asyncio.wait_for(limiter.acquire(500), timeout=0.3)


def test_token_estimate_rounds_up_and_never_returns_zero() -> None:
    assert estimate_tokens("") == 1
    assert estimate_tokens("a") == 1
    assert estimate_tokens("a" * 4) == 1
    assert estimate_tokens("a" * 5) == 2
