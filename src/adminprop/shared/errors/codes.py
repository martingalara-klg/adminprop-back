"""Excepciones de dominio -- una subclase por `error.code` (issue #6).

SDD: core/sdd_03_api_contracts.md parrafo "Codigos de Error Globales".
Solo se declaran aca los codigos *transversales* que ya tienen un
consumidor real en este issue (auth). El resto del catalogo (CONTRACT_OVERLAP,
RENT_PERIOD_ALREADY_PAID, etc.) se agrega en el issue que primero los
necesite -- inventar codigos sin uso no aporta y diverge del principio de
"no hay codigo fuera de sdd_03 sin implementacion real".
"""

from __future__ import annotations

from adminprop.shared.errors.base import AdminPropException


class ValidationError(AdminPropException):
    status_code = 400
    error_code = "VALIDATION_ERROR"
    message = "Los datos enviados no son validos."


class UnauthorizedException(AdminPropException):
    """sdd_03 parrafo "Codigos de Error Globales" -- 401 UNAUTHORIZED.

    Usada tanto para "credenciales incorrectas" (login) como para
    "token ausente/expirado/invalido" (logout, refresh) -- mensaje
    default generico; el caller decide el mensaje literal cuando el SDD
    lo exige (sdd_04 §2.2a anti-enumeration).
    """

    status_code = 401
    error_code = "UNAUTHORIZED"
    message = "Token ausente, expirado o invalido."


class AccountLockedException(AdminPropException):
    """sdd_03 -- 403 ACCOUNT_LOCKED, countdown en `details.retry_after_seconds`."""

    status_code = 403
    error_code = "ACCOUNT_LOCKED"
    message = "Cuenta bloqueada temporalmente por demasiados intentos fallidos."


class ForbiddenException(AdminPropException):
    status_code = 403
    error_code = "FORBIDDEN"
    message = "No tenes permiso para realizar esta accion."


class MembershipInactiveException(AdminPropException):
    """sdd_03 -- 403 MEMBERSHIP_INACTIVE.

    RN-D01/CLAUDE.md §4: un JWT valido no alcanza -- la membresia activa
    en una organizacion activa se verifica en login/refresh (tenant-isolation.md
    "Validar que el JWT corresponde a un miembro activo del tenant").
    """

    status_code = 403
    error_code = "MEMBERSHIP_INACTIVE"
    message = "Tu acceso a esta organizacion no esta activo. Contacta a un administrador."


class NotFoundException(AdminPropException):
    status_code = 404
    error_code = "NOT_FOUND"
    message = "El recurso solicitado no existe."


class RateLimitExceededException(AdminPropException):
    """sdd_03 -- 429 RATE_LIMIT_EXCEEDED, header `Retry-After` (sdd_04 §2.5)."""

    status_code = 429
    error_code = "RATE_LIMIT_EXCEEDED"
    message = "Demasiadas solicitudes. Espera unos segundos e intenta nuevamente."


class InternalError(AdminPropException):
    status_code = 500
    error_code = "INTERNAL_ERROR"
    message = "Ocurrio un error inesperado. El equipo fue notificado."


class SuperAdminRequiredException(AdminPropException):
    """sdd_03 §2 -- 403 SUPERADMIN_REQUIRED.

    CA-00-05 (spec_module_00_superadmin.md): un usuario owner/admin/
    maintenance que intenta acceder a `/superadmin/*` recibe este error;
    el intento queda auditado en `audit_logs` (RN-A04, issue #10, ver
    `shared/auth/dependencies.py.requires_super_admin`).
    """

    status_code = 403
    error_code = "SUPERADMIN_REQUIRED"
    message = "Esta accion requiere permisos de Super Admin."


class InvitationNotFoundException(AdminPropException):
    """sdd_03 §"Codigos de Error Globales" -- 404 INVITATION_NOT_FOUND.

    Issue #8 (spec_module_00_superadmin.md "Flujo de Activacion de
    Cuenta" paso 2): token de invitacion desconocido, o cuyo estado
    (`revoked`) no debe distinguirse de "no existe" para no revelar
    informacion del ciclo de vida de la invitacion.
    """

    status_code = 404
    error_code = "INVITATION_NOT_FOUND"
    message = "La invitacion no existe o ya no es valida."


class InvitationExpiredException(AdminPropException):
    """sdd_03 -- 410 INVITATION_EXPIRED (issue #8, GET/POST invitation)."""

    status_code = 410
    error_code = "INVITATION_EXPIRED"
    message = "La invitacion expiro. Pedile a un administrador que te reenvie una nueva."


class InvitationAlreadyAcceptedException(AdminPropException):
    """sdd_03 -- 409 INVITATION_ALREADY_ACCEPTED (issue #8)."""

    status_code = 409
    error_code = "INVITATION_ALREADY_ACCEPTED"
    message = "Esta invitacion ya fue utilizada."


