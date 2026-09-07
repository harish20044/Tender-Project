"""Client-side rate limiting.

Groq enforces both a request-per-minute and a token-per-minute ceiling, and
tender documents are long enough that the token ceiling is the one that bites.
Discovering it by getting a 429 mid-ingest wastes the call and the latency, so
the client holds itself below the limit and waits when it would exceed it.

The window is a sliding sixty seconds rather than a fixed bucket, because a
fixed bucket lets a burst at the boundary spend two windows' budget at once.
"""

from __future__ import annotations

import asyncio
import time
from collections import deque


class SlidingWindowLimiter:
    """Limits both call count and token volume over a sixty-second window."""

    def __init__(self, max_requests_per_minute: int, max_tokens_per_minute: int) -> None:
        self._max_requests = max_requests_per_minute
        self._max_tokens = max_tokens_per_minute
        self._window_seconds = 60.0
        # (timestamp, token_cost) per call, oldest first.
        self._events: deque[tuple[float, int]] = deque()
        self._lock = asyncio.Lock()

    def _evict_expired(self, now: float) -> None:
        cutoff = now - self._window_seconds
        while self._events and self._events[0][0] <= cutoff:
            self._events.popleft()

    def _current_usage(self) -> tuple[int, int]:
        return len(self._events), sum(cost for _, cost in self._events)

    async def acquire(self, estimated_tokens: int) -> None:
        """Block until this call fits inside both ceilings, then record it.

        ``estimated_tokens`` is the caller's forecast. It is reconciled against
        the true figure by :meth:`settle` once the response is in.
        """
        while True:
            async with self._lock:
                now = time.monotonic()
                self._evict_expired(now)
                requests, tokens = self._current_usage()

                fits = (
                    requests + 1 <= self._max_requests
                    and tokens + estimated_tokens <= self._max_tokens
                )
                if fits:
                    self._events.append((now, estimated_tokens))
                    return

                # Wait only until the oldest event ages out, which is the
                # earliest moment the window could have room.
                oldest_at = self._events[0][0] if self._events else now
                sleep_for = max(0.05, oldest_at + self._window_seconds - now)

            await asyncio.sleep(sleep_for)

    async def settle(self, estimated_tokens: int, actual_tokens: int) -> None:
        """Correct the most recent reservation once real usage is known.

        Estimates run low on documents with dense tables, and without this the
        limiter would drift above the true ceiling over a long ingest.
        """
        if actual_tokens == estimated_tokens:
            return

        async with self._lock:
            for index in range(len(self._events) - 1, -1, -1):
                timestamp, cost = self._events[index]
                if cost == estimated_tokens:
                    self._events[index] = (timestamp, actual_tokens)
                    return


def estimate_tokens(text: str) -> int:
    """Rough token count for rate-limit accounting only.

    Deliberately not a real tokeniser: the estimate only has to be close enough
    to keep the limiter honest, and pulling in a tokeniser for it would be a
    heavy dependency for an approximation. Four characters per token
    understates dense tabular text, so the figure is rounded up.
    """
    return max(1, (len(text) + 3) // 4)
