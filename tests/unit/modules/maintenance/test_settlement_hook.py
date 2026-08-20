"""tests/unit/modules/maintenance/test_settlement_hook.py -- issue #26.

Unit test puro (sin DB) de `modules/maintenance/settlement_hook.
is_work_order_settled` -- la aproximacion documentada de "ya liquidado"
mientras `settled_in_settlement_id` (Capa 6, issue #27) no exista.
"""

from __future__ import annotations

from adminprop.modules.maintenance.models import WorkOrder
from adminprop.modules.maintenance.settlement_hook import is_work_order_settled


def _work_order(status: str) -> WorkOrder:
    return WorkOrder(
        organization_id="00000000-0000-0000-0000-000000000001",
        property_id="00000000-0000-0000-0000-000000000002",
        title="Arreglo de prueba",
        payer="agency",
        status=status,
        created_by="00000000-0000-0000-0000-000000000003",
    )


class TestIsWorkOrderSettled:
    def test_closed_work_order_is_treated_as_settled(self):
        assert is_work_order_settled(_work_order("closed")) is True

    def test_open_work_order_is_not_settled(self):
        assert is_work_order_settled(_work_order("open")) is False

    def test_in_progress_work_order_is_not_settled(self):
        assert is_work_order_settled(_work_order("in_progress")) is False

    def test_cancelled_work_order_is_not_settled(self):
        assert is_work_order_settled(_work_order("cancelled")) is False
