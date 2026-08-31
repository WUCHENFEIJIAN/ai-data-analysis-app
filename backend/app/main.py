from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api.router import api_router
from app.core.config import Settings, get_settings
from app.core.database import Database
from app.core.errors import AppError
from app.core.logging import configure_logging
from app.llm.openai_compatible import OpenAICompatibleProvider
from app.sandbox.executor import SandboxExecutor
from app.services.analysis_runs import recover_interrupted_runs
from app.services.model_config import get_active, provider_from_record


def create_app(settings: Settings | None = None) -> FastAPI:
    app_settings = settings or get_settings()
    configure_logging(app_settings.app_env)
    database = Database(app_settings.database_url)

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        database.create_all()
        with database.session() as session:
            active_model = get_active(session)
            if active_model is not None:
                application.state.llm_provider = provider_from_record(active_model, app_settings)
            recover_interrupted_runs(session)
        app_settings.workspace_root.mkdir(parents=True, exist_ok=True)
        yield
        database.dispose()

    application = FastAPI(title="AI Data Analysis API", version="0.1.0", lifespan=lifespan)
    application.state.settings = app_settings
    application.state.database = database
    application.state.llm_provider = (
        OpenAICompatibleProvider(
            app_settings.llm_api_base,
            app_settings.llm_api_key,
            app_settings.llm_model,
            app_settings.llm_timeout_seconds,
            app_settings.llm_max_retries,
            max_tokens=app_settings.llm_max_tokens,
            thinking_enabled=app_settings.llm_thinking_enabled,
        )
        if app_settings.llm_api_base and app_settings.llm_api_key and app_settings.llm_model
        else None
    )
    application.state.sandbox_executor = SandboxExecutor(
        app_settings.sandbox_image,
        app_settings.python_timeout_seconds,
        app_settings.python_memory_limit,
        app_settings.python_cpu_limit,
    )
    application.state.analysis_background_tasks = {}
    application.add_middleware(
        CORSMiddleware,
        allow_origins=app_settings.frontend_origins,
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    application.include_router(api_router, prefix="/api")

    @application.exception_handler(AppError)
    async def handle_app_error(_: Request, exc: AppError) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content={"error": {"code": exc.code, "message": exc.message, "details": exc.details}},
        )

    return application


app = create_app()
