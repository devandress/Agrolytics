"""Active Learning Fase 0 — ground-truth labeling endpoints (docs/ACTIVE_LEARNING.md §3):

  PATCH /fields/{field_id}/photos/{photo_id}/label   — farmer-in-the-loop label
  POST  /photos/review-queue/{photo_id}              — agronomist/staff audit

API-level tests over the real ASGI app + a real Postgres row (Field/FieldPhoto rows
are inserted directly through the DB session — no satellite ingestion involved, so
this never touches an external service).
"""

import json
import uuid
from datetime import UTC, datetime

import pytest
from geoalchemy2.functions import ST_GeomFromGeoJSON
from sqlalchemy import update

from app.core.redis_client import get_redis
from app.db.session import AsyncSessionLocal
from app.models.field import Field
from app.models.field_photo import FieldPhoto
from app.models.user import User


@pytest.fixture(autouse=True)
async def _reset_auth_rate_limit():
    """This file registers/logs in far more than 5 times per minute (AUTH_RATE_LIMIT)
    across its tests — clear slowapi's Redis-backed counters before each test so
    tests don't 429 each other depending on run order/how many ran already this
    minute. Redis is internal infra (already required for JWT revocation checks on
    every authenticated request), not an external service — safe/expected to touch.
    """
    r = get_redis()
    keys = [k async for k in r.scan_iter("LIMITS:LIMITER/*/api/v1/auth/*")]
    if keys:
        await r.delete(*keys)
    yield

_POLY = json.dumps(
    {
        "type": "Polygon",
        "coordinates": [
            [
                [-121.5000, 36.6000],
                [-121.5000, 36.6010],
                [-121.4990, 36.6010],
                [-121.4990, 36.6000],
                [-121.5000, 36.6000],
            ]
        ],
    }
)


async def _register_and_login(client) -> tuple[str, uuid.UUID]:
    email = f"{uuid.uuid4()}@example.com"
    password = "correcthorse123"
    await client.post("/api/v1/auth/register", json={"email": email, "password": password})
    login = await client.post("/api/v1/auth/login", json={"email": email, "password": password})
    token = login.json()["access_token"]
    me = await client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"})
    return token, uuid.UUID(me.json()["id"])


async def _promote_to_admin(user_id: uuid.UUID) -> None:
    # Deliberately bypasses the API (register lets a client pick role="admin" itself —
    # a pre-existing hole in app/schemas/user.py/UserCreate outside this change's scope;
    # going through the DB directly keeps this test valid even if that gets fixed later).
    async with AsyncSessionLocal() as db:
        await db.execute(update(User).where(User.id == user_id).values(role="admin"))
        await db.commit()


async def _make_field(user_id: uuid.UUID, name: str = "Lote") -> uuid.UUID:
    async with AsyncSessionLocal() as db:
        field = Field(user_id=user_id, name=name, geometry=ST_GeomFromGeoJSON(_POLY), crop_type="Lechuga")
        db.add(field)
        await db.commit()
        await db.refresh(field)
        return field.id


async def _make_photo(field_id: uuid.UUID, **overrides) -> uuid.UUID:
    async with AsyncSessionLocal() as db:
        photo = FieldPhoto(field_id=field_id, file_path="/tmp/x.jpg", **overrides)
        db.add(photo)
        await db.commit()
        await db.refresh(photo)
        return photo.id


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


# ── PATCH /fields/{field_id}/photos/{photo_id}/label ────────────────────────


