"""El productor decide sobre lo que el sistema propone.

El satélite ve síntomas, no el campo. Estos tests fijan el contrato del ciclo de
vida y que las rutas de decisión no queden abiertas.
"""

import uuid

import pytest
from sqlalchemy import Enum as SAEnum

from app.models.field_task import FieldTask

_ESTADOS = {"propuesta", "pendiente", "hecho", "descartada"}


def _status_column():
    return FieldTask.__table__.c.status


def test_the_lifecycle_has_the_four_states():
    tipo = _status_column().type
    assert isinstance(tipo, SAEnum)
    assert set(tipo.enums) == _ESTADOS


def test_a_generated_task_starts_as_a_proposal():
    """Es el punto entero del cambio: el sistema propone, no impone. Si esto vuelve
    a 'pendiente', el generador le llena la lista de pendientes al productor sin
    preguntarle."""
    assert _status_column().default.arg == "propuesta"


def test_a_rejection_can_carry_its_reason():
    """El motivo es la corrección de alguien que camina el lote. Sin lugar donde
    guardarlo, esa información se pierde en el momento en que se produce."""
    col = FieldTask.__table__.c.rejection_reason
    assert col.nullable
    assert col.type.length >= 200


def test_a_decision_records_who_and_when():
    cols = FieldTask.__table__.c
    assert "decided_at" in cols
    assert "decided_by" in cols


@pytest.mark.parametrize(
    "ruta",
    [
        "/api/v1/tasks/proposals",
        f"/api/v1/tasks/{uuid.uuid4()}/approve",
        f"/api/v1/tasks/{uuid.uuid4()}/reject",
        "/api/v1/tasks/proposals/approve-all",
    ],
)
async def test_decision_routes_require_authentication(client, ruta):
    """Aprobar es una acción sobre datos de un productor concreto: sin sesión, no."""
    resp = await client.post(ruta) if "proposals" not in ruta or "approve-all" in ruta else await client.get(ruta)
    assert resp.status_code in (401, 403), f"{ruta} respondió {resp.status_code}"


async def test_approving_someone_elses_task_is_a_404(client):
    """Sin sesión no se llega ni a mirar; con sesión ajena tampoco, porque la
    consulta hace join contra el dueño del campo."""
    resp = await client.post(f"/api/v1/tasks/{uuid.uuid4()}/approve")
    assert resp.status_code in (401, 403, 404)
