# Guía de Producción y Flujos de Trabajo — Agrolytics

Guía para llevar Agrolytics a producción. **No es una guía genérica de SaaS**: está
escrita para el stack real de este repo (FastAPI + Celery + PostGIS + frontend
estático) e integra **Stripe**, **Vercel** y **Neon** como pediste.

> ⚠️ **Lo primero, lo más importante:** en `.env` hay una `DEEPSEEK_API_KEY` y un
> `JWT_SECRET` reales. Ya existe `.gitignore` que los excluye del repo, pero como
> estuvieron en el working tree conviene **rotar ambos** antes de lanzar y cargar
> los nuevos solo en el panel del proveedor (nunca en el código).

---

## 0. Realidad del stack — qué se puede y qué no

La guía SaaS típica asume **Next.js todo-en-uno en Vercel**. Agrolytics **no** es eso, y
es clave entenderlo para no perder tiempo:

| Pieza | Agrolytics | ¿Va en Vercel? |
|---|---|---|
| Frontend | `static/index.html` (HTML/JS vanilla + Leaflet + Chart.js) | ✅ Sí |
| API | FastAPI / Uvicorn (`app/main.py`) | ❌ No |
| Workers | Celery (ingesta satelital, insights) — procesos largos | ❌ No |
| Cron | Celery beat | ❌ No |
| Cola | Redis | ❌ No (usar Upstash) |
| Base de datos | PostgreSQL **+ PostGIS** (geoespacial) | ➡️ Neon |
| Storage | Rásters COG en disco/`S3_BUCKET` | ❌ No (usar S3/R2) |

**Por qué el backend NO va en Vercel:** Vercel corre funciones serverless de vida
corta. No puede mantener workers de Celery, conexiones Redis persistentes, ni
procesos largos de descarga/procesamiento satelital. Esto ya está documentado en
[DEPLOYMENT.md](../DEPLOYMENT.md).

### Arquitectura objetivo (híbrida)

```
                 ┌─────────────────────┐
   Usuario  ───► │  Vercel (frontend)  │  static/index.html
                 │  *.vercel.app       │  → llama a la API por HTTPS
                 └──────────┬──────────┘
                            │  /api/v1/*  (CORS allowlist)
                            ▼
   ┌───────────────────────────────────────────────────┐
   │  Render / Fly  (backend — Docker)                  │
   │  ┌──────────┐  ┌───────────┐  ┌─────────────────┐  │
   │  │ FastAPI  │  │ Celery    │  │ Celery beat     │  │
   │  │ (web)    │  │ worker    │  │ (cron)          │  │
   │  └────┬─────┘  └─────┬─────┘  └────────┬────────┘  │
   └───────┼──────────────┼─────────────────┼──────────┘
           │              │                 │
     ┌─────▼─────┐  ┌─────▼──────┐   ┌──────▼───────┐
     │  Neon     │  │  Upstash   │   │  S3 / R2     │
     │ PG+PostGIS│  │  Redis     │   │  rásters COG │
     └───────────┘  └────────────┘   └──────────────┘
           ▲
           │  Stripe webhooks ──► FastAPI /api/v1/billing/webhook
     ┌─────┴─────┐
     │  Stripe   │  Checkout + Customer Portal
     └───────────┘
```

---

## 1. Base de datos — Neon (PostgreSQL + PostGIS)

Agrolytics **necesita PostGIS** (geometrías de parcelas, rásters). Neon lo soporta.

### 1.1 Crear el proyecto

