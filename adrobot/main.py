from contextlib import asynccontextmanager
from pathlib import Path
from uuid import UUID

import httpx
import pycountry
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import create_engine, inspect
from sqlalchemy.pool import StaticPool
from starlette.middleware.trustedhost import TrustedHostMiddleware

from adrobot.config import Settings
from adrobot.database import Base, OperationRepository, make_engine
from adrobot.demo import DemoKeitaro
from adrobot.errors import AppError
from adrobot.keitaro import KeitaroClient, KeitaroGateway
from adrobot.schemas import CampaignInput, OffersInput
from adrobot.services import CampaignService, operation_view

STATIC = Path(__file__).parent / "static"


def create_app(
    settings: Settings | None = None, gateway: KeitaroGateway | None = None
) -> FastAPI:
    settings = settings or Settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        if settings.demo:
            engine = create_engine(
                "sqlite://",
                poolclass=StaticPool,
                connect_args={"check_same_thread": False},
            )
            Base.metadata.create_all(engine)
        else:
            engine = make_engine(settings.database_url)
            if "operations" not in inspect(engine).get_table_names():
                engine.dispose()
                raise RuntimeError("БД не подготовлена: выполните alembic upgrade head")
        async with httpx.AsyncClient(
            base_url=f"{settings.keitaro_url}/admin_api/v1/",
            headers={"Api-Key": settings.api_key.get_secret_value()},
            timeout=settings.timeout_seconds,
            follow_redirects=False,
        ) as http:
            client = gateway or (
                DemoKeitaro() if settings.demo else KeitaroClient(http)
            )
            app.state.service = CampaignService(
                client, OperationRepository(engine), settings
            )
            try:
                yield
            finally:
                engine.dispose()

    app = FastAPI(title="Adrobot", lifespan=lifespan, docs_url=None, redoc_url=None)
    app.add_middleware(
        TrustedHostMiddleware,
        allowed_hosts=["localhost", "127.0.0.1", "[::1]", "testserver"],
    )

    @app.middleware("http")
    async def local_request(request: Request, call_next):
        if request.method in ("POST", "PUT", "PATCH", "DELETE"):
            origin = request.headers.get("origin")
            if request.headers.get("x-adrobot-request") != "1" or (
                origin and origin != str(request.base_url).rstrip("/")
            ):
                return JSONResponse(
                    {"detail": "Запрос должен исходить из приложения"}, status_code=403
                )
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self'; style-src 'self'; "
            "img-src 'self' data:; frame-ancestors 'none'; "
            "base-uri 'none'; form-action 'self'"
        )
        response.headers["Cache-Control"] = "no-store"
        return response

    @app.exception_handler(AppError)
    async def app_error(request: Request, exc: AppError):
        return JSONResponse(
            {"detail": exc.message, "uncertain": exc.uncertain},
            status_code=exc.status_code,
        )

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, exc: RequestValidationError):
        errors = [
            {"field": ".".join(map(str, error["loc"])), "message": error["msg"]}
            for error in exc.errors()
        ]
        return JSONResponse(
            {"detail": "Проверьте входные данные", "errors": errors}, status_code=422
        )

    @app.exception_handler(Exception)
    async def unexpected_error(request: Request, exc: Exception):
        return JSONResponse(
            {
                "detail": "Внутренняя ошибка. "
                "Проверьте журнал операций перед повтором записи"
            },
            status_code=500,
        )

    @app.get("/", include_in_schema=False)
    async def index():
        return FileResponse(STATIC / "index.html")

    @app.get("/api/status")
    async def status():
        error = settings.configuration_error()
        return {
            "demo": settings.demo,
            "connected": error is None,
            "configuration_error": error,
            "creation_error": settings.creation_error(),
            "writes_enabled": settings.demo or settings.writes_enabled,
            "editor_enabled": settings.editor_enabled,
            "prefix": "test_demo_" if settings.demo else settings.campaign_prefix,
        }

    @app.get("/api/countries")
    async def countries():
        return sorted(
            [
                {"id": country.alpha_2, "name": country.name}
                for country in pycountry.countries
            ],
            key=lambda row: row["name"],
        )

    @app.get("/api/catalogs")
    async def catalogs(request: Request):
        return await request.app.state.service.catalogs()

    @app.get("/api/campaigns")
    async def campaigns(request: Request):
        return await request.app.state.service.campaigns()

    @app.post("/api/campaigns")
    async def create_campaign(data: CampaignInput, request: Request):
        operation = await request.app.state.service.create(data)
        return JSONResponse(
            operation_view(operation),
            status_code=201 if operation.status == "complete" else 202,
        )

    @app.get("/api/campaigns/{campaign_id}")
    async def editor(campaign_id: int, request: Request):
        return await request.app.state.service.editor(campaign_id)

    @app.put("/api/campaigns/{campaign_id}/streams/{stream_id}/offers")
    async def update_offers(
        campaign_id: int, stream_id: int, data: OffersInput, request: Request
    ):
        return await request.app.state.service.update_offers(
            campaign_id, stream_id, data
        )

    @app.get("/api/operations")
    async def operations(request: Request):
        return [
            operation_view(row)
            for row in request.app.state.service.repository.list_recent()
        ]

    @app.get("/api/operations/{operation_id}")
    async def operation(operation_id: UUID, request: Request):
        return operation_view(
            request.app.state.service.repository.get(str(operation_id))
        )

    @app.post("/api/operations/{operation_id}/reconcile")
    async def reconcile(operation_id: UUID, request: Request):
        return operation_view(
            await request.app.state.service.reconcile(str(operation_id))
        )

    @app.post("/api/operations/{operation_id}/resume")
    async def resume(operation_id: UUID, request: Request):
        return operation_view(await request.app.state.service.resume(str(operation_id)))

    app.mount("/static", StaticFiles(directory=STATIC), name="static")
    return app


app = create_app()
