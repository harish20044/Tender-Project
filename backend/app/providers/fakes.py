"""Deterministic stand-ins for the hosted providers.

CI runs without API keys, and unit tests should not depend on a paid service
being reachable or on a model's output being stable. These satisfy the same
protocols with predictable behaviour, and record what they were asked so tests
can assert on the calls rather than the text.
"""

from __future__ import annotations

import hashlib
from typing import Any

from app.providers.base import ChatMessage, Completion, RerankResult


class FakeChatProvider:
    """Returns queued responses in order, recording every call."""

    def __init__(self, responses: list[str] | None = None) -> None:
        self._responses = list(responses or [])
        self.calls: list[dict[str, Any]] = []

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
        self.calls.append(
            {
                "messages": messages,
                "model": model,
                "temperature": temperature,
                "max_tokens": max_tokens,
                "json_schema": json_schema,
                "reasoning_effort": reasoning_effort,
            }
        )

        text = self._responses.pop(0) if self._responses else "{}"
        return Completion(
            text=text,
            model=model or "fake-model",
            prompt_tokens=sum(len(m.content) // 4 for m in messages),
            completion_tokens=len(text) // 4,
        )


class FakeEmbeddingProvider:
    """Hash-derived vectors.

    Identical text always yields an identical vector and different text
    reliably differs, which is all retrieval tests need. The values carry no
    semantic meaning, so tests must not assert on relative distances.
    """

    def __init__(self, dimensions: int = 512) -> None:
        self._dimensions = dimensions
        self.embedded: list[tuple[str, bool]] = []

    @property
    def dimensions(self) -> int:
        return self._dimensions

    async def embed(self, texts: list[str], *, is_query: bool = False) -> list[list[float]]:
        vectors: list[list[float]] = []

        for text in texts:
            self.embedded.append((text, is_query))
            digest = hashlib.sha256(text.encode("utf-8")).digest()
            # Cycle the digest out to the required width and scale to [-1, 1].
            raw = [digest[i % len(digest)] for i in range(self._dimensions)]
            vectors.append([(value - 127.5) / 127.5 for value in raw])

        return vectors


class FakeRerankProvider:
    """Preserves input order with descending scores.

    A reranker that reorders nothing makes it obvious in a failing test whether
    a behaviour depends on reranking or merely on retrieval order.
    """

    def __init__(self) -> None:
        self.calls: list[tuple[str, int]] = []

    async def rerank(
        self, query: str, documents: list[str], *, top_n: int | None = None
    ) -> list[RerankResult]:
        self.calls.append((query, len(documents)))

        limit = top_n if top_n is not None else len(documents)
        return [
            RerankResult(index=i, score=1.0 - (i / max(1, len(documents))))
            for i in range(min(limit, len(documents)))
        ]
