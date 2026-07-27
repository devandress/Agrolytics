# Agrolytics - Deployment Guide

## Production Deployment

This guide covers deploying Agrolytics for ~100 users in a production environment.

### Prerequisites

- Docker & Docker Compose 2.0+
- PostgreSQL 15 with PostGIS 3.4
- Redis 7+
- 4GB+ RAM, 2+ CPU cores

### Environment Configuration

Copy `.env.example` to `.env` and configure critical variables:

```bash
cp .env.example .env
```

**Critical variables for production:**

```env
# Security
JWT_SECRET=<generate-with-openssl-rand-hex-32>
APP_ENV=production

# Database
DATABASE_URL=postgresql+asyncpg://postgres:${DB_PASSWORD}@db:5432/agrolytics
DATABASE_URL_SYNC=postgresql://postgres:${DB_PASSWORD}@db:5432/agrolytics

# Redis
REDIS_URL=redis://redis:6379/0

# Satellite
STAC_API_URL=https://planetarycomputer.microsoft.com/api/stac/v1
PLANETARY_COMPUTER_KEY=<optional>

# Storage
DATA_DIR=/app/data
```

### Generate secure JWT secret

```bash
openssl rand -hex 32
```

### Docker Compose Configuration

The `docker-compose.yml` is optimized for ~100 concurrent users:

- **API**: 4 Uvicorn workers, 2GB max memory
- **Celery Worker**: 8 concurrent tasks, 2GB max memory
- **PostgreSQL**: 200 connections, 4GB max memory
- **Redis**: LRU eviction, 512MB max memory

### Deployment Steps

1. **Clone and configure:**
   ```bash
   git clone <repo-url>
   cd Agrolytics
   cp .env.example .env
   # Edit .env with your production values
   ```

2. **Build images:**
   ```bash
   docker-compose build --no-cache
   ```

3. **Start services:**
   ```bash
   docker-compose up -d
   ```

4. **Verify health:**
   ```bash
   curl http://localhost:8000/health
   # {"status":"ok","env":"production"}
   ```

5. **Check logs:**
   ```bash
   docker-compose logs -f api celery-worker
   ```

### Database Initialization

Migrations and test user creation run automatically:

1. Alembic runs `alembic upgrade head`
2. Test user (123@gmail.com / 12345678) is created if it doesn't exist

To manually initialize:

```bash
docker-compose exec api python -m app.init_db
```

### Performance Tuning

**PostgreSQL Connection Pool:**
- Async pool: 20 base + 40 overflow
- Sync pool (Celery): 10 base + 20 overflow

**Celery:**
- Worker concurrency: 8 (configurable)
- Max tasks per child: 1000
- Fair scheduler enabled

**Redis:**
- Max memory: 512MB
- Eviction: LRU policy

### Monitoring

**Health endpoints:**
- `/health` — basic liveness check
- `/docs` — Swagger UI for API exploration

**Logs:**
```bash
docker-compose logs -f api
docker-compose logs -f celery-worker
docker-compose logs -f db
```

**Performance metrics:**
- Monitor PostgreSQL connections: `SELECT count(*) FROM pg_stat_activity`
- Monitor Celery tasks: Celery Flower (optional UI)
- Monitor Redis memory: `redis-cli INFO memory`

### Scaling Beyond 100 Users

For larger deployments:

1. **Database:** Use managed PostgreSQL (RDS, Cloud SQL) with read replicas
2. **Cache:** Use managed Redis or Redis Cluster
3. **Workers:** Scale Celery workers horizontally
4. **API:** Use Kubernetes or container orchestration
5. **Storage:** Use S3 or cloud object storage instead of local `/app/data`

## Dos entornos: Testing (local) y Deploy (Render)

Agrolytics se opera con **dos versiones**:

| Entorno | Dónde | Para qué | `APP_ENV` |
|---|---|---|---|
| **Testing** | Docker Compose en tu máquina | Desarrollo y pruebas diarias | `development` |
| **Producción** | Render (rama `main`) | Despliegue real | `production` |
| **Staging** (opcional) | Render (rama `develop`) | Pruebas en la nube antes de prod | `staging` |

### Testing (local)
```bash
cp .env.example .env        # APP_ENV=development
docker-compose up           # base + docker-compose.override.yml (hot reload)
# http://localhost:8001  ·  usuario demo: 123@gmail.com / 12345678
```
Es la "versión de pruebas": corre migraciones, crea el usuario demo y permite ingestar datos
satelitales reales. Sentry/PostHog quedan **apagados** (sin `SENTRY_DSN`/`POSTHOG_KEY`).

### Producción (Render, rama `main`)
`render.yaml` despliega web + worker + beat + PostgreSQL + Redis desde `main`. Variables públicas
ya están en el blueprint; las **secretas** (`DEEPSEEK_API_KEY`, `SENTRY_DSN`, `POSTHOG_KEY`) se
cargan en el dashboard (`sync:false`). `APP_ENV=production` activa el hardening (CORS estricto,
docs ocultos, fail-fast de secretos).

