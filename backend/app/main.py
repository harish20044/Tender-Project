"""FastAPI application entry point."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes import tenders
from app.core.config import get_settings
from app.core.logging import configure_logging, get_logger

logger = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    configure_logging(settings.log_level)
    logger.info(
        "starting",
        env=settings.app_env,
        providers_configured=settings.providers_configured,
    )
    if not settings.providers_configured:
        # Loud, because a missing key surfaces much later as a confusing
        # ingestion failure rather than a startup error.
        logger.warning(
            "model_providers_unconfigured",
            detail="GROQ_API_KEY and JINA_API_KEY are unset. "
            "Extraction and retrieval will not run.",
        )
    yield
    logger.info("shutting_down")


def create_app() -> FastAPI:
    settings = get_settings()

    app = FastAPI(
        title="Tender Intelligence API",
        description=(
            "Analysis of construction tender documents and auditable Bid / No-Bid recommendations."
        ),
        version="0.1.0",
        lifespan=lifespan,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.get("/health", tags=["system"])
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    app.include_router(tenders.router)

    return app


app = create_app()
