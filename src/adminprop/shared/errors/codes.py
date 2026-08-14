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
    el intento queda auditado (TODO(#10): audit_logs todavia no existe).
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