1. [neon.tech](https://neon.tech) → New Project → región más cercana al backend
   (si el backend está en Render `oregon`/`virginia` o Fly `gru`, elegí la más
   próxima para minimizar latencia).
2. Copiá **dos** connection strings desde el dashboard de Neon:
   - **Pooled** (`...-pooler...`) → para la API (muchas conexiones cortas).
   - **Direct** (sin `-pooler`) → para migraciones de Alembic y Celery.

### 1.2 Habilitar PostGIS

La migración `001` corre `CREATE EXTENSION postgis` automáticamente
(`alembic upgrade head`, ejecutado por `entrypoint.sh` con `RUN_MIGRATIONS=true`).
Si querés verificar manualmente desde el SQL Editor de Neon:

```sql
CREATE EXTENSION IF NOT EXISTS postgis;
SELECT postgis_version();
```

### 1.3 Conectar Agrolytics a Neon

`app/core/config.py` ya normaliza una sola connection string: poné el URL **sync**
de Neon en `DATABASE_URL_SYNC` y el async (`+asyncpg`) se deriva solo. El esquema
legacy `postgres://` también se normaliza.

```env
# Pooled para la API (asyncpg):
DATABASE_URL_SYNC=postgresql://USER:PASS@ep-xxx-pooler.region.aws.neon.tech/agrolytics?sslmode=require
```

> **Pooling:** Neon ya hace pooling con el endpoint `-pooler`. Mantené
> `DB_POOL_SIZE`/`DB_MAX_OVERFLOW` bajos (los defaults de 5/10 están bien) para no
> chocar con los límites de conexión del plan free de Neon.
>
> **Migraciones:** corré Alembic contra el endpoint **direct** (sin pooler), porque
> el pooler en modo transaction no soporta sentencias DDL/prepared bien.

### 1.4 Tablas que ya existen vs. las que faltan para SaaS

Agrolytics ya tiene: `users` (con `plan`, `role`, `preferences`, `notifications`),
`fields`, `field_photo`, `field_task`, `index`, `insight`, `satellite_scene`.

**Falta para facturación real (Stripe):** una tabla `subscriptions`
(ver §4). Hoy el plan vive en `users.plan` como string — suficiente para empezar,
pero conviene la tabla dedicada para historial y estado de Stripe.

---

## 2. Frontend en Vercel

El frontend es `static/index.html` (más `privacy.html`, `terms.html`). Es
same-origin con la API cuando se sirve desde el backend, pero en producción lo
separamos a Vercel apuntando a la API en Render/Fly.

### 2.1 Configuración

Crear `vercel.json` en la raíz:

```json
{
  "version": 2,
  "buildCommand": "",
  "outputDirectory": "static",
  "rewrites": [
    { "source": "/privacy", "destination": "/privacy.html" },
    { "source": "/terms", "destination": "/terms.html" },
    { "source": "/api/:path*", "destination": "https://TU-API.onrender.com/api/:path*" }
  ]
}
```

El `rewrite` de `/api/*` hace que el frontend hable con su mismo origen y Vercel
haga de proxy al backend → **evitás problemas de CORS** y no exponés la URL del
backend en el cliente. Si preferís llamadas directas, ajustá `const API` en
`static/index.html` y agregá el dominio de Vercel a `CORS_ORIGINS` (§6).

### 2.2 `env.js` (config pública del frontend)

`index.html` carga `/env.js` y lee `window.AGV_CONFIG` (ej. `posthog_key`). En
Vercel generá ese archivo en build o serví un `static/env.js` con **solo claves
públicas** (nunca secretos):

```js
window.AGV_CONFIG = {
  posthog_key: "phc_xxx",            // public project key, OK exponer
  posthog_host: "https://us.i.posthog.com"
};
```

### 2.3 Deploy

```bash
vercel              # preview
vercel --prod       # producción
```

Vercel da deploy automático por cada push a GitHub (preview en PRs, prod en `main`).

---

## 3. Backend + workers — Render o Fly (se queda fuera de Vercel)

El repo ya trae blueprints listos:

- **Render:** [render.yaml](../render.yaml) — provisiona web + worker + beat +
  Postgres + Redis. Para usar Neon en vez del Postgres de Render, borrá el bloque
  `databases:` y reemplazá `fromDatabase` por el connection string de Neon en
  `DATABASE_URL_SYNC` (como secret `sync:false`).
- **Fly:** [fly.toml](../fly.toml) + [docs/DEPLOY_FLY.md](DEPLOY_FLY.md).

### Redis gestionado (Upstash)

Render Key Value o Upstash. Para Upstash usá la URL `rediss://` (TLS):

```env
REDIS_URL=rediss://default:PASSWORD@HOST.upstash.io:6379
```

### Storage de rásters

El disco persistente de Render/Fly se monta en **un solo servicio**, así que los
COG que escribe el worker no los ve la web. Para servir rásters multi-servicio,
cambiá `DATA_DIR` a object storage S3-compatible (`S3_BUCKET`, ej. AWS S3 o
Cloudflare R2). Para un MVP de un solo nodo, el disco alcanza.

---

## 4. Pagos con Stripe (reemplazar el billing simulado)

**Estado actual:** [app/api/v1/endpoints/billing.py](../app/api/v1/endpoints/billing.py)
tiene pasarelas **simuladas** (`GATEWAYS` Stripe/PayPal/MercadoPago), `/checkout`
devuelve un objeto preview y `/subscribe` setea el plan sin cobrar (deshabilitado
en producción a propósito). Los planes viven en
[app/services/plans.py](../app/services/plans.py) (`free` / `pro` / `enterprise`).

Migración a Stripe real, paso a paso:

### 4.1 Productos y precios en Stripe

1. Stripe Dashboard → Products. Creá un producto por plan de pago:
   - **Productor (`pro`)** → precio recurrente $29/mes → guardá el `price_id`.
   - **Cooperativa (`enterprise`)** → "Contactar" (no checkout self-service).
   - `free` no necesita precio.
2. Mapeá `price_id` en `plans.py`:

```python
# app/services/plans.py
"pro": {
    ...,
    "stripe_price_id": "price_xxx",   # ← nuevo campo
},
```

### 4.2 Dependencia y settings

```bash
# requirements.txt
stripe>=9.0
```

```python
# app/core/config.py  (agregar)
STRIPE_SECRET_KEY: str = ""
STRIPE_WEBHOOK_SECRET: str = ""
STRIPE_PRICE_PRO: str = ""
FRONTEND_URL: str = "http://localhost:8001"   # para success/cancel URLs
```

### 4.3 Modelo de suscripción

```python
# app/models/subscription.py
class Subscription(Base):
    __tablename__ = "subscriptions"
    id: Mapped[uuid] = mapped_column(primary_key=True, default=uuid4)
    user_id: Mapped[uuid] = mapped_column(ForeignKey("users.id"), unique=True)
    stripe_customer_id: Mapped[str | None]
    stripe_subscription_id: Mapped[str | None]
    status: Mapped[str]                # active | past_due | canceled | trialing
    plan: Mapped[str]                  # free | pro | enterprise
    current_period_end: Mapped[datetime | None]
```

Generá la migración: `alembic revision --autogenerate -m "subscriptions"`.

### 4.4 Endpoints reales

Reemplazar `/checkout` y `/subscribe` por:

- **`POST /billing/checkout`** → crea una Stripe Checkout Session y devuelve la URL:

```python
session = stripe.checkout.Session.create(
    mode="subscription",
    customer_email=current_user.email,
    line_items=[{"price": settings.STRIPE_PRICE_PRO, "quantity": 1}],
    success_url=f"{settings.FRONTEND_URL}/?checkout=success",
    cancel_url=f"{settings.FRONTEND_URL}/?checkout=cancel",
    metadata={"user_id": str(current_user.id), "plan": body.plan},
)
return {"url": session.url}
```

  En el front, `confirmPay()` (en `static/index.html`) cambia de simular a:
  `window.location = (await api('/billing/checkout', ...)).url`.

- **`GET /billing/portal`** → Stripe Customer Portal (el usuario gestiona tarjeta,
  facturas y cancelación; **no lo construyas vos**):

```python
portal = stripe.billing_portal.Session.create(
    customer=sub.stripe_customer_id,
    return_url=f"{settings.FRONTEND_URL}/?view=plan",
)
return {"url": portal.url}
```

### 4.5 Webhook — el corazón del sistema

**Sin webhook tu BD nunca se entera de lo que pasa en Stripe.** Endpoint
`POST /billing/webhook` (sin auth JWT; se valida con la firma de Stripe):

```python
@router.post("/webhook")
async def stripe_webhook(request: Request, db: DBSession):
    payload = await request.body()
    sig = request.headers.get("stripe-signature")
    try:
        event = stripe.Webhook.construct_event(payload, sig, settings.STRIPE_WEBHOOK_SECRET)
    except Exception:
        raise HTTPException(400, "Firma inválida")

    t = event["type"]
    obj = event["data"]["object"]
    if t == "checkout.session.completed":
        # activar plan: leer metadata.user_id, guardar customer/subscription id
        ...
    elif t == "customer.subscription.updated":
        # cambio de plan / renovación → actualizar status + current_period_end
        ...
    elif t == "customer.subscription.deleted":
        # cancelación → plan="free", status="canceled"
        ...
    elif t == "invoice.payment_failed":
        # status="past_due" → disparar email de pago fallido
        ...
    return {"received": True}
```

Eventos mínimos a escuchar:

| Evento | Acción |
|---|---|
| `checkout.session.completed` | Activar suscripción, guardar `customer_id` |
| `customer.subscription.updated` | Cambio de plan / renovación |
| `customer.subscription.deleted` | Cancelación → volver a `free` |
| `invoice.payment_failed` | Marcar `past_due`, avisar al usuario |

### 4.6 Probar el webhook localmente

```bash
stripe login
stripe listen --forward-to localhost:8001/api/v1/billing/webhook
# en otra terminal:
stripe trigger checkout.session.completed
```

`stripe listen` imprime el `whsec_...` → ponelo en `STRIPE_WEBHOOK_SECRET`.

### 4.7 Aplicar los límites del plan

El gating ya está modelado en `plans.py` (`plan_max_fields`, `plan_allows_ai`).
Verificá esos límites en los endpoints que crean parcelas y llaman a la IA, leyendo
el plan **desde la suscripción** (no desde un valor que el cliente pueda mandar).

---

## 5. Emails transaccionales (Resend)

Hoy Agrolytics no envía emails. Para SaaS conviene Resend + dominio verificado.

```env
RESEND_API_KEY=re_xxx
EMAIL_FROM="Agrolytics <no-reply@tudominio.com>"
```

Los que importan para este producto (encolar vía **Celery**, ya tenés el worker):

1. **Bienvenida / verificación** — al registrarse (link 24 h).
2. **Reset de contraseña** — link de un solo uso, 1 h máx.
3. **Pago confirmado** — desde el webhook `checkout.session.completed`.
4. **Pago fallido** — desde `invoice.payment_failed`, con link al Customer Portal.
5. **Renovación próxima** — 7 días antes (tarea de Celery beat).
6. **Alertas agronómicas** — riego/plagas según `users.notifications` (ya existe el
   toggle en Configuración del front). Este es el email diferencial del producto.

**Imprescindible:** mandar desde dominio propio con **SPF + DKIM + DMARC**
configurados, o todo va a spam. Nunca desde Gmail.

---

## 6. Seguridad (lo que ya está y lo que falta)

Agrolytics ya trae bastante hardening (ver [DEPLOYMENT.md](../DEPLOYMENT.md)):

- ✅ **Fail-fast en producción** — `config.py` rechaza arrancar con `JWT_SECRET`
  débil, password `postgres` por defecto, o `CORS_ORIGINS=*`.
- ✅ **CORS allowlist** (`CORS_ORIGINS`), sin wildcard.
- ✅ **Security headers** (`X-Frame-Options`, `HSTS`, etc.).
- ✅ **Rate limit** en `/auth/login` y `/auth/register` (`AUTH_RATE_LIMIT`).
- ✅ **Revocación de token** en logout (blocklist JTI en Redis).
- ✅ **Docs ocultos** en prod (`DOCS_ENABLED=false`).
- ✅ **`.env` fuera del repo** (`.gitignore` + `.dockerignore`).

Pendiente / a reforzar:

- ⬜ **Rotar `DEEPSEEK_API_KEY` y `JWT_SECRET`** que estuvieron en el working tree.
- ⬜ **`CORS_ORIGINS`** debe incluir el dominio de Vercel (ej.
  `https://agrolytics.vercel.app,https://tudominio.com`).
- ⬜ **Webhook de Stripe**: validar firma siempre (`STRIPE_WEBHOOK_SECRET`), nunca
  confiar en el body.
- ⬜ **Verificación de email obligatoria** antes de dejar entrar al dashboard
  (hoy no existe; agregar campo `email_verified` + flujo).

### Variables de entorno de producción (resumen)

| Variable | Dónde | Notas |
|---|---|---|
| `APP_ENV=production` | backend | activa el hardening |
| `JWT_SECRET` | backend | `openssl rand -hex 32`, **rotado** |
| `DATABASE_URL_SYNC` | backend | connection string de Neon (pooled) |
| `REDIS_URL` | backend | Upstash `rediss://` |
| `CORS_ORIGINS` | backend | incluir dominio de Vercel |
| `DEEPSEEK_API_KEY` | backend | secret, **rotado** |
| `STRIPE_SECRET_KEY` | backend | modo **LIVE** al lanzar |
| `STRIPE_WEBHOOK_SECRET` | backend | del endpoint de prod |
| `STRIPE_PRICE_PRO` | backend | price_id del plan |
| `RESEND_API_KEY` | backend | emails |
| `SENTRY_DSN` | backend | opcional, errores |
| `S3_BUCKET` | backend | rásters multi-servicio |
| `posthog_key` | frontend (`env.js`) | **público**, analytics |

---

## 7. Flujos de trabajo (Git → Deploy)

Agrolytics usa entornos por rama (ver [DEPLOYMENT.md](../DEPLOYMENT.md)):

| Entorno | Rama | Dónde | `APP_ENV` |
|---|---|---|---|
| Testing | local | Docker Compose | `development` |
| Staging | `develop` | Render (`render.staging.yaml`) + Vercel preview | `staging` |
| Producción | `main` | Render/Fly + Vercel prod + Neon + Stripe live | `production` |

**Flujo diario:**

```
feature/* ──PR──► develop ──(merge)──► main
   │                 │                    │
   │            staging (nube)       producción
   └─ local Docker Compose
```

1. Trabajás en `feature/*`, probás en local (`docker-compose up`, puerto 8001).
2. PR a `develop` → deploy automático a **staging** (Render + Vercel preview).
3. Validás en staging con Stripe en **modo test**.
4. Merge a `main` → deploy a **producción** (Stripe **live**).

> El push a `main` dispara deploy real. Por eso conviene proteger la rama con PR
> obligatorio y, si querés, requerir checks (tests/lint) antes de mergear.

---

## 8. Orden recomendado de construcción

Para no rehacer trabajo, en este orden:

1. **Rotar secretos** (`DEEPSEEK_API_KEY`, `JWT_SECRET`) y subir el repo a GitHub
   (con `.gitignore` ya creado).
2. **Neon** — crear proyecto, habilitar PostGIS, conectar `DATABASE_URL_SYNC`.
3. **Backend en Render/Fly** — blueprint ya existe; cargar secrets en el dashboard.
4. **Frontend en Vercel** — `vercel.json` + `env.js`, apuntar `/api/*` al backend.
5. **Ajustar `CORS_ORIGINS`** con el dominio de Vercel.
6. **Stripe** — productos/precios, modelo `subscriptions`, checkout + webhook +
   portal, probar con `stripe listen`.
7. **Emails (Resend)** — dominio verificado (SPF/DKIM/DMARC), templates en Celery.
8. **Verificación de email** obligatoria antes del dashboard.
9. **Monitoring** — `SENTRY_DSN`, PostHog (opt-in, ya implementado en el front).
10. **Checklist de lanzamiento** (§9).

---

## 9. Checklist antes de lanzar

- [ ] `DEEPSEEK_API_KEY` y `JWT_SECRET` rotados (los viejos invalidados)
- [ ] `APP_ENV=production` y la app arranca sin errores de fail-fast
- [ ] Neon con PostGIS habilitado; `alembic upgrade head` corrió OK
- [ ] `CORS_ORIGINS` = dominios reales (Vercel + dominio propio), sin `*`
- [ ] Stripe en modo **LIVE**, webhook con firma verificada y probado
- [ ] Customer Portal de Stripe habilitado (gestión de plan/tarjeta)
- [ ] Límites de plan (`max_fields`, `ai_monthly`) aplicados server-side
- [ ] Emails desde dominio verificado (SPF/DKIM/DMARC) — no Gmail
- [ ] Dominio propio con SSL (Vercel y backend terminan TLS)
- [ ] Sentry recibiendo errores; PostHog opt-in funcionando
- [ ] Backups automáticos de Neon activados
- [ ] `/terms` y `/privacy` publicados (ya existen en `static/`)
- [ ] Flujo completo probado: registro → verificar email → crear parcela →
      analizar → upgrade con tarjeta real → cancelar desde el portal

---

## 10. Herramientas extra (agregar gradualmente)

| Herramienta | Para qué en Agrolytics |
|---|---|
| **Sentry** | Errores del backend en tiempo real (`SENTRY_DSN` ya soportado) |
| **PostHog** | Analytics + feature flags (ya integrado, opt-in con banner de cookies) |
| **Cloudflare / R2** | DNS, CDN, anti-DDoS y storage S3-compatible para rásters |
| **Crisp / Intercom** | Chat de soporte dentro de la app |
| **Trigger.dev** | Alternativa a Celery para jobs si migrás parte a serverless |
| **Stripe Tax** | Cálculo automático de impuestos por país (LATAM/US) |

---

### Referencias internas

- [DEPLOYMENT.md](../DEPLOYMENT.md) — guía de deploy original (Render/Docker)
- [docs/DEPLOY_FLY.md](DEPLOY_FLY.md) — deploy en Fly.io
- [render.yaml](../render.yaml) / [render.staging.yaml](../render.staging.yaml) — blueprints
- [app/services/plans.py](../app/services/plans.py) — catálogo de planes (fuente única)
- [app/api/v1/endpoints/billing.py](../app/api/v1/endpoints/billing.py) — billing (hoy simulado)
- [app/core/config.py](../app/core/config.py) — todas las variables de entorno
