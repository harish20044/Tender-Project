# Tender Intelligence & Bid Decision Support

AI-assisted analysis of construction tender documents, producing an auditable Bid / No-Bid recommendation.

Given a tender pack (Notice Inviting Tender, Bill of Quantities, conditions of contract), the system extracts the requirements that matter, answers questions against the source text with page-level citations, compares the tender against past ones, and produces a scored recommendation that a human can check line by line.

---

## The one design decision that shapes everything

**The recommendation is not a language model's opinion.**

```
PDF → parse + OCR → chunk → LLM EXTRACTS facts into a validated schema (with page + bbox)
                          → DETERMINISTIC engine applies the hard gates
                          → LLM NARRATES the resulting scorecard
```

Eligibility is arithmetic, not inference. Annual turnover against threshold, similar-work experience, earnest money affordability, registration class, deadline feasibility — these are evaluated by code, and the same inputs always give the same answer. The model's job is to read the document accurately and to explain the result in prose. It never casts the vote.

Every extracted fact carries the page and bounding box it came from, so any number in the interface can be traced back to the exact region of the source PDF that produced it. Without that link, "explainable" would be an unfounded claim.

---

## Stack

| Concern | Choice | Why |
|---|---|---|
| Parsing | Docling, pdfplumber fallback | Handles multi-page, merged-cell Bill of Quantities tables that line-based extractors miss |
| OCR | Tesseract, auto-routed per page | Tenders from government portals are frequently scanned images with no text layer |
| Embeddings | Jina `jina-embeddings-v3` | Hosted; no GPU available in this deployment |
| Reranking | Jina `jina-reranker-v2-base-multilingual` | Cross-encoder reranking is the single largest retrieval quality gain |
| Vector store | pgvector inside PostgreSQL | One database, real metadata filtering, transactional with the facts table |
| Generation | Groq, GPT-OSS 120B | Fast enough to fan out all 50 FAQs per document in parallel at ingest |
| Orchestration | LangGraph | Explicit state machine with checkpointing for the decision agent |
| Queue | Celery + Redis | A 500-page tender cannot be parsed inside an HTTP request |
| Object storage | MinIO (S3 API) | Source PDFs |
| API | FastAPI + Pydantic, Alembic | |
| Auth | Keycloak | Roles: Analyst, Manager, Admin |
| Frontend | React, TypeScript, Vite, Tailwind, pdf.js | |

The AI layer is entirely hosted, so tender text leaves the machine. That is a deliberate trade-off given no local GPU. All model calls sit behind a provider interface, so a self-hosted mode can be added later without touching calling code.

---

## Getting started

Full instructions, including how to run without Docker, are in
[RUNNING.md](RUNNING.md). The short version follows.

Requires Docker and Docker Compose. Docker Desktop on Windows needs WSL2,
which needs administrator rights; if that route is closed, use route B in
RUNNING.md, which replaces Postgres with a hosted database, drops Redis, and
stores documents on the local filesystem.

```bash
cp .env.example .env
```

Fill in two keys in `.env`:

- `GROQ_API_KEY` from https://console.groq.com/keys
- `JINA_API_KEY` from https://jina.ai/embeddings

Then:

```bash
docker compose up --build
```

| Service | URL |
|---|---|
| Frontend | http://localhost:5173 |
| API docs | http://localhost:8000/docs |
| MinIO console | http://localhost:9001 |
| Keycloak | http://localhost:8080 |

Apply migrations on first run:

```bash
docker compose exec api alembic upgrade head
```

---

## Cost control

Both providers are metered, and a single large tender is a meaningful token spend. Four mitigations are built in rather than bolted on:

- Chunks are content-hashed, so identical text embeds once across the whole corpus.
- Embeddings are truncated to 512 dimensions using the model's Matryoshka property, cutting storage and comparison cost with little retrieval loss.
- FAQ answers are computed once per document at ingest and cached, not recomputed per view.
- All Groq traffic passes through one throttled, retrying client. Set `GROQ_MAX_RPM` and `GROQ_MAX_TPM` in `.env` below your account's real limits.

Model calls happen in workers, never in a request handler.

The generation models reason before answering, and reasoning is billed as
output. `GROQ_REASONING_EFFORT` is the lever: on extraction, `low` reaches the
same answer as `high` for roughly a quarter of the reasoning tokens. Raise it
for decision narration, where the argument is harder than the arithmetic.

One trap worth knowing. On a reasoning model `max_tokens` caps reasoning and
answer together, and reasoning comes first. Set it too low and the call
returns an empty string having spent its whole budget thinking.

Groq's catalogue changes. Confirm what your key can reach rather than assuming
a model is still served:

```bash
curl https://api.groq.com/openai/v1/models -H "Authorization: Bearer $GROQ_API_KEY"
```

---

## Repository layout

```
backend/
  app/
    api/          FastAPI routers
    core/         config, logging, security
    db/           SQLAlchemy models, session, migrations
    pipeline/     parse → OCR → chunk → embed → index
    extraction/   Pydantic fact schemas and the LLM calls that fill them
    decision/     deterministic gates, risk taxonomy, scorecard
    similarity/   comparison against the historical corpus
    corpus/       pluggable historical tender source
    workers/      Celery tasks
    eval/         golden set and scoring harness
frontend/
  src/            React application
data/
  seed/           historical corpus (not committed)
  golden/         evaluation fixtures
docs/             architecture and decision records
infra/            Keycloak realm and related config
```

---

## Status

Under active development. Current phase: project scaffold.

Known open item: the historical corpus for tender comparison does not exist yet and is being assembled manually. Until it has enough density, the comparison feature reports low confidence rather than presenting weak matches as strong ones.
