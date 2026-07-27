# Deploy Agrolytics on Fly.io — always-on, (almost) free

This is the **always-on, near-free** path: one Fly.io machine that never sleeps,
with **Supabase** (free Postgres, doesn't expire) and **Upstash** (free Redis).
No Celery worker by default — enable it later when you want background satellite
ingestion (see the last section).

**Rough cost:** one `shared-cpu-1x` 1 GB machine ≈ **$5–6/mo** (or ~$3/mo at
512 MB). Postgres (Supabase) and Redis (Upstash) stay on their free tiers.
A credit card on file is required by Fly, but small usage stays cheap.

---

## 0. Prerequisites (once)

```bash
# Install the Fly CLI
curl -L https://fly.io/install.sh | sh      # macOS/Linux
fly auth signup                             # or: fly auth login
```

Accounts you'll need (all free to start): **Fly.io**, **Supabase**, **Upstash**.

---

## 1. Postgres — Supabase (free)

1. Create a project at <https://supabase.com> (pick the region closest to `gru`
   / your users; remember the DB password you set).
2. Project → **Connect** → **Connection string** → choose **Session pooler**
   (port **5432**). It looks like:
   ```
   postgresql://postgres.abcdxyz:YOUR_PASSWORD@aws-0-us-east-1.pooler.supabase.com:5432/postgres
   ```
   > ⚠️ Use the **Session pooler (5432)**, not the Transaction pooler (6543).
   > Session mode is compatible with SQLAlchemy + asyncpg (prepared statements).
   > URL-encode any special characters in the password (`@`→`%40`, etc.).

You'll set this as `DATABASE_URL_SYNC`. The app **derives the async URL
automatically** (`config.py`), so this one var is enough.

---

## 2. Redis — Upstash (free)

1. Create a database at <https://upstash.com> → **Redis** (global or a region
   near your app).
2. Copy the **`rediss://`** URL (TLS, includes the password). Example:
   ```
   rediss://default:SOME_TOKEN@usw1-xxxx.upstash.io:6379
   ```

You'll set this as `REDIS_URL`.

---

## 3. Create the Fly app

From the repo root (`Agrolytics/`), the included `fly.toml` is ready to use:

```bash
fly launch --no-deploy --copy-config --name <your-unique-app-name>
```

- Keep the existing `fly.toml` when prompted.
- Pick a **globally-unique** app name (the URL becomes
  `https://<your-app>.fly.dev`).
- Decline Fly's offers to provision Postgres/Redis — we use Supabase/Upstash.

Create the persistent volume for raster files (same region as the app):

```bash
fly volumes create agrolytics_data --size 1 --region gru
```

---

## 4. Secrets

Generate a strong JWT secret and set everything in one shot (this is what the
production config validator requires — a weak/missing `JWT_SECRET` or default
Postgres password makes the app refuse to boot):

```bash
fly secrets set \
  JWT_SECRET="$(openssl rand -hex 32)" \
  DATABASE_URL_SYNC="postgresql://postgres.abcdxyz:YOUR_PASSWORD@aws-0-...pooler.supabase.com:5432/postgres" \
  REDIS_URL="rediss://default:SOME_TOKEN@usw1-xxxx.upstash.io:6379" \
  DEEPSEEK_API_KEY="sk-..." \
  CORS_ORIGINS="https://<your-app>.fly.dev"
```

Optional (leave unset to keep them off): `SENTRY_DSN`, `POSTHOG_KEY`.

> The frontend is served by the same FastAPI app (same origin), so CORS isn't
> strictly required — but set `CORS_ORIGINS` to your `fly.dev` domain (and any
> custom domain) so a future separate frontend works.

---

## 5. Deploy

```bash
fly deploy
```

What happens: Fly builds the Docker image, runs `alembic upgrade head` on a
release machine (creates the schema + PostGIS), then starts one always-on `api`
machine.

Verify:

```bash
fly status
curl https://<your-app>.fly.dev/health      # → {"status":"ok","env":"production"}
open https://<your-app>.fly.dev              # the dashboard
```

Create your first account from the app's register screen (no demo user is
seeded in production, by design).

---

## 6. Rotate the DeepSeek key 🔑

The key currently in your local `.env` was shared during development. Before
selling, **rotate it** at the DeepSeek dashboard and set only the new value via
`fly secrets set DEEPSEEK_API_KEY=...`. Never commit real keys (`.env` is
git-ignored; `.env.example` holds only placeholders).

---

## 7. (Optional) Enable background satellite ingestion

The free setup has no Celery worker. To turn on scheduled ingestion + insights:

1. In `fly.toml`, **uncomment** the `worker` and `beat` lines under
   `[processes]` and the second `[[vm]]` block at the bottom.
2. Redeploy and scale the new machines up:
   ```bash
   fly deploy
   fly scale count worker=1 beat=1
   ```

This adds a second `shared-cpu-1x` 512 MB machine (≈ +$3/mo). The worker and the
api don't share the volume, so for multi-machine raster serving move `DATA_DIR`
to S3-compatible object storage (see `S3_BUCKET` in `app/core/config.py`).

---

## Cost & scaling cheat-sheet

| Item | Plan | Cost |
|------|------|------|
| Fly api machine (always-on, 1 GB) | shared-cpu-1x | ~$5–6/mo |
| Supabase Postgres | Free | $0 (no 30-day expiry) |
| Upstash Redis | Free | $0 |
| Celery worker+beat (optional) | shared-cpu-1x 512 MB | ~+$3/mo |

Scale up when you have paying customers: raise `DB_POOL_SIZE` / `DB_MAX_OVERFLOW`
(env vars), bump machine memory (`fly scale memory 2048`), or add machines
(`fly scale count api=2`). Keep total DB connections under your Postgres plan's
limit.
