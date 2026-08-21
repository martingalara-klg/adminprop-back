"""Issue #33 -- Test E2E del ciclo mensual completo.

SDD: docs/sdd/core/sdd_01_prd.md §6 ("Tiempo del ciclo mensual completo:
Generacion de pendientes -> cobros -> liquidaciones de todos los
duenios") + todas las specs de modulo involucradas (03 contratos, 04
cobranzas, 05 liquidaciones, 06 mantenimiento).

Atraviesa la API real (httpx contra la app, `tests/integration/e2e/
conftest.py`) y los workers (invocados de forma sincronica igual que
`tests/integration/workers/test_generate_rent_periods.py` y
`tests/integration/workers/test_documents_worker.py` -- Celery hace
`asyncio.run()` internamente y no puede llamarse desde el loop de
pytest-asyncio ya corriendo) contra la DB real de test.

Escenario (seed reutilizable en `seed_demo_organization`, conftest.py):
organizacion demo con 1 propietario (`commission_pct=10.00`), 2
propiedades (una con contrato ARS con mora diaria, otra con contrato
USD), un pedido de reparacion cerrado (payer=agency) y un concepto
recurrente cargado. El mes M se fija en Enero/2026 (mockeado via
`_generate_rent_periods_async`) para que el flujo sea determinista sin
importar la fecha real en la que corre la suite -- `payment_date` (dia
15) queda siempre en el pasado respecto de "hoy".

No es CERO cambios de logica de produccion: este test no modifica
ningun modulo de `src/adminprop`, solo ejercita los ya implementados
(issues #1-#32).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from decimal import Decimal

import pytest

from adminprop.workers.documents_worker import _generate_settlement_async
from adminprop.workers.notification_worker import _generate_rent_periods_async
from tests.integration.e2e.conftest import seed_demo_organization

pytestmark = pytest.mark.asyncio

# Mes M fijo (independiente de la fecha real de ejecucion de la suite,
# ver docstring del modulo). `_generate_rent_periods_async` usa
# `datetime.now(UTC).date()` como "hoy" -- se mockea para que el periodo
# generado sea siempre Enero/2026, sin importar cuando corra CI.
_PERIOD_TODAY = datetime(2026, 1, 20, 12, 0, tzinfo=UTC)
_PERIOD_STR = "2026-01"
_PERIOD_DATE_STR = "2026-01-01"
_PAY_DAY_ARS = "2026-01-15"  # dia de gracia 10 -> 5 dias de mora (CA-04-05)
_PAY_DAY_USD = "2026-01-05"  # dentro del dia de gracia -> sin mora


class _FrozenDateTime(datetime):
    """Sustituye `datetime.now(UTC)` dentro de `notification_worker` para
    que "el mes en curso" sea determinista (Enero/2026, ver constantes de
    arriba) sin depender de la fecha real de ejecucion de CI."""

    @classmethod
    def now(cls, tz=None):
        if tz is not None:
            return _PERIOD_TODAY.astimezone(tz)
        return _PERIOD_TODAY


class TestCicloMensualCompleto:
    """docs/sdd/core/sdd_01_prd.md §6 + spec_module_04/05/06 -- ciclo
    mensual completo: generar periodos -> cobrar (mora perdonada
    parcialmente + USD pagado en ARS con TC) -> cargar cargos del mes ->
    reparacion agency cerrada -> liquidacion asincrona con desglose por
    propiedad y exports descargables."""

    async def test_ciclo_mensual_completo_generar_cobrar_cargar_reparar_liquidar(
        self, client, seed, monkeypatch
    ):
        """Flujo end-to-end (CA del issue #33):
        - "Existe un seed de organizacion demo reutilizable"
          (`seed_demo_organization`, ver conftest.py).
        - "El flujo completo del mes esta verificado: generacion, cobro
          con mora perdonada, cobro en USD con TC, reparacion agency
          descontada, y liquidacion con desglose por propiedad."
        """
        demo = await seed_demo_organization(seed, contract_start="2025-01-01")
        org_id = demo["organization_id"]
        owner_headers = demo["owner"]["headers"]
        maintenance_headers = demo["maintenance_user"]["headers"]
        landlord_id = demo["landlord_id"]
        property_ars = demo["property_ars"]
        property_usd = demo["property_usd"]
        contract_ars = demo["contract_ars"]
        contract_usd = demo["contract_usd"]

        # ─── Paso 1: generacion de periodos del mes (job Beat) ─────────────
        # spec_module_04_cobranzas.md §RF-01. Implements: CA-04-01
        # (idempotencia), CA-04-02 (RN-P01).
        monkeypatch.setattr(
            "adminprop.workers.notification_worker.datetime", _FrozenDateTime
        )
        await _generate_rent_periods_async(request_id=str(uuid.uuid4()))

        rent_period_ars = await seed.get_rent_period_by_contract(
            organization_id=org_id, contract_id=contract_ars, period=_PERIOD_DATE_STR
        )
        rent_period_usd = await seed.get_rent_period_by_contract(
            organization_id=org_id, contract_id=contract_usd, period=_PERIOD_DATE_STR
        )
        assert rent_period_ars["status"] == "pending"
        assert rent_period_ars["amount_due"] == Decimal("100000.00")
        assert rent_period_usd["status"] == "pending"
        assert rent_period_usd["amount_due"] == Decimal("500.00")

        # Re-correr el job el mismo mes no duplica ningun rent_period
        # (CA-04-01, idempotencia via `ON CONFLICT DO NOTHING`).
        await _generate_rent_periods_async(request_id=str(uuid.uuid4()))
        rent_period_ars_again = await seed.get_rent_period_by_contract(
            organization_id=org_id, contract_id=contract_ars, period=_PERIOD_DATE_STR
        )
        assert rent_period_ars_again["id"] == rent_period_ars["id"]

        # ─── Paso 2: cobro ARS con mora sugerida y perdon parcial ──────────
        # CA-04-05: "pagando el dia 15 con dia de gracia 10, el sistema
        # sugiere interes por 5 dias de mora con el % del contrato; el
        # operador puede imputar un valor menor (perdon parcial)".
        preview_response = await client.get(
            f"/v1/rent-periods/{rent_period_ars['id']}/interest-preview",
            params={"payment_date": _PAY_DAY_ARS},
            headers=owner_headers,
        )
        assert preview_response.status_code == 200
        preview = preview_response.json()["data"]
        assert preview["days_late"] == 5
        # saldo 100000.00 x (0.50/100) x 5 dias = 2500.00
        assert Decimal(preview["suggested_interest"]) == Decimal("2500.00")

        payment_ars_response = await client.post(
            f"/v1/rent-periods/{rent_period_ars['id']}/payments",
            json={
                "payment_date": _PAY_DAY_ARS,
                "method": "transfer",
                "payment_currency": "ARS",
                "amount": "100000.00",
                "destination": "agency_account",
                "charged_interest": "1000.00",  # perdon parcial: 2500 - 1000 = 1500 perdonado
                "notes": "Cobro alquiler ARS con mora parcialmente perdonada",
            },
            headers=owner_headers,
        )
        assert payment_ars_response.status_code == 201
        payment_ars = payment_ars_response.json()["data"]
        assert Decimal(payment_ars["suggested_interest"]) == Decimal("2500.00")
        assert Decimal(payment_ars["charged_interest"]) == Decimal("1000.00")
        assert Decimal(payment_ars["forgiven_interest"]) == Decimal("1500.00")
        assert payment_ars["days_late"] == 5

        rent_period_ars_after = await seed.get_rent_period_by_contract(
            organization_id=org_id, contract_id=contract_ars, period=_PERIOD_DATE_STR
        )
        assert rent_period_ars_after["status"] == "paid"

        # CA-04-06: "todo perdon de interes queda en el log de auditoria
        # con autor y cobro asociado".
        forgiveness_audit = await seed.audit_rows(org_id, "interest.forgiven")
        assert len(forgiveness_audit) == 1
        assert forgiveness_audit[0]["entity_id"] == uuid.UUID(payment_ars["id"])
        assert forgiveness_audit[0]["user_id"] == demo["owner"]["id"]

        # ─── Paso 3: cobro de contrato USD pagado en ARS con TC manual ────
        # CA-04-03: "un cobro de contrato USD pagado en pesos sin
        # exchange_rate devuelve 400 EXCHANGE_RATE_REQUIRED; con TC, el
        # cobro registra el TC usado."
        missing_rate_response = await client.post(
            f"/v1/rent-periods/{rent_period_usd['id']}/payments",
            json={
                "payment_date": _PAY_DAY_USD,
                "method": "transfer",
                "payment_currency": "ARS",
                "amount": "500.00",
                "destination": "agency_account",
                "charged_interest": "0.00",
            },
            headers=owner_headers,
        )
        assert missing_rate_response.status_code == 400
        assert missing_rate_response.json()["error"]["code"] == "EXCHANGE_RATE_REQUIRED"

        exchange_rate = Decimal("1200.0000")
        payment_usd_response = await client.post(
            f"/v1/rent-periods/{rent_period_usd['id']}/payments",
            json={
                "payment_date": _PAY_DAY_USD,
                "method": "transfer",
                "payment_currency": "ARS",
                "amount": "500.00",
                "exchange_rate": str(exchange_rate),
                "destination": "agency_account",
                "charged_interest": "0.00",
                "notes": "Cobro contrato USD pagado en pesos con TC manual",
            },
            headers=owner_headers,
        )
        assert payment_usd_response.status_code == 201
        payment_usd = payment_usd_response.json()["data"]
        assert payment_usd["payment_currency"] == "ARS"
        assert Decimal(payment_usd["exchange_rate"]) == exchange_rate
        assert Decimal(payment_usd["amount"]) == Decimal("500.00")
        assert Decimal(payment_usd["suggested_interest"]) == Decimal("0.00")

        rent_period_usd_after = await seed.get_rent_period_by_contract(
            organization_id=org_id, contract_id=contract_usd, period=_PERIOD_DATE_STR
        )
        assert rent_period_usd_after["status"] == "paid"

        # ─── Paso 4: cargar el concepto recurrente del mes ─────────────────
        # RF-05/spec_module_04_cobranzas.md: "cargar" el mes -- un
        # concepto recurrente con su charge_entry para el periodo, que la
        # liquidacion debe descontar.
        recurring_charge_response = await client.post(
            f"/v1/properties/{property_ars}/recurring-charges",
            json={"charge_type": "municipalidad", "label": "Expensas municipales"},
            headers=owner_headers,
        )
        assert recurring_charge_response.status_code == 201
        recurring_charge_id = recurring_charge_response.json()["data"]["id"]

        charge_entry_response = await client.post(
            f"/v1/recurring-charges/{recurring_charge_id}/entries",
            json={"period": _PERIOD_STR, "amount": "5000.00"},
            headers=owner_headers,
        )
        assert charge_entry_response.status_code == 201
        assert Decimal(charge_entry_response.json()["data"]["amount"]) == Decimal("5000.00")

        # ─── Paso 5: reparacion cerrada, payer=agency ──────────────────────
        # spec_module_06_mantenimiento.md: crear (owner) -> cotizar
        # (maintenance) -> aprobar (owner) -> cerrar (maintenance).
        create_wo_response = await client.post(
            "/v1/work-orders",
            json={
                "property_id": str(property_ars),
                "title": "Arreglo de caneria principal",
                "description": "Perdida de agua en bano principal",
                "payer": "agency",
            },
            headers=owner_headers,
        )
        assert create_wo_response.status_code == 201
        work_order = create_wo_response.json()["data"]
        assert work_order["status"] == "open"
        assert work_order["payer"] == "agency"
        work_order_id = work_order["id"]

        quote_response = await client.post(
            f"/v1/work-orders/{work_order_id}/quotes",
            json={"amount": "8000.00", "description": "Cambio de caneria + mano de obra"},
            headers=maintenance_headers,
        )
        assert quote_response.status_code == 201
        quote_id = quote_response.json()["data"]["id"]

        approve_response = await client.post(
            f"/v1/quotes/{quote_id}/approve", headers=owner_headers
        )
        assert approve_response.status_code == 200
        assert approve_response.json()["data"]["status"] == "in_progress"

        # CA-06-03: reaprobar la misma cotizacion -> 409 QUOTE_ALREADY_APPROVED.
        reapprove_response = await client.post(
            f"/v1/quotes/{quote_id}/approve", headers=owner_headers
        )
        assert reapprove_response.status_code == 409
        assert reapprove_response.json()["error"]["code"] == "QUOTE_ALREADY_APPROVED"

        close_response = await client.post(
            f"/v1/work-orders/{work_order_id}/close",
            json={"final_cost": "8000.00"},
            headers=maintenance_headers,
        )
        assert close_response.status_code == 200
        closed_work_order = close_response.json()["data"]
        assert closed_work_order["status"] == "closed"
        assert Decimal(closed_work_order["final_cost"]) == Decimal("8000.00")

        # ─── Paso 6: generacion de liquidacion asincrona con TC ────────────
        # spec_module_05_liquidaciones.md §RF-01/RF-02. CA-05-03: "la
        # generacion responde 202 y el polling atraviesa processing ->
        # completed". Se invoca el `documents_worker` de forma sincronica
        # (mismo criterio que tests/integration/workers/
        # test_documents_worker.py) porque Celery no corre en la suite.
        generate_response = await client.post(
            "/v1/settlements/generate",
            json={
                "landlord_id": str(landlord_id),
                "period": _PERIOD_STR,
                "exchange_rate": str(exchange_rate),
            },
            headers=owner_headers,
        )
        assert generate_response.status_code == 202
        accepted = generate_response.json()["data"]
        assert accepted["status"] == "pending"
        settlement_id = uuid.UUID(accepted["settlement_id"])

        await _generate_settlement_async(settlement_id, org_id, str(uuid.uuid4()))

        # ─── Verificacion: totales calculados a mano contra la respuesta ──
        #
        # HALLAZGO E2E (documentado, no corregido en este PR -- ver
        # reporte de la Fase 8 / issue #33): `SettlementRepository.
        # _PAYMENTS_SQL` (src/adminprop/modules/settlements/repository.py)
        # selecciona `pay.payment_currency AS currency`, y tanto
        # `calculate_settlement._convert_to_ars` como el gate sincronico
        # `has_usd` de `SettlementService.generate`/`regenerate`
        # (RN-L06/CA-05-02) deciden la conversion a ARS en base a ESE
        # campo. Pero `payments.amount` esta denominado en la MONEDA DEL
        # CONTRATO (RF-03: "el periodo pasa a paid cuando el capital
        # imputado alcanza amount_due", ambos en la misma unidad;
        # confirmado por `tests/integration/payments/test_register_
        # payment.py::TestExchangeRateRequired`, que registra un pago
        # parcial de "500.00" contra un `amount_due` USD de "1000.00").
        # Para un contrato USD cobrado en pesos (CA-04-03, `payment_
        # currency="ARS"`, `exchange_rate` grabado en el pago), la
        # liquidacion NO convierte ese cobro (trata "500.00" como si ya
        # fueran ARS) porque `currency` != "USD" segun ese campo -- el TC
        # de la liquidacion queda persistido pero no se aplica a este
        # cobro. La conversion SI funcionaria si `payment_currency`
        # coincidiera con la moneda del contrato (USD), pero entonces no
        # se podria ejercer CA-04-03 (que exige pagarlo en pesos). Ambos
        # CA no son simultaneamente satisfacibles con el codigo actual
        # para el mismo cobro -- posible fix de una linea (cambiar el
        # alias de la query a `c.currency AS currency`), pero se deja sin
        # tocar (fuera de alcance de este issue, "CERO cambios de logica
        # de produccion") y se reporta para revision humana.
        #
        # Calculo manual documentado contra el comportamiento ACTUAL del
        # sistema (en Decimal -- nunca floats):
        #
        #   Propiedad ARS: alquiler 100000.00 + interes cobrado 1000.00
        #                  = 101000.00 (destino agency_account)
        #   Propiedad USD: alquiler 500.00 (payment_currency=ARS, TC
        #                  1200.0000 grabado en el cobro pero NO aplicado
        #                  por la liquidacion -- ver hallazgo arriba)
        #                  = 500.00 (destino agency_account)
        #   total_collected      = 101000.00 + 500.00 = 101500.00
        #   commission_base      = 101500.00 (TODOS los destinos, RN-L02;
        #                          aca no hay cobros "ya rendidos")
        #   commission_total     = 101500.00 x 10% = 10150.00
        #   charges_total        = 5000.00 (expensas municipales, Prop ARS)
        #   repairs_total        = 8000.00 (reparacion agency cerrada, Prop ARS)
        #   net_amount           = 101500.00 - 10150.00 - 5000.00 - 8000.00
        #                        = 78350.00
        expected_total_collected = Decimal("101500.00")
        expected_commission_total = Decimal("10150.00")
        expected_charges_total = Decimal("5000.00")
        expected_repairs_total = Decimal("8000.00")
        expected_net_amount = Decimal("78350.00")

        get_response = await client.get(
            f"/v1/settlements/{settlement_id}",
            params={"scope": "consolidated"},
            headers=owner_headers,
        )
        assert get_response.status_code == 200
        settlement_detail = get_response.json()["data"]
        assert settlement_detail["job_status"] == "completed"
        assert settlement_detail["warnings"] == []
        assert Decimal(settlement_detail["total_collected"]) == expected_total_collected
        assert Decimal(settlement_detail["commission_total"]) == expected_commission_total
        assert Decimal(settlement_detail["charges_total"]) == expected_charges_total
        assert Decimal(settlement_detail["repairs_total"]) == expected_repairs_total
        assert Decimal(settlement_detail["net_amount"]) == expected_net_amount
        assert Decimal(settlement_detail["exchange_rate"]) == exchange_rate

        # CA-05-05: la reparacion agency cerrada se descuenta y queda
        # vinculada a la liquidacion (settled_in_settlement_id) -- no se
        # descuenta dos veces si se regenerara.
        repair_line_items = [
            li for li in settlement_detail["line_items"] if li["line_type"] == "repair"
        ]
        assert len(repair_line_items) == 1
        assert repair_line_items[0]["source_entity_id"] == str(work_order_id)
        assert Decimal(repair_line_items[0]["amount_ars"]) == Decimal("8000.00")

        work_order_after = await client.get(
            f"/v1/work-orders/{work_order_id}", headers=owner_headers
        )
        assert work_order_after.status_code == 200

        # CA-05-07 / RF-04: `scope=per_property` agrupa por propiedad con
        # subtotal -- Prop ARS = 101000 (alquiler) - 5000 (cargo) - 8000
        # (reparacion) = 88000.00; Prop USD = 500.00 (solo alquiler, sin
        # convertir -- ver hallazgo documentado arriba).
        per_property_response = await client.get(
            f"/v1/settlements/{settlement_id}",
            params={"scope": "per_property"},
            headers=owner_headers,
        )
        assert per_property_response.status_code == 200
        property_groups = per_property_response.json()["data"]["property_groups"]
        assert property_groups is not None
        groups_by_property = {g["property_id"]: g for g in property_groups}
        assert set(groups_by_property) == {str(property_ars), str(property_usd)}
        assert Decimal(groups_by_property[str(property_ars)]["subtotal_ars"]) == Decimal(
            "88000.00"
        )
        assert Decimal(groups_by_property[str(property_usd)]["subtotal_ars"]) == Decimal(
            "500.00"
        )

        # ─── Paso 7: exports Excel/PDF descargables ────────────────────────
        # CA-05-07: "el export Excel y el PDF ... quedan descargables
        # desde el detalle".
        xlsx_response = await client.get(
            f"/v1/settlements/{settlement_id}/export",
            params={"format": "xlsx"},
            headers=owner_headers,
        )
        assert xlsx_response.status_code == 200
        assert xlsx_response.headers["content-type"] == (
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        )
        assert len(xlsx_response.content) > 0

        pdf_response = await client.get(
            f"/v1/settlements/{settlement_id}/export",
            params={"format": "pdf"},
            headers=owner_headers,
        )
        assert pdf_response.status_code == 200
        assert pdf_response.headers["content-type"] == "application/pdf"
        assert pdf_response.content[:4] == b"%PDF"

        attachments = await client.get(
            f"/v1/settlements/{settlement_id}", headers=owner_headers
        )
        attachment_formats = {
            a["format"] for a in attachments.json()["data"]["attachments"]
        }
        assert attachment_formats == {"xlsx", "pdf"}
