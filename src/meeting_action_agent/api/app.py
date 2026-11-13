import os

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from ..env import load_env
from .routes import router

DEV_ORIGINS = ["http://localhost:5173"]


def create_app() -> FastAPI:
    app = FastAPI(title="Meeting Action Agent API")
    app.add_middleware(
        CORSMiddleware,
        # 다른 PC에서 접속할 때만 MAA_CORS_ORIGINS(쉼표 구분)로 명시적으로 추가한다. 기본은 로컬 개발 origin뿐이다.
        allow_origins=DEV_ORIGINS + [o.strip() for o in os.environ.get("MAA_CORS_ORIGINS", "").split(",") if o.strip()],
        allow_methods=["GET", "POST", "PUT", "DELETE"],
        allow_headers=["Content-Type"],
    )
    app.include_router(router)
    return app


def create_app_from_env() -> FastAPI:
    """uvicorn --factory 진입점. import 시점이 아니라 실행 시점에만 .env를 읽는다."""
    load_env()
    return create_app()
