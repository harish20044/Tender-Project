"""Provider interfaces.

Generation, embedding and reranking are all hosted services in this
deployment, but calling code never says so. Everything goes through these
protocols, which keeps three things possible: swapping a vendor without
touching callers, running the test suite with fakes and no network, and adding
a self-hosted mode later if a GPU becomes available.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable


@dataclass(frozen=True)
class ChatMessage:
    role: str  # "system" | "user" | "assistant"
    content: str


@dataclass(frozen=True)
class Completion:
    text: str
    model: str
    prompt_tokens: int
    completion_tokens: int

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens


@dataclass(frozen=True)
class RerankResult:
    index: int  # Position in the documents list passed in
    score: float


@dataclass
class UsageTally:
    """Running token count, so a job can report what it cost.

    Both providers are metered and a single large tender is a meaningful
    spend, so usage is surfaced rather than left to a vendor dashboard.
    """

    prompt_tokens: int = 0
    completion_tokens: int = 0
    embedding_tokens: int = 0
    calls: int = 0
    by_model: dict[str, int] = field(default_factory=dict)

    def record_completion(self, completion: Completion) -> None:
        self.prompt_tokens += completion.prompt_tokens
        self.completion_tokens += completion.completion_tokens
        self.calls += 1
        self.by_model[completion.model] = (
            self.by_model.get(completion.model, 0) + completion.total_tokens
        )

    def record_embedding(self, tokens: int, model: str) -> None:
        self.embedding_tokens += tokens
        self.calls += 1
        self.by_model[model] = self.by_model.get(model, 0) + tokens


@runtime_checkable
class ChatProvider(Protocol):
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
        """Generate a completion.

        When ``json_schema`` is given the provider must constrain output to it.
        Extraction depends on this: a malformed object means a re-run and a
        second charge for the same document.

        ``reasoning_effort`` is honoured by providers whose models reason
        before answering, and ignored by those that do not. It is the main
        cost lever on such models, since reasoning is billed as output.
        """
        ...


@runtime_checkable
class EmbeddingProvider(Protocol):
    @property
    def dimensions(self) -> int: ...

    async def embed(self, texts: list[str], *, is_query: bool = False) -> list[list[float]]:
        """Embed a batch.

        ``is_query`` selects the asymmetric task type where the model supports
        one. Passage and query embeddings occupy the same space but are encoded
        differently, and mixing them costs real retrieval quality.
        """
        ...


@runtime_checkable
class RerankProvider(Protocol):
    async def rerank(
        self, query: str, documents: list[str], *, top_n: int | None = None
    ) -> list[RerankResult]: ...


class ProviderError(RuntimeError):
    """Raised when a provider fails in a way retrying will not fix."""


class ProviderRateLimitError(RuntimeError):
    """Raised when a provider refuses the call for rate reasons.

    Separate from ProviderError because the caller should back off and retry
    rather than fail the job.
    """

    def __init__(self, message: str, retry_after_seconds: float | None = None) -> None:
        super().__init__(message)
        self.retry_after_seconds = retry_after_seconds
