-- Runs once, on first container start, before the application connects.
-- Alembic owns the schema; this file only installs extensions that must exist
-- before any migration runs.

CREATE EXTENSION IF NOT EXISTS vector;      -- pgvector: embedding storage and ANN search
CREATE EXTENSION IF NOT EXISTS pg_trgm;     -- trigram similarity for lexical matching
CREATE EXTENSION IF NOT EXISTS unaccent;    -- normalise accented characters in full-text search
CREATE EXTENSION IF NOT EXISTS "uuid-ossp"; -- uuid generation
