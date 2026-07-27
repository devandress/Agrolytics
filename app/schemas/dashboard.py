"""Dashboard summary schema."""

import uuid
from typing import Any

from pydantic import BaseModel


class DashboardResponse(BaseModel):
    user_id: uuid.UUID
    total_fields: int
    total_area_ha: float
    active_stress_alerts: list[dict[str, Any]]
    latest_prescriptions: list[dict[str, Any]]
    yield_estimates: list[dict[str, Any]]
