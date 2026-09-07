"""Jina embeddings and reranking.

Embedding is the larger of the two spends, so two economies are applied here
rather than left to callers.

Output dimensions are truncated using the model's Matryoshka property. The
full vector is longer than retrieval over this corpus needs, and the shorter
one costs less to store and compare.

Passages and queries are encoded with different task types. They share a
vector space but are not encoded identically, and treating a query as a
passage measurably degrades retrieval.

Deduplication by content hash lives one layer up, in the pipeline, because it
needs the database. Boilerplate clauses repeat heavily between tenders, so it
removes a large share of the work before it reaches this client.
"""

from __future__ import annotations

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
from app.providers.base import ProviderError, ProviderRateLimitError, RerankResult

logger = get_logger(__name__)

JINA_BASE_URL = "https://api.jina.ai/v1"


class JinaProvider:
    """Embeddings and reranking. One client, since both share a key and host."""

    def __init__(
        self,
        api_key: str | None = None,
        *,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        settings = get_settings()

        self._api_key = api_key if api_key is not None else settings.jina_api_key
        if not self._api_key:
            raise ProviderError("JINA_API_KEY is not set. Retrieval cannot run without it.")

        self._embed_model = settings.jina_embed_model
        self._rerank_model = settings.jina_rerank_model
        self._dimensions = settings.jina_embed_dimensions
        self._batch_size = settings.jina_embed_batch_size

        self._client = client or httpx.AsyncClient(
            base_url=JINA_BASE_URL,
            timeout=httpx.Timeout(120.0),
            headers={
                "Authorization": f"Bearer {self._api_key}",
                "Content-Type": "application/json",
            },
        )

    @property
    def dimensions(self) -> int:
        return self._dimensions

    async def embed(self, texts: list[str], *, is_query: bool = False) -> list[list[float]]:
        if not texts:
            return []

        vectors: list[list[float]] = []
        # Batched to stay inside the request size limit and to keep a failure
        # from costing the whole document's worth of work.
        for start in range(0, len(texts), self._batch_size):
            batch = texts[start : start + self._batch_size]
            vectors.extend(await self._embed_batch(batch, is_query=is_query))

        return vectors

    async def _embed_batch(self, batch: list[str], *, is_query: bool) -> list[list[float]]:
        payload = {
            "model": self._embed_model,
            "task": "retrieval.query" if is_query else "retrieval.passage",
            "dimensions": self._dimensions,
            "input": batch,
        }

        body = await self._post("/embeddings", payload)

        try:
            # The API does not guarantee input order in the response, so
            # entries are placed by their stated index rather than appended.
            ordered: list[list[float]] = [[] for _ in batch]
            for item in body["data"]:
                ordered[int(item["index"])] = [float(value) for value in item["embedding"]]
        except (KeyError, IndexError, ValueError) as exc:
            raise ProviderError(f"Unexpected Jina embeddings response: {str(body)[:400]}") from exc

        if any(not vector for vector in ordered):
            raise ProviderError("Jina returned fewer embeddings than inputs")

        return ordered

    async def rerank(
        self, query: str, documents: list[str], *, top_n: int | None = None
    ) -> list[RerankResult]:
        """Reorder candidates by relevance to the query.

        A cross-encoder reads the query and passage together rather than
        comparing two independently computed vectors, which is why this
        recovers relevance that dense retrieval alone misses. It is the single
        largest quality gain in the retrieval path.
        """
        if not documents:
            return []

        payload: dict[str, Any] = {
            "model": self._rerank_model,
            "query": query,
            "documents": documents,
            "top_n": top_n if top_n is not None else len(documents),
        }

        body = await self._post("/rerank", payload)

        try:
            return [
                RerankResult(index=int(item["index"]), score=float(item["relevance_score"]))
                for item in body["results"]
            ]
        except (KeyError, ValueError) as exc:
            raise ProviderError(f"Unexpected Jina rerank response: {str(body)[:400]}") from exc

    async def _post(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        async for attempt in AsyncRetrying(
            stop=stop_after_attempt(4),
            wait=wait_exponential(multiplier=1, min=1, max=20),
            retry=retry_if_exception_type((ProviderRateLimitError, httpx.TransportError)),
            reraise=True,
        ):
            with attempt:
                response = await self._client.post(path, json=payload)

                if response.status_code == 429:
                    logger.warning("jina_rate_limited", path=path)
                    raise ProviderRateLimitError("Jina rate limit reached")

                if response.status_code >= 500:
                    raise httpx.TransportError(f"Jina returned {response.status_code}")

                if response.status_code >= 400:
                    raise ProviderError(
                        f"Jina rejected the request ({response.status_code}): {response.text}"
                    )

                result: dict[str, Any] = response.json()
                return result

        raise ProviderError("Jina call exhausted its retries")

    async def aclose(self) -> None:
        await self._client.aclose()
