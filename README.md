# Agrolytics API

Agricultural intelligence platform that ingests Sentinel-2 satellite imagery and generates actionable field insights (management zones, stress alerts, nitrogen prescriptions, yield estimates) with a premium paywall model.

---

## Tech stack

| Layer | Technology |
|---|---|
| API | FastAPI + Uvicorn |
| Database | PostgreSQL 15 + PostGIS 3.4 |
| ORM | SQLAlchemy 2.0 (async) + GeoAlchemy2 |
| Migrations | Alembic |
| Background jobs | Celery 5 + Redis 7 |
| Satellite data | Sentinel-2 L2A via Microsoft Planetary Computer STAC API |
| Geospatial | rasterio, shapely, geopandas |
| ML | scikit-learn (KMeans) |
| Auth | JWT (python-jose) + bcrypt |

---

## Quick start

### 1. Clone and configure

```bash
git clone <repo-url>
cd Agrolytics
cp .env.example .env
# Edit .env — at minimum set JWT_SECRET to a random hex string:
# openssl rand -hex 32
```

### 2. Launch with Docker Compose

```bash
docker-compose up --build
```

This will:
1. Start **postgis**, **redis**, **api**, **celery-worker**, and **celery-beat**.
2. The `api` container runs `alembic upgrade head` automatically before starting Uvicorn.
3. Swagger UI is available at **http://localhost:8000/docs**.

### 3. Verify health

```bash
curl http://localhost:8000/health
# {"status":"ok","env":"development"}
```

---

## API endpoints

### Authentication

| Method | Path | Description |
|---|---|---|
| POST | `/api/v1/auth/register` | Register a new user |
| POST | `/api/v1/auth/login` | Login → returns access + refresh tokens |
| POST | `/api/v1/auth/refresh` | Issue a new access token |

### Fields (CRUD)

| Method | Path | Description |
|---|---|---|
| POST | `/api/v1/fields` | Create a field (GeoJSON Polygon) |
| GET | `/api/v1/fields` | List all fields for the authenticated user |
| GET | `/api/v1/fields/{id}` | Get a single field |
| PATCH | `/api/v1/fields/{id}` | Update field name, geometry, crop type |
| DELETE | `/api/v1/fields/{id}` | Delete field + cascade all data |

### Spectral indices

| Method | Path | Description |
|---|---|---|
| GET | `/api/v1/fields/{id}/ndvi?start_date=&end_date=` | Time-series + latest GeoJSON |

### Insights (premium)

| Method | Path | Description |
|---|---|---|
| POST | `/api/v1/insights/fields/{id}/insights/generate` | Start async insight computation |
| GET | `/api/v1/insights/{id}` | Get insight (preview if unpurchased) |
| POST | `/api/v1/insights/{id}/buy` | Simulate payment, unlock full content |

### Dashboard

| Method | Path | Description |
|---|---|---|
| GET | `/api/v1/dashboard/{user_id}` | Summary: alerts, prescriptions, yield estimates |

---

## Testing the insight purchase flow

```bash
# 1. Register
curl -X POST http://localhost:8000/api/v1/auth/register \
  -H "Content-Type: application/json" \
  -d '{"email":"farmer@example.com","password":"secret123","role":"farmer"}'

# 2. Login
TOKEN=$(curl -s -X POST http://localhost:8000/api/v1/auth/login \
  -H "Content-Type: application/json" \
  -d '{"email":"farmer@example.com","password":"secret123"}' \
  | python3 -c "import sys,json; print(json.load(sys.stdin)['access_token'])")

# 3. Create a field (GeoJSON Polygon in WGS-84)
FIELD_ID=$(curl -s -X POST http://localhost:8000/api/v1/fields \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "North Block",
    "geometry": {
      "type": "Polygon",
      "coordinates": [[
        [-63.0, -33.0], [-62.9, -33.0], [-62.9, -33.1],
        [-63.0, -33.1], [-63.0, -33.0]
      ]]
    },
    "crop_type": "corn"
  }' | python3 -c "import sys,json; print(json.load(sys.stdin)['id'])")

echo "Field ID: $FIELD_ID"

# 4. Trigger insight generation (returns task_id)
TASK=$(curl -s -X POST \
  "http://localhost:8000/api/v1/insights/fields/$FIELD_ID/insights/generate" \
  -H "Authorization: Bearer $TOKEN")
echo "$TASK"

# 5. List insights after the Celery task completes (~30 s)
# (Use the insight_id from the DB or from a future /fields/{id}/insights list endpoint)

# 6. Get insight — content is masked (preview only)
curl -s "http://localhost:8000/api/v1/insights/<insight_id>" \
  -H "Authorization: Bearer $TOKEN" | python3 -m json.tool

# 7. Buy the insight (simulated payment)
curl -s -X POST "http://localhost:8000/api/v1/insights/<insight_id>/buy" \
  -H "Authorization: Bearer $TOKEN" | python3 -m json.tool

# 8. Get insight again — full content now visible
curl -s "http://localhost:8000/api/v1/insights/<insight_id>" \
  -H "Authorization: Bearer $TOKEN" | python3 -m json.tool

# 9. Dashboard
curl -s "http://localhost:8000/api/v1/dashboard/<user_id>" \
  -H "Authorization: Bearer $TOKEN" | python3 -m json.tool
```

---

## Running tests

```bash
# Inside the container
docker-compose exec api pytest

# Or locally (requires rasterio, scikit-learn, etc.)
pip install -r requirements.txt
pytest
```

---

## Project structure

```
app/
├── main.py               # FastAPI app factory
├── core/
│   ├── config.py         # Pydantic settings (env vars)
│   ├── security.py       # JWT + password hashing
│   └── logging.py        # Loguru setup
├── db/
│   ├── base.py           # SQLAlchemy declarative base
│   └── session.py        # Async + sync session factories
├── models/               # ORM models (users, fields, scenes, indices, insights)
├── schemas/              # Pydantic request/response schemas
├── api/
│   └── v1/
│       ├── router.py
│       └── endpoints/    # auth, fields, indices, insights, dashboard
├── services/             # Business logic
│   ├── satellite_ingestion.py  # STAC search + COG download
│   ├── clustering.py           # KMeans zone mapping
│   ├── stress_alert.py         # p25 baseline stress detection
│   ├── prescription.py         # Variable-rate N prescription
│   ├── yield_estimation.py     # Linear NDVI-based yield model
│   └── insights_generator.py  # Orchestrator
└── tasks/
    ├── celery_app.py     # Celery app + beat schedule
    ├── satellite_tasks.py
    └── insight_tasks.py
migrations/               # Alembic migrations
tests/                    # pytest unit tests
docker-compose.yml
Dockerfile
```

---

## Environment variables

See `.env.example` for all variables.  Critical ones:

| Variable | Description |
|---|---|
| `JWT_SECRET` | Random secret for signing tokens — **change in production** |
| `DATABASE_URL` | Async PostgreSQL connection string (asyncpg) |
| `DATABASE_URL_SYNC` | Sync connection string (psycopg2, used by Alembic + Celery) |
| `REDIS_URL` | Redis broker / backend for Celery |
| `STAC_API_URL` | Planetary Computer STAC endpoint |
| `PLANETARY_COMPUTER_KEY` | Optional PC subscription key |
| `DATA_DIR` | Local path for COG raster storage |
