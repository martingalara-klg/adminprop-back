"""tests/unit/modules/maintenance/test_settlement_hook.py -- issue #26,
actualizado en el issue #29.

Unit test puro (sin DB) de `modules/maintenance/settlement_hook.
is_work_order_settled`. Issue #29: `settled_in_settlement_id` (Capa 6,
migracion #27) ya esta mapeada en el modelo ORM -- el test verifica la
senal REAL (`settled_in_settlement_id IS NOT NULL`), ya no la
aproximacion por `status == 'closed'` que dejaba el issue #26 como
CONCERN documentado.
"""

from __future__ import annotations

from adminprop.modules.maintenance.models import WorkOrder
from adminprop.modules.maintenance.settlement_hook import is_work_order_settled

_SETTLEMENT_ID = "00000000-0000-0000-0000-000000000099"


def _work_order(status: str, *, settled_in_settlement_id: str | None = None) -> WorkOrder:
    return WorkOrder(
        organization_id="00000000-0000-0000-0000-000000000001",
        property_id="00000000-0000-0000-0000-000000000002",
        title="Arreglo de prueba",
        payer="agency",
        status=status,
        created_by="00000000-0000-0000-0000-000000000003",
        settled_in_settlement_id=settled_in_settlement_id,
    )


class TestIsWorkOrderSettled:
    """RN-L04: "ya liquidado" = vinculado a una liquidacion real, no una
    aproximacion por status."""

    def test_closed_work_order_with_settlement_link_is_settled(self):
        work_order = _work_order("closed", settled_in_settlement_id=_SETTLEMENT_ID)
        assert is_work_order_settled(work_order) is True

    def test_closed_work_order_without_settlement_link_is_not_settled(self):
        """Issue #29 cierra el CONCERN del #26: un pedido `closed` que
        todavia NO fue incluido en ninguna liquidacion (aun no le llego
        el turno, o `payer='landlord'` y nunca se liquida via este
        modulo) ya no queda bloqueado por la vieja aproximacion."""
        work_order = _work_order("closed", settled_in_settlement_id=None)
        assert is_work_order_settled(work_order) is False

    def test_open_work_order_is_not_settled(self):
        assert is_work_order_settled(_work_order("open")) is False

    def test_in_progress_work_order_is_not_settled(self):
        assert is_work_order_settled(_work_order("in_progress")) is False

    def test_cancelled_work_order_is_not_settled(self):
        assert is_work_order_settled(_work_order("cancelled")) is False