class UserAlreadyMemberException(AdminPropException):
    """sdd_03 §"Codigos de Error Globales" -- 409 USER_ALREADY_MEMBER.

    Issue #8: `accept-invitation` con un email que ya es user global Y ya
    tiene membresia (activa o inactiva) en la organizacion de la
    invitacion -- decision de implementacion del issue (ver PR): se
    modela como conflicto explicito en vez de reactivar la membresia en
    silencio.
    """

    status_code = 409
    error_code = "USER_ALREADY_MEMBER"
    message = "Este usuario ya es miembro de la organizacion."


class ResetTokenExpiredException(AdminPropException):
    """Issue #8 -- 410, agregado a sdd_03 §"Codigos de Error Globales" en
    este mismo PR (regla de oro: el SDD se actualiza antes que el codigo).

    Equivalente de INVITATION_EXPIRED pero para
    GET/POST /auth/reset-password/:token: el token de reset existio (a
    diferencia de NOT_FOUND, generico y ya en catalogo, para "nunca
    existio / ya fue consumido") pero su ventana de 1h ya paso.
    """

    status_code = 410
    error_code = "RESET_TOKEN_EXPIRED"
    message = "El link para restablecer tu contrasena vencio. Pedi uno nuevo."


class InvitationPendingExistsException(AdminPropException):
    """sdd_03 §"Codigos de Error Globales" -- 409 INVITATION_PENDING_EXISTS.

    RF-03 (spec_module_00_superadmin.md): solo puede existir una invitacion
    de owner `pending` por organizacion. `POST .../invite-owner` la levanta
    si ya hay una pendiente -- el caller debe usar
    `POST .../resend-invitation` (que revoca la anterior automaticamente).
    """

    status_code = 409
    error_code = "INVITATION_PENDING_EXISTS"
    message = "Ya existe una invitacion de owner pendiente para esta organizacion."


class LastOwnerRequiredException(AdminPropException):
    """sdd_03 §"Codigos de Error Globales" -- 422 LAST_OWNER_REQUIRED.

    Issue #9 (spec_module_07_administracion.md RN-02/RN-A03): la
    organizacion siempre debe tener al menos un owner activo -- se
    levanta al intentar desactivar o cambiarle el rol al ultimo owner
    activo.
    """

    status_code = 422
    error_code = "LAST_OWNER_REQUIRED"
    message = "La organizacion debe tener al menos un owner activo."


class SystemRoleImmutableException(AdminPropException):
    """sdd_03 §"Codigos de Error Globales" -- 422 SYSTEM_ROLE_IMMUTABLE.

    Issue #9 (spec_module_07_administracion.md RF-03/RN-03): los roles de
    sistema (`owner`, `admin`, `maintenance`) son inmutables en el MVP --
    no hay endpoint de escritura de roles todavia (`GET /roles` es solo
    lectura), pero la invariante se documenta aca para que cualquier
    endpoint futuro la reutilice.
    """

    status_code = 422
    error_code = "SYSTEM_ROLE_IMMUTABLE"
    message = "Los roles de sistema no se pueden editar."


class RoleNotFoundException(AdminPropException):
    """sdd_03 §"Codigos de Error Globales" -- 404 ROLE_NOT_FOUND.

    Issue #9: defensivo -- el nombre de rol (`admin`/`maintenance`) no
    existe para la organizacion del JWT (no deberia pasar nunca, los 3
    roles de sistema se siembran en la creacion de la organizacion).
    """

    status_code = 404
    error_code = "ROLE_NOT_FOUND"
    message = "El rol solicitado no existe."


class ContractOverlapException(AdminPropException):
    """sdd_03 §"Codigos de Error Globales" -- 409 CONTRACT_OVERLAP.

    Issue #17 (spec_module_03_contratos.md RF-02/RF-03, RN-01/RN-C01): una
    propiedad no puede tener dos contratos `active` con vigencias
    superpuestas. Se levanta tanto al crear como al activar
    (`details.conflicting_contract_id` identifica el contrato en
    conflicto) -- validacion app-level ANTES del EXCLUDE de DB (que es la
    red de seguridad, no la UX, ver `modules/contracts/repository.py`).
    """

    status_code = 409
    error_code = "CONTRACT_OVERLAP"
    message = "La propiedad ya tiene un contrato vigente en ese rango de fechas."


class ContractNotActiveException(AdminPropException):
    """sdd_03 §"Codigos de Error Globales" -- 422 CONTRACT_NOT_ACTIVE.

    Issue #17: `POST /contracts/:id/terminate` sobre un contrato que no
    esta `active` (ya `terminated`/`expired`, o todavia `draft`).
    """

    status_code = 422
    error_code = "CONTRACT_NOT_ACTIVE"
    message = "El contrato no esta activo."


class InvalidStatusTransitionException(AdminPropException):
    """sdd_03 §"Codigos de Error Globales" -- 422 INVALID_STATUS_TRANSITION.

    Issue #17: `POST /contracts/:id/activate` sobre un contrato que no
    esta `draft` (RF-03: `draft -> active` es la unica transicion valida
    para este endpoint).
    """

    status_code = 422
    error_code = "INVALID_STATUS_TRANSITION"
    message = "La transicion de estado solicitada no es valida."


