# Running the project

Database, file storage, and authentication all live on Supabase — that part
is identical whether or not you use Docker, and it's why route B below is as
thin as it is: there's no local Postgres or MinIO to replace anymore.

Docker Desktop on Windows requires WSL2, and installing WSL2 requires
administrator rights. On a locked-down machine that route is closed, so
route B exists and is fully supported.

---

## 0. Set up Supabase (needed for both routes)

1. Sign up at https://supabase.com and create a project. Save the database
   password you're asked to set — you'll need it once.
2. **Database → Extensions** in the sidebar → search "vector" → enable it.
   This is pgvector.
3. **Storage** in the sidebar → **New bucket** → name it `tender-documents` →
   leave it **private**. Tender documents are commercially sensitive.
4. Collect five values from **Settings**:

   | Settings page | Value | `.env` key |
   |---|---|---|
   | API | Project URL | `SUPABASE_URL` |
   | API | `anon` public key | `SUPABASE_ANON_KEY` |
   | API | `service_role` secret key | `SUPABASE_SERVICE_ROLE_KEY` |
   | API → JWT Settings | JWT Secret | `SUPABASE_JWT_SECRET` |
   | Database → Connection string | URI | `DATABASE_URL` |

5. Also from **Settings → API → S3 Access Keys**, generate a key pair for
   Storage's S3-compatible endpoint (separate from the anon/service_role keys
   above):

   | Value | `.env` key |
   |---|---|
   | `https://<project_ref>.storage.supabase.co/storage/v1/s3` | `S3_ENDPOINT_URL` |
   | Access Key ID | `S3_ACCESS_KEY` |
   | Secret Access Key | `S3_SECRET_KEY` |

The `service_role` key bypasses every access rule. Server-side only — never
send it to the frontend, never commit it.

```bash
cp .env.example .env
```

Fill in the values above, plus:

- `GROQ_API_KEY` from https://console.groq.com/keys
- `JINA_API_KEY` from https://jina.ai/embeddings

---

## A. With Docker

Runs the API, worker, and frontend in containers; Redis too, since Celery
still needs a broker. Database, storage, and auth are Supabase regardless.

```bash
docker compose up -d --build
```

Open http://localhost:5174.

| Service | URL |
|---|---|
| Frontend | http://localhost:5174 |
| API docs | http://localhost:8001/docs |
| Database / storage / auth | your Supabase dashboard |

Ports are shifted off their frameworks' defaults (Vite's 5173, uvicorn's
8000, Redis's 6379 → 5174, 8001, 6380) so this project can run alongside
another local one without either fighting over a port.

### Getting tender data

The dashboard reads tender notices scraped from the public procurement
portal. Nothing appears until a scrape has run:

```bash
docker compose exec api python scripts/scrape_tenders.py --pages 12
```

Run it again whenever you want fresher notices; the portal listing turns over
through the day. Results are written to `data/cache/`, which is mounted from
the host, so a scrape run inside or outside the container is visible to both.

### Downloading tender documents

The scraped notices point at tender packs that the portal keeps behind a
CAPTCHA gate. `scripts/download_documents.py` drives a real Chrome through
that gate — the CAPTCHA is read by Tesseract — saves each pack as a ZIP, and
uploads it to Supabase Storage, recording the URL on the tender's row in the
cache. Because it needs Chrome and Tesseract, run it on the host rather than
inside the API container.

One-time setup:

```bash
cd backend
pip install -e ".[scraper]"
```

- Tesseract is a separate program. Windows build:
  https://github.com/UB-Mannheim/tesseract/wiki — put it on `PATH` or set
  `TESSERACT_CMD` in `.env`.
- Chrome must be installed; Selenium fetches the matching driver itself.
- Set `SUPABASE_URL` and `SUPABASE_SERVICE_ROLE_KEY` in `.env` and create the
  `tender-documents` bucket in the Supabase dashboard. `--local-only` skips
  the upload and keeps the ZIPs on disk.

Then, for whatever the last scrape collected:

```bash
python scripts/download_documents.py --limit 5          # headless
python scripts/download_documents.py --headed           # watch it work
python scripts/download_documents.py --reference 2026_NHAI_290394_1
```

Each pack gets a bounded number of OCR attempts (`CAPTCHA_MAX_ATTEMPTS`): on a
rejection the script refreshes the image and reads again, and it pauses
between tenders. `--debug-dir var/captcha` saves every image beside its read,
for tuning the threshold against real portal CAPTCHAs.

### Everyday commands

```bash
docker compose logs -f api          # follow one service
docker compose ps                   # what is running
docker compose restart api          # after a config change
docker compose down                 # stop, keep the Redis volume
docker compose down -v              # stop and wipe it
```

Application code is bind-mounted, so editing a file under `backend/` or
`frontend/` reloads automatically. Rebuild only when dependencies change:

```bash
docker compose up -d --build
```

### Migrations

The schema lives in `backend/app/db/models.py`; Alembic turns it into SQL.
The first migration also enables the Postgres extensions the schema needs
(`vector` for embeddings, `pg_trgm` for fuzzy question matching) — nothing
else in the project does this since it moved to a hosted Supabase database.

```bash
docker compose exec api alembic upgrade head
```

---

## B. Without Docker

Needs Python 3.12 or newer and Node 20 or newer. Nothing here requires
administrator rights, and there is no local service to stand up — Supabase
already provides the database and storage, so this route is just running the
API and frontend as ordinary processes.

### 1. The dashboard, end to end

The scraper writes tender notices straight into the `tenders` table, and the
API reads them from there — so `DATABASE_URL` (section 0) needs to be set
even for just this. Run the migration once first if you haven't:

```bash
cd backend
pip install -e ".[dev]"
alembic upgrade head
python scripts/scrape_tenders.py --pages 12
python -m uvicorn app.main:app --reload --port 8001
```

In a second terminal:

```bash
cd frontend
npm install
npm run dev
```

Open http://localhost:5174. The dev server proxies `/api` to the backend, so
there is no base URL to configure.

Re-run the scrape whenever you want fresher notices:

```bash
python scripts/scrape_tenders.py --pages 20 --construction-only
```

### 2. Everything else

Set these in `.env` for this mode — no broker, filesystem storage instead of
Supabase Storage if you'd rather not configure it yet:

```
REDIS_URL=
CELERY_BROKER_URL=
CELERY_RESULT_BACKEND=
STORAGE_BACKEND=filesystem
STORAGE_PATH=./var/documents
```

Everything else (`DATABASE_URL`, `SUPABASE_*`, `GROQ_API_KEY`, `JINA_API_KEY`)
comes from section 0 above. Then:

```bash
alembic upgrade head
uvicorn app.main:app --reload --port 8001
```

API docs at http://localhost:8001/docs.

### 3. Document parsing, when you need it

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

Route B is not production, but the gap is smaller than it used to be — the
database, storage, and auth are the same Supabase project either way. What
still differs:

**Ingestion blocks without a broker.** Uploading a large tender ties up the
request until parsing finishes. Fine for developing the pipeline, wrong for
real use.

**The filesystem storage backend has no expiring URLs.** It serves files
through the API with no time limit. Use the `s3` backend (Supabase Storage)
once you're testing anything beyond the dashboard.

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

**curl reaches Groq but not Jina/Supabase, with no output at all.** A Windows
certificate-revocation check failing in schannel, not a problem with the key.
Python uses its own certificate bundle and is unaffected, so the application
works. Add `--ssl-no-revoke` if you want curl to work too.
