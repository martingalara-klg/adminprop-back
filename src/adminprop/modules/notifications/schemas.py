"""Pydantic schemas del panel in-app de notificaciones -- PascalCase
singular (issue #31).

SDD: infrastructure/spec_notificaciones.md RF-02 + core/sdd_02_domain_model.md
§2.16 "Notificacion".
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict

# sdd_02 §2.16 -- los 6 valores del MVP (v1.1, decision #115).
NotificationEventType = Literal[
    "adjustment_pending",
    "contract_expiring",
    "quote_submitted",
    "quote_approved",
    "work_order_created",
    "work_order_closed",
]


class Notification(BaseModel):
    """Fila propia del usuario -- RF-02: "cada aviso lleva el payload
    suficiente para navegar al recurso ... sin queries extra"."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    event_type: NotificationEventType
    payload: dict
    read_at: datetime | None
    created_at: datetime


class NotificationListResponse(BaseModel):
    """`GET /notifications` -- incluye el contador de no leidas (badge,
    RF-02) en `meta` para que el cliente no necesite un segundo request."""

    data: list[Notification]
    meta: dict


class NotificationReadResponse(BaseModel):
    """`POST /notifications/:id/read`."""

    data: Notification


class NotificationReadAllResponse(BaseModel):
    """`POST /notifications/read-all` -- CA-NT-04: "el badge queda en cero"."""

    data: dict
