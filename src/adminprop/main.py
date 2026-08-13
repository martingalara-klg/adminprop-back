from fastapi import FastAPI

from adminprop.config import get_settings
from adminprop.modules.auth.router import router as auth_router
from adminprop.modules.health.router import router as health_router
from adminprop.shared.errors.handlers import register_exception_handlers
from adminprop.shared.logging import RequestContextMiddleware, setup_logging


def create_app() -> FastAPI:
    settings = get_settings()
    setup_logging(settings.service_name, settings.log_level)

    app = FastAPI(title=settings.app_name)
    app.add_middleware(RequestContextMiddleware)
    register_exception_handlers(app)
    app.include_router(health_router)
    app.include_router(auth_router)
    return app


app = create_app()
