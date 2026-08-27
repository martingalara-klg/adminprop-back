from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from adminprop.config import get_settings
from adminprop.modules.administracion.router import (
    audit_logs_router,
    organization_settings_router,
    roles_router,
    users_router,
)
from adminprop.modules.auth.router import router as auth_router
from adminprop.modules.charges.router import (
    charge_entries_router,
    property_recurring_charges_router,
    recurring_charges_router,
)
from adminprop.modules.contracts.router import adjustments_router
from adminprop.modules.contracts.router import router as contracts_router
from adminprop.modules.health.router import router as health_router
from adminprop.modules.maintenance.router import (
    attachments_router,
    property_work_orders_router,
    quotes_router,
    work_orders_router,
)
from adminprop.modules.notifications.router import router as notifications_router
from adminprop.modules.payments.router import debt_router, payments_root_router
from adminprop.modules.payments.router import router as payments_router
from adminprop.modules.people.router import landlords_router, renters_router
from adminprop.modules.properties.router import (
    neighborhoods_router,
    properties_router,
    service_accounts_router,
)
from adminprop.modules.settlements.router import router as settlements_router
from adminprop.modules.superadmin.router import router as superadmin_router
from adminprop.shared.errors.handlers import register_exception_handlers
from adminprop.shared.logging import RequestContextMiddleware, setup_logging


def create_app() -> FastAPI:
    settings = get_settings()
    setup_logging(settings.service_name, settings.log_level)

    app = FastAPI(title=settings.app_name)
    app.add_middleware(RequestContextMiddleware)
    # issue #90, sdd_04 §2.4a -- CORS deshabilitado por default (lista
    # vacia): agregar el middleware solo con origenes configurados evita
    # tocar el comportamiento actual (dev local usa el proxy de Vite).
    # Se registra despues de RequestContextMiddleware para quedar como
    # capa MAS externa (Starlette envuelve en orden inverso de
    # add_middleware) y asi cubrir preflight y respuestas de error.
    if settings.cors_allowed_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=settings.cors_allowed_origins,  # nunca "*" -- incompatible con credenciales
            allow_credentials=True,
            allow_methods=["GET", "POST", "PATCH", "PUT", "DELETE", "OPTIONS"],
            allow_headers=["Authorization", "Content-Type", "X-Request-Id"],
            expose_headers=["X-Request-Id", "Content-Disposition"],
        )
    register_exception_handlers(app)
    app.include_router(health_router)
    app.include_router(auth_router)
    app.include_router(superadmin_router)
    app.include_router(users_router)
    app.include_router(roles_router)
    app.include_router(organization_settings_router)
    app.include_router(landlords_router)
    app.include_router(renters_router)
    app.include_router(properties_router)
    app.include_router(service_accounts_router)
    app.include_router(neighborhoods_router)
    app.include_router(contracts_router)
    app.include_router(adjustments_router)
    app.include_router(payments_router)
    app.include_router(payments_root_router)
    app.include_router(debt_router)
    app.include_router(work_orders_router)
    app.include_router(quotes_router)
    app.include_router(attachments_router)
    app.include_router(property_work_orders_router)
    app.include_router(property_recurring_charges_router)
    app.include_router(recurring_charges_router)
    app.include_router(charge_entries_router)
    app.include_router(settlements_router)
    app.include_router(notifications_router)
    app.include_router(audit_logs_router)
    return app


app = create_app()
