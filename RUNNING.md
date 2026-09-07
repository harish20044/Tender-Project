# Running the project

Two routes. Pick by whether the machine can run Docker.

Docker Desktop on Windows requires WSL2, and installing WSL2 requires
administrator rights. On a locked-down corporate machine that route is closed,
so route B exists and is fully supported.

---

## A. With Docker

Everything runs in containers, matching production. This is the normal way to
run the project.

First time only:

```bash
cp .env.example .env
```

Add your two keys to `.env`:

- `GROQ_API_KEY` from https://console.groq.com/keys
- `JINA_API_KEY` from https://jina.ai/embeddings

Then, every time:

```bash
docker compose up -d --build
```

Open http://localhost:5173.

| Service | URL |
|---|---|
| Frontend | http://localhost:5173 |
| API docs | http://localhost:8000/docs |
| MinIO console | http://localhost:9001 |
| Keycloak | http://localhost:8080 |

### Getting tender data

The dashboard reads tender notices scraped from the public procurement
portal. Nothing appears until a scrape has run:

```bash
docker compose exec api python scripts/scrape_tenders.py --pages 12
```

Run it again whenever you want fresher notices; the portal listing turns over
through the day. Results are written to `data/cache/`, which is mounted from
the host, so a scrape run inside or outside the container is visible to both.

### Everyday commands

```bash
docker compose logs -f api          # follow one service
docker compose ps                   # what is running
docker compose restart api          # after a config change
docker compose down                 # stop, keep data
docker compose down -v              # stop and wipe the databases
```

Application code is bind-mounted, so editing a file under `backend/` or
`frontend/` reloads automatically. Rebuild only when dependencies change:

```bash
docker compose up -d --build
```

### Migrations

There are no migrations yet, so this currently does nothing. It is the command
once the schema lands:

```bash
docker compose exec api alembic upgrade head
```

---

## B. Without Docker

Needs Python 3.12 or newer and Node 20 or newer. Nothing here requires
administrator rights.

The three services Docker would have provided are replaced as follows.

| Container | Replacement | Why it works |
|---|---|---|
| PostgreSQL + pgvector | A free hosted Postgres | pgvector is available as an extension; nothing to install locally |
| Redis | Nothing | With no broker configured, Celery runs tasks inline |
| MinIO | Local filesystem | Same storage interface, different backend |

### 1. The dashboard, end to end

The dashboard needs no database. It reads tender notices scraped from the
public procurement portal.

Fetch the data, then start both processes:

```bash
cd backend
pip install -e ".[dev]"
python scripts/scrape_tenders.py --pages 12
python -m uvicorn app.main:app --reload
```

In a second terminal:

```bash
cd frontend
npm install
npm run dev
```

Open http://localhost:5173. The dev server proxies `/api` to the backend, so
there is no base URL to configure.

Re-run the scrape whenever you want fresher notices. The portal listing turns
over through the day.

```bash
python scripts/scrape_tenders.py --pages 20 --construction-only
```

### 2. Database

Create a free Postgres at https://neon.tech or https://supabase.com and enable
the extension from their SQL console:

```sql
CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS pg_trgm;
CREATE EXTENSION IF NOT EXISTS unaccent;
```

Copy the connection string into `.env` as `DATABASE_URL`, changing the scheme
to `postgresql+psycopg://`.

### 3. Backend

```bash
cd backend
python -m venv .venv
.venv/Scripts/activate        # Windows
pip install -e ".[dev]"
```

Set these in `.env` for this mode:

```
STORAGE_BACKEND=filesystem
STORAGE_PATH=./var/documents
REDIS_URL=
CELERY_BROKER_URL=
CELERY_RESULT_BACKEND=
DATABASE_URL=postgresql+psycopg://...your hosted database...
GROQ_API_KEY=...
JINA_API_KEY=...
```

Then:

```bash
alembic upgrade head
uvicorn app.main:app --reload
```

API docs at http://localhost:8000/docs.

### 4. Document parsing, when you need it

Parsing is a separate extra because Docling pulls PyTorch, which is a large
download and lags new Python releases. The API, database and provider layers
run without it.

```bash
pip install -e ".[parsing]"
```

Tesseract is a separate program, not a Python package. Without it, scanned
tender documents cannot be read at all. Install the Windows build from
https://github.com/UB-Mannheim/tesseract/wiki and put it on `PATH`.

---

## Differences that matter

Route B is not production. Three things genuinely differ, and each will
eventually need the real thing.

**Ingestion blocks.** Without a broker, uploading a large tender ties up the
request until parsing finishes. Fine for developing the pipeline, wrong for
real use.

**Stored documents have no expiring URLs.** The filesystem backend serves
files through the API with no time limit. Tender documents are commercially
sensitive, so production needs the S3 backend and its signed URLs.

**No Keycloak.** Authentication is not part of route B. Endpoints are
unprotected, so do not point it at anything you care about.

---

## Troubleshooting

**`model_not_found` from Groq.** The catalogue changes. Check what your key
can actually reach:

```bash
curl https://api.groq.com/openai/v1/models -H "Authorization: Bearer $GROQ_API_KEY"
```

**An empty string back from the model.** The generation models reason before
answering, and `max_tokens` caps reasoning and answer together. Too small a
budget is spent entirely on reasoning. Raise `max_tokens`, or lower
`GROQ_REASONING_EFFORT`.

**curl reaches Groq but not Jina, with no output at all.** A Windows
certificate-revocation check failing in schannel, not a problem with the key.
Python uses its own certificate bundle and is unaffected, so the application
works. Add `--ssl-no-revoke` if you want curl to work too.