### Staging (Render, rama `develop`) — opcional
`render.staging.yaml` define un stack independiente con nombres `-staging` y su propia BD/Redis,
apuntando a `develop`. Úsalo cuando quieras un entorno de nube de pruebas (ver instrucciones en el
encabezado del archivo). Flujo: **push a `develop` → staging**, **merge a `main` → producción**.

---

### Deploying to Render (free / low-cost PaaS)

> **Note:** Vercel cannot host this backend — it only runs short-lived serverless
> functions and cannot run the Celery workers, Redis, PostGIS, persistent raster
> storage, or long-running satellite processing. Use Render (below) for the API +
> workers; a static frontend may live on Vercel pointed at the Render API URL.

A `render.yaml` Blueprint is included. Steps:

1. Push the repo to GitHub.
2. Render Dashboard → **New → Blueprint** → select the repo. Render reads
   `render.yaml` and provisions the web service, Celery worker, Celery beat,
   PostgreSQL, and a Key Value (Redis).
3. `CORS_ORIGINS` defaults to Render's free subdomain
   (`https://agrolytics-api.onrender.com`). After the first deploy, confirm the
   real URL Render assigned (it may add a random suffix if the name was taken) and
   update `CORS_ORIGINS` to match. The bundled frontend is same-origin, so this is
   only strictly needed once you host a separate frontend.
4. `JWT_SECRET` is auto-generated; PostGIS is enabled by migration `001` on first
   `alembic upgrade head` (run automatically by `entrypoint.sh` since
   `RUN_MIGRATIONS=true`).

Config is single-connection-string friendly: set `DATABASE_URL_SYNC` to the managed
Postgres URL and the async (`+asyncpg`) URL is derived automatically in
`app/core/config.py`. Legacy `postgres://` schemes are normalized.

**Render caveats:** free Postgres expires (~30 days); persistent disks attach to a
single service, so for multi-service raster serving switch `DATA_DIR` to S3-compatible
object storage (`S3_BUCKET`). See `render.yaml` comments.

### Production hardening (built in)

The following are now enforced/available in code:

- **Fail-fast config** — in `APP_ENV=production` the app refuses to start with a
  default/weak `JWT_SECRET`, a default `postgres` DB password, or `CORS_ORIGINS=*`.
- **CORS allowlist** — driven by `CORS_ORIGINS` (no wildcard).
- **Security headers** — `X-Content-Type-Options`, `X-Frame-Options`,
  `Referrer-Policy`, and `Strict-Transport-Security` (production) on every response.
- **Auth rate limiting** — `/auth/login` and `/auth/register` limited per IP
  (`AUTH_RATE_LIMIT`, default `5/minute`).
- **Token revocation** — `POST /auth/logout` blocklists the token's JTI in Redis
  until expiry; `get_current_user` rejects revoked tokens.
- **Docs hidden** — `/docs` and `/redoc` are disabled in production.
- **No secret leakage** — `.dockerignore` keeps `.env` out of the image.

### Security Checklist

- [ ] JWT_SECRET rotated and strong (32 bytes+) — enforced at startup
- [ ] DATABASE_URL uses secure password (20+ chars) — default rejected at startup
- [ ] CORS_ORIGINS set to specific origins (not `*`) — enforced at startup
- [ ] HTTPS enforced via reverse proxy / platform (Render terminates TLS)
- [ ] Database backups scheduled daily
- [ ] Logs rotated and archived
- [x] Rate limiting on auth endpoints (slowapi)
- [ ] Secrets stored in platform vault / `fromDatabase`/`generateValue`, not `.env`
- [x] `.env` excluded from Docker image (`.dockerignore`)

### Backup & Recovery

**Database backup:**
```bash
docker-compose exec db pg_dump -U postgres agrolytics > backup.sql
```

**Restore from backup:**
```bash
docker-compose exec -T db psql -U postgres agrolytics < backup.sql
```

### Troubleshooting

**Migrations fail:**
```bash
docker-compose exec api alembic current
docker-compose exec api alembic heads
docker-compose exec api alembic stamp <revision>
```

**Celery not processing tasks:**
```bash
docker-compose exec redis redis-cli PING
docker-compose logs celery-worker
```

**Database connection exhaustion:**
- Increase `pool_size` in `app/db/session.py`
- Check for connection leaks in application code
- Monitor with `SELECT count(*) FROM pg_stat_activity WHERE usename='postgres'`

### Rolling Updates

1. Build new image: `docker-compose build api`
2. Start new container: `docker-compose up -d api --no-deps`
3. Monitor: `docker-compose logs -f api`
4. Rollback if needed: `docker-compose down && git checkout && docker-compose up -d`
