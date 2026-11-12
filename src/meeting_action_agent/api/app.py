from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from ..env import load_env
from .routes import router

DEV_ORIGINS = ["http://localhost:5173"]


def create_app() -> FastAPI:
    app = FastAPI(title="Meeting Action Agent API")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=DEV_ORIGINS,
        allow_methods=["GET", "POST", "PUT"],
        allow_headers=["Content-Type"],
    )
    app.include_router(router)
    return app


def create_app_from_env() -> FastAPI:
    """uvicorn --factory 진입점. import 시점이 아니라 실행 시점에만 .env를 읽는다."""
    load_env()
    return create_app()
