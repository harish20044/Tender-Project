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
| Parsing | PyMuPDF | Fast, and returns per-block geometry, which is what lets a citation point at a region rather than a page |
| OCR | Tesseract | Reads the portal's CAPTCHA today. Pages with no text layer are detected and reported, but not yet routed through OCR |
| Embeddings | Jina `jina-embeddings-v3` | Hosted; no GPU available in this deployment |
| Reranking | Jina `jina-reranker-v2-base-multilingual` | Client implemented; retrieval currently fuses dense and lexical rankings without it |
| Vector store | pgvector, via Supabase | One database, real metadata filtering, transactional with the facts table |
| Generation | Groq, GPT-OSS 120B | Fast enough to fan out all 50 FAQs per document in parallel at ingest |
| Queue | Celery + Redis | A 500-page tender cannot be parsed inside an HTTP request |
| Object storage | Supabase Storage (S3-compatible) | Source PDFs |
| API | FastAPI + Pydantic, Alembic | |
| Auth | Supabase Auth | ES256 tokens verified against the project's published keys; roles read from server-written app_metadata |
| Frontend | React, TypeScript, Vite, Tailwind | |

The AI layer is entirely hosted, so tender text leaves the machine. That is a deliberate trade-off given no local GPU. All model calls sit behind a provider interface, so a self-hosted mode can be added later without touching calling code.

Database, storage and auth are hosted on Supabase rather than self-managed, since this project also runs on machines that cannot reliably run Docker at all (see [RUNNING.md](RUNNING.md)). Only the API, worker, Redis and frontend run in containers.

---

## Getting started

Full instructions, including how to run without Docker, are in
[RUNNING.md](RUNNING.md). The short version follows.

Requires a free [Supabase](https://supabase.com) project (database, storage
and auth) and Docker for the rest. Docker Desktop on Windows needs WSL2, which
needs administrator rights; if that route is closed, use route B in
RUNNING.md, which drops Docker and Redis entirely and runs the API directly.

```bash
cp .env.example .env
```

Fill in `.env`: the five Supabase values from your project's dashboard (see
RUNNING.md for exactly where each one lives), plus two provider keys:

- `GROQ_API_KEY` from https://console.groq.com/keys
- `JINA_API_KEY` from https://jina.ai/embeddings

Then:

```bash
docker compose up --build
```