async def test_label_photo_happy_path(client):
    token, user_id = await _register_and_login(client)
    field_id = await _make_field(user_id)
    photo_id = await _make_photo(field_id)

    resp = await client.patch(
        f"/api/v1/fields/{field_id}/photos/{photo_id}/label",
        json={"pest_key": "downy_mildew", "severity": "alto", "label_source": "farmer"},
        headers=_auth(token),
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["pest_key"] == "downy_mildew"
    assert body["severity"] == "alto"
    assert body["label_source"] == "farmer"


async def test_label_photo_invalid_pest_key_is_422(client):
    token, _ = await _register_and_login(client)
    resp = await client.patch(
        f"/api/v1/fields/{uuid.uuid4()}/photos/{uuid.uuid4()}/label",
        json={"pest_key": "not_a_real_pest", "severity": "alto", "label_source": "farmer"},
        headers=_auth(token),
    )
    assert resp.status_code == 422


async def test_label_photo_invalid_severity_is_422(client):
    token, _ = await _register_and_login(client)
    resp = await client.patch(
        f"/api/v1/fields/{uuid.uuid4()}/photos/{uuid.uuid4()}/label",
        json={"pest_key": "downy_mildew", "severity": "catastrofico", "label_source": "farmer"},
        headers=_auth(token),
    )
    assert resp.status_code == 422


async def test_label_photo_farmer_cannot_claim_agronomist_source(client):
    token, user_id = await _register_and_login(client)
    field_id = await _make_field(user_id)
    photo_id = await _make_photo(field_id)

    resp = await client.patch(
        f"/api/v1/fields/{field_id}/photos/{photo_id}/label",
        json={"pest_key": "downy_mildew", "severity": "alto", "label_source": "agronomist"},
        headers=_auth(token),
    )
    assert resp.status_code == 403


async def test_label_photo_of_other_owners_field_is_404(client):
    owner_token, owner_id = await _register_and_login(client)
    field_id = await _make_field(owner_id)
    photo_id = await _make_photo(field_id)

    intruder_token, _ = await _register_and_login(client)
    resp = await client.patch(
        f"/api/v1/fields/{field_id}/photos/{photo_id}/label",
        json={"pest_key": "sano", "severity": "bajo", "label_source": "farmer"},
        headers=_auth(intruder_token),
    )
    assert resp.status_code == 404


async def test_label_photo_mismatched_field_id_is_404(client):
    # Same owner, but the photo belongs to a *different* one of their own fields —
    # the path's field_id must match the photo's actual field_id, not just be owned.
    token, user_id = await _register_and_login(client)
    field_a = await _make_field(user_id, name="Lote A")
    field_b = await _make_field(user_id, name="Lote B")
    photo_id = await _make_photo(field_a)

    resp = await client.patch(
        f"/api/v1/fields/{field_b}/photos/{photo_id}/label",
        json={"pest_key": "sano", "severity": "bajo", "label_source": "farmer"},
        headers=_auth(token),
    )
    assert resp.status_code == 404


async def test_relabeling_a_reviewed_photo_clears_the_stale_review_trail(client):
    token, user_id = await _register_and_login(client)
    field_id = await _make_field(user_id)
    photo_id = await _make_photo(
        field_id,
        pest_key="downy_mildew",
        severity="alto",
        reviewed_by=None,  # FK — leave unset to avoid needing a real reviewer row
        reviewed_at=datetime.now(UTC),
    )

    resp = await client.patch(
        f"/api/v1/fields/{field_id}/photos/{photo_id}/label",
        json={"pest_key": "sano", "severity": "bajo", "label_source": "farmer"},
        headers=_auth(token),
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["reviewed_by"] is None
    assert body["reviewed_at"] is None
    assert body["pest_key"] == "sano"
    assert body["severity"] == "bajo"


# ── POST /photos/review-queue/{photo_id} ────────────────────────────────────


async def test_review_queue_requires_admin(client):
    token, user_id = await _register_and_login(client)
    field_id = await _make_field(user_id)
    photo_id = await _make_photo(field_id, pest_key="downy_mildew", severity="alto")

    resp = await client.post(
        f"/api/v1/photos/review-queue/{photo_id}",
        json={"reviewed_pest_key": "downy_mildew", "reviewed_severity": "alto", "agree": True},
        headers=_auth(token),
    )
    assert resp.status_code == 403


async def test_review_queue_admin_can_overwrite_label_and_stamps_audit_trail(client):
    farmer_token, farmer_id = await _register_and_login(client)
    field_id = await _make_field(farmer_id)
    photo_id = await _make_photo(field_id, pest_key="downy_mildew", severity="bajo", label_source="farmer")

    admin_token, admin_id = await _register_and_login(client)
    await _promote_to_admin(admin_id)

    resp = await client.post(
        f"/api/v1/photos/review-queue/{photo_id}",
        json={"reviewed_pest_key": "botrytis", "reviewed_severity": "alto", "agree": False},
        headers=_auth(admin_token),
    )
    assert resp.status_code == 200
    body = resp.json()
    # Overwritten, not merged — the agronomist's read replaces the farmer's.
    assert body["pest_key"] == "botrytis"
    assert body["severity"] == "alto"
    assert body["reviewed_by"] == str(admin_id)
    assert body["reviewed_at"] is not None


async def test_review_queue_agree_true_still_stamps_reviewed_at(client):
    farmer_token, farmer_id = await _register_and_login(client)
    field_id = await _make_field(farmer_id)
    photo_id = await _make_photo(field_id, pest_key="downy_mildew", severity="alto", label_source="farmer")

    admin_token, admin_id = await _register_and_login(client)
    await _promote_to_admin(admin_id)

    resp = await client.post(
        f"/api/v1/photos/review-queue/{photo_id}",
        json={"reviewed_pest_key": "downy_mildew", "reviewed_severity": "alto", "agree": True},
        headers=_auth(admin_token),
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["reviewed_by"] == str(admin_id)
    assert body["reviewed_at"] is not None


async def test_review_queue_nonexistent_photo_is_404(client):
    admin_token, admin_id = await _register_and_login(client)
    await _promote_to_admin(admin_id)
    resp = await client.post(
        f"/api/v1/photos/review-queue/{uuid.uuid4()}",
        json={"reviewed_pest_key": "sano", "reviewed_severity": "bajo", "agree": True},
        headers=_auth(admin_token),
    )
    assert resp.status_code == 404