class BusinessRuleViolationException(AdminPropException):
    """sdd_03 §"Codigos de Error Globales" -- 422 BUSINESS_RULE_VIOLATION.

    Issue #17 (RN-C04/RN-04, CA-03-06): `PATCH /contracts/:id` nunca
    acepta cambios de `current_amount` -- el monto vigente solo cambia via
    un ajuste registrado (`ContractAdjustment`, fuera de alcance de este
    issue, ver #18).
    """

    status_code = 422
    error_code = "BUSINESS_RULE_VIOLATION"
    message = "La operacion viola una regla de negocio."


class AdjustmentPendingExistsException(AdminPropException):
    """sdd_03 §"Codigos de Error Globales" -- 409 ADJUSTMENT_PENDING_EXISTS.

    Issue #18 (spec_module_03_contratos.md RF-04, sdd_02 §2.8): no puede
    haber dos ajustes `pending` del mismo contrato -- el indice parcial
    unico `idx_contract_adjustments_one_pending_per_contract` (migracion
    #16) es la red de seguridad; esta excepcion es la validacion
    app-level defensiva que usa el job `detect_due_adjustments` antes de
    insertar (mismo criterio que `ContractOverlapException` con el
    EXCLUDE constraint de `contracts`).
    """

    status_code = 409
    error_code = "ADJUSTMENT_PENDING_EXISTS"
    message = "Ya existe un ajuste pendiente para este contrato."


class AdjustmentAlreadyAppliedException(AdminPropException):
    """sdd_03 §"Codigos de Error Globales" -- 409 ADJUSTMENT_ALREADY_APPLIED.

    Issue #18: `POST /adjustments/:id/apply` sobre un ajuste que ya esta
    `applied` -- inmutable (sdd_02 §2.8): una correccion es un ajuste
    nuevo con nota, nunca reabrir uno ya aplicado.
    """

    status_code = 409
    error_code = "ADJUSTMENT_ALREADY_APPLIED"
    message = "El ajuste ya fue aplicado y es inmutable."


class AdjustmentPctRequiredException(AdminPropException):
    """sdd_03 §"Codigos de Error Globales" -- 400 ADJUSTMENT_PCT_REQUIRED.

    Issue #18: `pct` se acepta a nivel de schema como opcional (mismo
    criterio que `ContractUpdate.current_amount`, issue #17) para poder
    distinguir "el cliente no mando `pct`" -> 400 ADJUSTMENT_PCT_REQUIRED
    en vez de un generico 422 VALIDATION_ERROR de Pydantic por
    `extra="forbid"`/campo requerido.
    """

    status_code = 400
    error_code = "ADJUSTMENT_PCT_REQUIRED"
    message = "El porcentaje de ajuste es obligatorio."


class ExchangeRateRequiredException(AdminPropException):
    """sdd_03 §"Codigos de Error Globales" -- 400 EXCHANGE_RATE_REQUIRED.

    Issue #22 (spec_module_04_cobranzas.md RF-03, RN-P06): un cobro cuya
    `payment_currency` difiere de la moneda del contrato exige
    `exchange_rate` -- validacion app-level (el CHECK de la migracion #20
    solo exige `exchange_rate > 0` cuando no es NULL, no la obligatoriedad
    condicional, que requiere leer `contracts.currency`).
    """

    status_code = 400
    error_code = "EXCHANGE_RATE_REQUIRED"
    message = "Se requiere el tipo de cambio porque la moneda del pago difiere de la del contrato."


class PaymentExceedsContractBalanceException(AdminPropException):
    """sdd_03 §"Codigos de Error Globales" -- 422 PAYMENT_EXCEEDS_CONTRACT_BALANCE.

    Issue #22 (RF-03, RN-P05): el capital imputado (`amount`) de un cobro
    no puede superar el saldo impago del periodo (`amount_due - paid_total`).
    """

    status_code = 422
    error_code = "PAYMENT_EXCEEDS_CONTRACT_BALANCE"
    message = "El monto del cobro excede el saldo pendiente del periodo."


class RentPeriodAlreadyPaidException(AdminPropException):
    """sdd_03 §"Codigos de Error Globales" -- 422 RENT_PERIOD_ALREADY_PAID.

    Issue #22 (RF-03): `POST /rent-periods/:id/payments` sobre un periodo
    cuyo `status` ya es `paid` -- no admite mas imputaciones.
    """

    status_code = 422
    error_code = "RENT_PERIOD_ALREADY_PAID"
    message = "El periodo ya fue pagado en su totalidad."


class EntityHasDependenciesException(AdminPropException):
    """sdd_03 §"Codigos de Error Globales" -- 409 ENTITY_HAS_DEPENDENCIES.

    Issue #13 (spec_module_02_personas.md RF-01/RF-03, CA-02-06): baja de
    un `landlord` con propiedades activas o de un `renter` con contrato
    vigente. Codigo transversal (ya listado en el catalogo de sdd_03 desde
    la version inicial); esta es la primera subclase Python concreta --
    mismo criterio documentado en el encabezado de este archivo ("se
    agrega en el issue que primero lo necesite").
    """

    status_code = 409
    error_code = "ENTITY_HAS_DEPENDENCIES"
    message = "El recurso tiene dependencias activas y no puede eliminarse."
