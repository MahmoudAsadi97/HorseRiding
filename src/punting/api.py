"""Secure, read-only API and server-rendered browser interface."""

from contextlib import asynccontextmanager
from pathlib import Path
from secrets import compare_digest
from threading import Lock
from typing import Annotated
from urllib.parse import urlencode

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.responses import JSONResponse
from fastapi.security import (
    HTTPAuthorizationCredentials,
    HTTPBasic,
    HTTPBasicCredentials,
    HTTPBearer,
)
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import ValidationError
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from punting import queries
from punting.config import Settings

REPORTS = {
    "class-droppers": ("Class droppers", "Class change below the ceiling, with strong key race form."),
    "elite-closers": ("Elite closers", "Tempo-adjusted terminal speed, filtered by running style."),
    "first-up": ("Trainers · first up", "Settled historical starts with a runs-back counter of zero."),
    "backup": ("Trainers · quick backup", "Settled historical starts within the selected backup window."),
}


def create_app(settings: Settings | None = None, engine=None) -> FastAPI:
    """Defer settings and database creation until a request needs them."""
    configured_settings = settings
    configured_engine = engine
    engine_lock = Lock()

    def runtime_settings():
        nonlocal configured_settings
        if configured_settings is None:
            configured_settings = Settings()
        return configured_settings

    def runtime_engine():
        nonlocal configured_engine
        if configured_engine is None:
            with engine_lock:
                if configured_engine is None:
                    from punting.db import make_engine

                    configured_engine = make_engine(runtime_settings().database_url)
        return configured_engine

    @asynccontextmanager
    async def lifespan(application):
        yield
        if engine is None and configured_engine is not None:
            configured_engine.dispose()

    application = FastAPI(title="Punting Warehouse", version="0.1.0", lifespan=lifespan,
                          docs_url=None, redoc_url=None, openapi_url=None)
    assets = Path(__file__).parent
    application.mount("/static", StaticFiles(directory=assets / "static"), name="static")
    templates = Jinja2Templates(directory=assets / "templates")

    def display(value):
        if value is None:
            return "—"
        if isinstance(value, (int, float)):
            return f"{value:,.2f}".rstrip("0").rstrip(".") if isinstance(value, float) else str(value)
        return str(value)

    templates.env.filters["display"] = display
    bearer = HTTPBearer(auto_error=False)
    basic = HTTPBasic(auto_error=False)

    def api_auth(credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)]):
        token = runtime_settings().api_token.get_secret_value()
        if len(token.strip()) < 32:
            raise HTTPException(503, "API authentication is not configured")
        supplied = credentials.credentials if credentials else ""
        if not compare_digest(supplied.encode(), token.encode()):
            raise HTTPException(401, "Authentication required", headers={"WWW-Authenticate": "Bearer"})

    def browser_auth(credentials: Annotated[HTTPBasicCredentials | None, Depends(basic)]):
        config = runtime_settings()
        password = config.admin_password.get_secret_value()
        if not config.admin_username.strip() or len(password.strip()) < 16:
            raise HTTPException(503, "Browser authentication is not configured")
        supplied_user = credentials.username if credentials else ""
        supplied_password = credentials.password if credentials else ""
        user_matches = compare_digest(supplied_user.encode(), config.admin_username.encode())
        password_matches = compare_digest(supplied_password.encode(), password.encode())
        if not (user_matches & password_matches):
            raise HTTPException(401, "Authentication required", headers={
                "WWW-Authenticate": 'Basic realm="Punting Warehouse", charset="UTF-8"',
            })

    def database():
        with Session(runtime_engine()) as session:
            yield session

    @application.middleware("http")
    async def response_security(request, call_next):
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'none'; style-src 'self'; "
            "img-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
        )
        return response

    @application.exception_handler(SQLAlchemyError)
    async def database_error(request, error):
        return JSONResponse(status_code=503, content={"detail": "Warehouse temporarily unavailable"})

    @application.get("/health/live")
    def live():
        return {"status": "ok"}

    @application.get("/health/ready")
    def ready():
        with runtime_engine().connect() as connection:
            connection.execute(text("SELECT 1"))
        return {"status": "ok"}

    api_dependencies = [Depends(api_auth)]

    @application.get("/api/class-droppers", dependencies=api_dependencies)
    def class_droppers(filters: Annotated[queries.QueryFilters, Query()],
                       session: Annotated[Session, Depends(database)]):
        return queries.class_droppers(session, filters)

    @application.get("/api/elite-closers", dependencies=api_dependencies)
    def elite_closers(filters: Annotated[queries.QueryFilters, Query()],
                      session: Annotated[Session, Depends(database)]):
        return queries.elite_closers(session, filters)

    @application.get("/api/trainers/first-up", dependencies=api_dependencies)
    def first_up(filters: Annotated[queries.QueryFilters, Query()],
                 session: Annotated[Session, Depends(database)]):
        return queries.trainer_report(session, filters, pattern="first_up")

    @application.get("/api/trainers/backup", dependencies=api_dependencies)
    def backup(filters: Annotated[queries.QueryFilters, Query()],
               session: Annotated[Session, Depends(database)]):
        return queries.trainer_report(session, filters, pattern="backup")

    @application.get("/api/meetings", dependencies=api_dependencies)
    def meetings(filters: Annotated[queries.QueryFilters, Query()],
                 session: Annotated[Session, Depends(database)]):
        return queries.meetings(session, filters)

    @application.get("/api/runners/{runner_id}/sectionals", dependencies=api_dependencies)
    def sectionals(runner_id: str, filters: Annotated[queries.QueryFilters, Query()],
                   session: Annotated[Session, Depends(database)]):
        result = queries.runner_sectionals(session, runner_id, filters)
        if result is None:
            raise HTTPException(404, "Runner not found within the selected filters")
        return result

    def browser_filters(request):
        data = dict(request.query_params)
        data.pop("report", None)
        if "run_styles" in data:
            data["run_styles"] = request.query_params.getlist("run_styles")
        try:
            return queries.QueryFilters.model_validate(data)
        except ValidationError as error:
            raise HTTPException(422, "Invalid filters: check date order, thresholds and page size") from error

    def page_links(request, result):
        def link(offset):
            values = [(key, value) for key, value in request.query_params.multi_items() if key != "offset"]
            return "?" + urlencode(values + [("offset", offset)])
        return {
            "previous": link(max(0, result["offset"] - result["limit"])) if result["offset"] else None,
            "next": link(result["offset"] + result["limit"])
            if result["offset"] + result["limit"] < result["total"] else None,
        }

    @application.get("/", dependencies=[Depends(browser_auth)])
    def dashboard(request: Request, session: Annotated[Session, Depends(database)]):
        filters = browser_filters(request)
        report = request.query_params.get("report", "class-droppers")
        if report not in REPORTS:
            raise HTTPException(422, "Unknown report")
        if report == "class-droppers":
            result = queries.class_droppers(session, filters)
        elif report == "elite-closers":
            result = queries.elite_closers(session, filters)
        else:
            result = queries.trainer_report(session, filters,
                                            pattern="first_up" if report == "first-up" else "backup")
        return templates.TemplateResponse(request=request, name="dashboard.html", context={
            "report": report, "reports": REPORTS, "filters": filters,
            "result": result, "links": page_links(request, result),
        })

    @application.get("/runners/{runner_id}/sectionals", dependencies=[Depends(browser_auth)],
                     name="sectional_page")
    def sectional_page(request: Request, runner_id: str,
                        session: Annotated[Session, Depends(database)]):
        filters = browser_filters(request)
        result = queries.runner_sectionals(session, runner_id, filters)
        if result is None:
            raise HTTPException(404, "Runner not found within the selected filters")
        return templates.TemplateResponse(request=request, name="sectionals.html", context={
            "result": result, "links": page_links(request, result),
        })

    return application


app = create_app()