| Service | URL |
|---|---|
| Frontend | http://localhost:5174 |
| API docs | http://localhost:8001/docs |
| Database / storage / auth | your Supabase project dashboard |

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
```

---

## Status

All four objectives run end to end against the live stack.

| Capability | State |
|---|---|
| Portal listing scraper | Working. Writes straight to the `tenders` table; a scheduled task refreshes it. |
| Upload and ingest | Working. PDF → per-page parse → chunks that keep their page range → Jina embeddings in pgvector. |
| Question answering | Working. Hybrid retrieval (pgvector cosine + Postgres full text, fused by reciprocal rank), answered from retrieved passages only, with citations. |
| The 50 standard FAQs | Working, cached per tender. |
| Fact extraction | Working. 22 decision-relevant figures with the page, sentence and page region each came from. |
| Bid / No-Bid decision | Working. Nine gates, deterministic, with a risk register and counterfactuals. |
| Comparison against past tenders | Working. Scope similarity, reissue detection and structural matches, over a corpus seeded from real scraped tenders. Awarded value, winning bidder and bid count where the award has been published. |
| Workspace screen | Working. Findings beside their source sentence, correctable in place. |
| Sign-in and roles | Working. Tokens verified against Supabase's public keys; every route protected by default. |
| Evaluation | Working. A golden set scored through the production pipeline, with thresholds that fail the run. |
| Award outcomes | Working. Reads CPPP's Result of Tenders through two CAPTCHA gates into the historical corpus. |
| Document pack download | Reaches the issuing portal and stops at its Digital Signature gate — see below. |

---

## Measuring it

Accuracy claims about extraction are worth nothing unasserted, so there is a
golden set with known answers and a harness that scores against it:

```bash
python scripts/evaluate.py                      # the table below
python scripts/evaluate.py --json runs/today.json
```

Four rates, because they fail for different reasons and have opposite
remedies:

| Rate | What it catches | Last run |
|---|---|---|
| Value accuracy | Right number, right unit — crore and lakh converted to rupees, months to days | 22/22 |
| Page accuracy | The citation points at the page the value is actually on | 21/22 |
| Region coverage | The citation narrows to a box on that page, not just the page | 21/22 |
| Abstention | A question the document does not answer is declined, not invented | 3/3 |

Precision is reported beside accuracy deliberately: a system that answers
nothing is perfectly precise and useless, and one that guesses freely is the
reverse. A null counts as an honest abstention and is penalised in accuracy
only.

The harness runs the real ingest, retrieval and extraction path rather than
calling the model directly. That is the whole point, and it has earned its
keep: writing it surfaced three bugs that no prompt-level test could reach.

- **The rate limiter could deadlock.** A single call costing more than the
  whole per-minute token budget could never satisfy the ceiling, so it waited
  for room that would never appear — the window emptied, the call still did
  not fit, and it slept another minute, forever. Extraction simply hung, with
  no error. Ten retrieved passages of a long tender clear a 6000 TPM ceiling
  on their own, so any real pack would have hit it.
- **Retrieval mixed superseded document versions.** Every version's chunks
  competed on equal terms, so a corrected pack could be answered from the
  text it corrected, and which copy won was arbitrary. This is what held
  region coverage down to 77%: passages from versions written before block
  geometry existed carried no boxes.
- **The golden PDF was not reproducible.** It embedded a creation timestamp
  and a random trailer ID, so ingest saw a new document on every run and the
  corpus filled with copies of one file — which is how the version-mixing bug
  became visible in the first place.

It needs the database and both provider keys and spends real tokens, which is
why it is a script and not part of `pytest`. `scripts/evaluate.py` exits
non-zero below its thresholds, so it can gate a release.

The honest limitation: the golden document is one this project generates, so
its figures are written in the forms Indian tenders use but its layout is far
cleaner than a scanned pack. It measures unit conversion, page attribution
and abstention; it does not prove performance on a real scanned document.
`GoldenCase` takes an annotated real pack as soon as there is one.

### Where this stops, and why

**Document packs stop at a Digital Signature Certificate.** The chain is
implemented and runs: open the tender's live detail page, clear CPPP's
CAPTCHA, find the "Tender Document" link, decode it, and land on the issuing
department's own portal. The portal then says, verbatim:

> It seems DSCHandler is not installed or is not started. If DSCHandler is
> installed, please start it to login!

DSCHandler is the local service that talks to a **Digital Signature
Certificate** — a hardware token issued to a *registered bidder*. The packs
sit behind bidder authentication, so reaching the portal is not the same as
being allowed in. This is an authorisation boundary, not a technical one, and
automating past it would mean impersonating a registered entity. The
downloader reports `requires_signature` and stops there on purpose.

In practice this costs little: an estimator *is* a registered bidder, has the
token, and already holds the pack. Upload is the real workflow, and it is the
path the pipeline is built around.

Three things on the way there were genuine bugs, now fixed: CPPP moved its
outbound links behind its own base64 redirector, so the link to the
department sat on `eprocure.gov.in/cppp` — exactly the host the matcher
excluded, which is why every tender reported "no download link"; those
redirector anchors carry `rel="noreferrer"`, so following one sends no
Referer and the redirector answers with a 302 back to the CPPP home page, a
click that appears to work and lands nowhere; and a crashed browser threw
from the cleanup in `finally`, replacing the real error with a stack trace
about window handles.

**Award outcomes are published for some tenders, not all.** The corpus now
carries awarded value, winning bidder and bid count, read from CPPP's Result
of Tenders (`scripts/scrape_awards.py`). Two caveats, both the portal's. It
prints its own disclaimer beside the figure — "Currency regarding Contract
Value may please be checked with the corresponding tender portals/websites" —
so the value carries no stated unit and is flagged unverified rather than
presented as a checked rupee amount. And many awards are published with the
value left at zero, which is recorded as *not stated* rather than as a nil
contract. Expect outcomes on a minority of rows, and read coverage with
`scripts/scrape_awards.py --coverage`.

Gathering them is slow by construction: the search and every detail page sit
behind separate CAPTCHAs, cleared about one attempt in seven, so a record
costs a dozen or more page loads. It is a batch job, not a request handler.

**Citations narrow to a block, not a glyph.** A fact now carries the rectangle
of the laid-out blocks its quote spans, stored per chunk and served on the
fact. Where a quote cannot be located in its passage the page citation
survives and the box is omitted rather than guessed — one fact in the golden
run takes that path, and the eval measures it as region coverage rather than
hiding it.

The box reaches the API but **the workspace does not draw it yet**: findings
still highlight their source sentence as text, not a region over the page
image. That is the one piece of this left to build, and it is now a frontend
change against data that is already there.
