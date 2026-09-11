FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

# tesseract-ocr    scanned pages with no text layer
# poppler-utils    page rasterisation ahead of OCR
# libgl1/libglib   image handling pulled in by the document parser
RUN apt-get update && apt-get install -y --no-install-recommends \
      tesseract-ocr \
      tesseract-ocr-eng \
      poppler-utils \
      libgl1 \
      libglib2.0-0 \
      curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Test and lint tooling is useful in a development container and dead weight
# in a deployed one, so it is opt-in. Compose leaves the default, which keeps
# `docker compose exec api pytest` working.
ARG INSTALL_DEV=true

# Dependency layer stays cached while only application code changes.
COPY pyproject.toml ./
RUN pip install --upgrade pip \
    && if [ "$INSTALL_DEV" = "true" ]; then \
         pip install -e ".[dev]"; \
       else \
         pip install -e .; \
       fi

COPY . .

EXPOSE 8001

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8001"]
