"""Excepciones de dominio del modulo mantenimiento (issue #26).

SDD: core/sdd_03_api_contracts.md §"Codigos de Error Globales" ->
"Mantenimiento": `WORK_ORDER_ALREADY_CLOSED` (409), `WORK_ORDER_ALREADY_SETTLED`
(422), `QUOTE_ALREADY_APPROVED` (409). Los demas codigos que este modulo
necesita (`NOT_FOUND`, `VALIDATION_ERROR`, `INVALID_STATUS_TRANSITION`) ya
viven en `shared/errors/codes.py` -- se reutilizan, mismo criterio que
`modules/people/exceptions.py`.
"""

from __future__ import annotations

from adminprop.shared.errors.base import AdminPropException


class WorkOrderAlreadyClosedException(AdminPropException):
    """sdd_03 §"Codigos de Error Globales" -- 409 WORK_ORDER_ALREADY_CLOSED.

    RF-04 (spec_module_06_mantenimiento.md): "Cerrar un pedido ya cerrado
    -> 409 WORK_ORDER_ALREADY_CLOSED" (CA-06-04 solo cubre el cierre
    exitoso; este es el flujo alternativo explicito del RF)."""

    status_code = 409
    error_code = "WORK_ORDER_ALREADY_CLOSED"
    message = "El pedido de reparacion ya esta cerrado."


class WorkOrderAlreadySettledException(AdminPropException):
    """sdd_03 §"Codigos de Error Globales" -- 422 WORK_ORDER_ALREADY_SETTLED.

    RF-05/CA-06-07: "Un pedido closed ya liquidado no puede cancelarse ni
    reabrirse". Ver `settlement_hook.py` para la aproximacion documentada
    de "ya liquidado" mientras la Capa 6 (issue #27) no exista."""

    status_code = 422
    error_code = "WORK_ORDER_ALREADY_SETTLED"
    message = "El pedido ya fue cerrado y liquidado: no puede cancelarse ni reabrirse."


class QuoteAlreadyApprovedException(AdminPropException):
    """sdd_03 §"Codigos de Error Globales" -- 409 QUOTE_ALREADY_APPROVED.

    RF-03/CA-06-03: "Aprobar sobre un pedido que ya tiene aprobada ->
    409 QUOTE_ALREADY_APPROVED" (RN-02: una sola cotizacion `approved`
    por pedido, indice parcial unico de la migracion #25)."""

    status_code = 409
    error_code = "QUOTE_ALREADY_APPROVED"
    message = "El pedido ya tiene una cotizacion aprobada."
