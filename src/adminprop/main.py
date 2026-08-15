from fastapi import FastAPI

from adminprop.config import get_settings
from adminprop.modules.administracion.router import (
    organization_settings_router,
    roles_router,
    users_router,
)
from adminprop.modules.auth.router import router as auth_router
from adminprop.modules.health.router import router as health_router
from adminprop.modules.people.router import landlords_router, renters_router
from adminprop.modules.superadmin.router import router as superadmin_router
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
    app.include_router(superadmin_router)
    app.include_router(users_router)
    app.include_router(roles_router)
    app.include_router(organization_settings_router)
    app.include_router(landlords_router)
    app.include_router(renters_router)
    return app


app = create_app()
