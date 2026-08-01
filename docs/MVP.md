# Agrolytics — MVP: qué falta y qué no

**Fecha de corte:** 2026-08-01 · **Rama:** `main` (sincronizada con `origin/main`)
**Verificado contra el código, no contra el roadmap.** Cada afirmación de este documento
tiene su evidencia citada (`archivo:línea`, comando ejecutado o variable de entorno).

Este documento **reemplaza a [ROADMAP.md](ROADMAP.md) como orden de trabajo**. El roadmap
sigue siendo válido como visión de producto (Fases 2–4), pero nada de ahí se toca hasta
cerrar el bloque P0 de acá.

---

## 0. Definición de MVP (el criterio que decide todo lo demás)

> Un productor desconocido entra a la URL pública, se registra, dibuja su parcela, ve
> índices satelitales reales de su campo, recibe tareas priorizadas, y **puede pagar el
> plan Pro con su tarjeta**. Si algo falla, nosotros nos enteramos antes que él.

Todo lo que no sea necesario para esa frase queda fuera del MVP. Sin excepciones.

Tres pruebas de aceptación, todas end-to-end contra el deploy público:

1. **Registro → parcela → dato real.** Cuenta nueva, parcela dibujada, NDVI de una escena
   Sentinel-2 real visible en el mapa en menos de 24 h.
2. **Cobro.** Checkout de MercadoPago en modo LIVE, webhook recibido y verificado, el plan
   del usuario pasa a `pro` en la DB, y una cancelación lo devuelve a `free`.
3. **Observabilidad.** Un error 500 provocado a propósito aparece en Sentry con stack trace.

---

## 1. Lo que YA está (no falta — no re-hacerlo)

### Backend / dominio
- **API FastAPI completa**: 13 routers registrados (`app/api/v1/router.py:22-39`) — auth,
  fields, indices, insights, dashboard, weather, rasters, chat, advisory, roles, analysis,
  billing. 9.449 líneas de Python en `app/`.
- **Auth JWT real**: registro, login, refresh, logout, cambio de contraseña, forgot/reset
  (`app/api/v1/endpoints/auth.py`). Rate limit por IP en auth (`AUTH_RATE_LIMIT=5/minute`).
- **Fail-fast de configuración en producción** (`app/core/config.py:145`): la app no arranca
  con `JWT_SECRET` por defecto si `APP_ENV=production`.
- **Ingesta satelital real y automática**: Sentinel-2 cada 6 h, Sentinel-1 (radar) cada 6 h
  desfasado 3 h, multisensor (Landsat/MODIS) diario (`app/tasks/celery_app.py:33-58`).
  Se dispara también al crear parcela (`app/api/v1/endpoints/fields.py:93,207`).
- **Motor agronómico**: 30 servicios en `app/services/` — índices, fusión multisensor,
  anomalías, clustering, fenología, biomasa, rendimiento, riego, prescripción, estrés,
  modelo de plagas + ráster de riesgo, salud de rancho.
- **Billing MercadoPago real** (`app/api/v1/endpoints/billing.py`): suscripción recurrente
  vía `/preapproval`, webhook como único lugar que otorga plan pago, cancelación, y
  degradación automática a `free` si la suscripción se pausa/cancela. Precio por hectárea.
- **Límite de parcelas por plan aplicado server-side** (`app/api/v1/endpoints/fields.py:44-45`).
- **Tareas de campo persistentes** + generación desde índices + fotos de validación.
- **Etiquetas de ground-truth en fotos** (WIP sin commitear): `pest_key`, `severity`,
  `label_source`, `label_confidence`, `reviewed_by`, `reviewed_at` + migración `008` ya
  aplicada + 4 endpoints (`roles.py:287-397`). Ver §5.

### Infraestructura y calidad
- **119 tests pasando** (`docker exec agrovision-api-1 pytest` → `119 passed in 7.03s`).
- **Migraciones Alembic** en orden, un solo head (`alembic current` → `008 (head)`).
- **Docker Compose** dev (bind mount) y prod separados; `Dockerfile`, `entrypoint.sh`.
- **Ruff + mypy configurados** y corriendo dentro de la imagen.
- **Manifiestos de deploy escritos**: `render.yaml`, `render.staging.yaml`,
  `render.paid.yaml`, `fly.toml`.
- **Código de Sentry y PostHog ya integrado** (`app/main.py:25-26,120-121`) — solo falta la clave.
- **Backup a S3 escrito** (`app/services/backup.py`) + tarea diaria 08:00 UTC.
- **`/terms` y `/privacy` publicados** (`app/main.py:129-135`).
- **Frontend funcional**: SPA de 2.391 líneas (`static/index.html`) — mapa dominante,
  sidebar unificada, timeline, wizard de parcela, panel de análisis, gestión de plagas,
  subida de foto con cámara, pantalla de planes y checkout.

---

## 2. P0 — BLOQUEANTE para el MVP

Sin esto no se puede cobrar, o se cobra y se rompe. Orden = orden de ejecución.

### P0.1 · MercadoPago nunca se probó con dinero
**Evidencia:** `MERCADOPAGO_ACCESS_TOKEN` y `MERCADOPAGO_PUBLIC_KEY` vacías en `.env`;
`/billing/plans` devuelve `"sandbox": true` (`billing.py:74`). Todo el flujo de pago corre
hoy en modo preview: no crea `init_point`, no cobra.
**Qué hacer:** credenciales `TEST-...` → checkout completo → webhook recibido y verificado →
plan `pro` en la DB → cancelación → vuelve a `free`. Recién ahí, token `APP_USR-...`.
**Riesgo si se salta:** el único camino de ingreso del producto está sin ejecutar una sola vez.

### P0.2 · Sin verificación de email en el registro
**Evidencia:** `auth.py` expone 9 rutas y ninguna es `verify-email`; `send_email` solo se usa
para reset de contraseña (`auth.py:33`). `SMTP_HOST/USER/PASSWORD` no están en `.env`.
**Qué hacer:** proveedor de email transaccional con dominio verificado (SPF/DKIM/DMARC),
token de verificación al registrarse, y gatear la creación de parcelas hasta verificar.
**Riesgo si se salta:** cuentas basura, y ningún email del sistema llega — incluido el reset
de contraseña, que hoy está escrito pero muerto por falta de SMTP.

### P0.3 · La cuota de IA no se mide
**Evidencia:** comentario propio en `app/core/config.py:93` — *"the actual per-plan monthly
quota (`PLANS[...]["ai_monthly"]`) isn't metered yet"*. Lo único que hay es un rate limit por
IP de `20/hour`. El plan free declara `ai_monthly: 0` (`plans.py:38`) y nadie lo aplica.
**Qué hacer:** contador mensual por usuario, persistido, chequeado antes de llamar a DeepSeek,
con error claro al agotarse.
**Riesgo si se salta:** un usuario del plan gratis consume tokens de DeepSeek sin techo. Es
costo real en dólares, y es el modo más fácil de que el MVP pierda plata por cliente.

### P0.4 · Las fotos se pierden al reiniciar
**Evidencia:** `roles.py:302-307` escribe a `DATA_DIR/photos/<field_id>/` en disco local. En
Render/Fly sin volumen persistente ese directorio es efímero.
**Qué hacer:** subir las fotos a S3/R2 (ya hay `boto3` en el proyecto por `backup.py`) y
guardar la key, no la ruta local. Migrar las existentes.
**Extra del mismo endpoint** — `dest.write_bytes(await file.read())` lee el archivo entero en
memoria, sin límite de tamaño ni validación de tipo, y confía en la extensión que manda el
cliente. Agregar límite de bytes y validación de content-type real.
**Riesgo si se salta:** las fotos son el activo de datos del producto (§ Active Learning).
Perderlas es perder lo único que no se puede recomprar.

### P0.5 · Datos de demostración mezclados con datos reales
**Evidencia:** `app/core/demo_data.py` alimenta `roles.py` y `ranch_health.py` — las cifras de
dinero y agua/SGMA son inventadas.
**Qué hacer:** decidir por cada cifra: (a) etiquetarla en la UI como *estimación de
demostración* de forma imposible de confundir, o (b) sacarla del MVP. No hay opción (c).
**Riesgo si se salta:** un productor toma una decisión de riego o de plata con un número
inventado. Es el mismo error de falsa certeza que el modelo de plagas ya evita con
`is_model`/`confidence` — no repetirlo con dinero.

### P0.6 · Nada está desplegado ni verificado en vivo
**Evidencia:** `PUBLIC_BASE_URL=http://localhost:8001` en `.env`. Los manifiestos existen pero
no hay constancia de un deploy funcionando.
**Qué hacer, en orden:** DB gestionada con PostGIS → `alembic upgrade head` → API + worker +
beat desplegados → dominio con SSL → `CORS_ORIGINS` con dominios reales (sin `*`) →
`APP_ENV=production` → `JWT_SECRET` y `DEEPSEEK_API_KEY` rotados → `DOCS_ENABLED=false`.
**Riesgo si se salta:** no hay MVP. Un MVP que no está en línea no existe.

### P0.7 · Sin observabilidad
**Evidencia:** `SENTRY_DSN` y `POSTHOG_KEY` no están en `.env`; el código ya está integrado y
solo espera la clave (`app/main.py:25-26`).
**Qué hacer:** crear ambos proyectos, poner las claves, provocar un 500 y verlo llegar.
**Riesgo si se salta:** los errores del primer usuario real los descubrimos porque se va.

---

## 3. P1 — Importante, pero no bloquea cobrar

- **Backups apagados.** `backup.py:33-34` exige `BACKUP_S3_ENDPOINT_URL`, `BACKUP_S3_BUCKET`,
  `BACKUP_S3_ACCESS_KEY` y `BACKUP_S3_SECRET_KEY`; ninguna está seteada, así que la tarea
  diaria hace no-op. Además el propio comentario de `celery_app.py:50-53` advierte que en el
  plan free de Render el beat no corre: usar `python -m app.services.backup` como cron job.
- **Los 5 roles son vistas, no permisos.** `User.role` solo acepta `farmer|admin`
  (`app/models/user.py:23-24`); el selector de rol de la UI no gatea nada. Aceptable para el
  MVP mono-usuario; bloqueante en cuanto un cliente tenga empleados.
- **Las etiquetas nuevas no tienen UI.** El backend acepta `pest_key`/`severity`
  (`roles.py:362`) pero `static/index.html` solo pregunta *"¿La alerta es correcta?"*
  (línea 575). Sin frontend, la tabla se llena de `NULL` y el trabajo de §5 no rinde.
- **Deuda de tipos y lint.** `mypy app` → 60 errores en 21 archivos; `ruff check app tests` →
  5 errores (2× UP017 en `backup.py`, 1× F541 en `pest_model.py`, 2× C416 en `analysis.py`).
  Todos preexistentes, ninguno introducido por el WIP actual. 3 son auto-fixables.
- **Documentación desactualizada.** [GUIA_PRODUCCION.md](GUIA_PRODUCCION.md) §4 dice
  "reemplazar el billing simulado" con **Stripe**, pero el billing real ya está hecho con
  **MercadoPago**. Esa sección entera induce a error y hay que reescribirla.
- **`pytest` roto en el host.** `pluggy` 0.13.0 del sistema contra `pytest-asyncio` moderno:
  `TypeError: HookimplMarker.__call__() got an unexpected keyword argument 'specname'`.
  Workaround: correr los tests dentro del contenedor. Documentarlo en CONTRIBUTING.md.

---

## 4. Fuera del MVP — congelado hasta después de cobrarle al primer cliente

No es que estén mal. Es que ninguna hace que alguien pague hoy.

| Congelado | De dónde viene |
|---|---|
| Clasificador ML de plagas (Active Learning Fases 1–4) | [ACTIVE_LEARNING.md](ACTIVE_LEARNING.md) §Fases |
| WhatsApp/SMS (Twilio), voz TTS, modo offline PWA | [ROADMAP.md](ROADMAP.md) Fase 2 |
| Integración contable real, SGMA por API, calibración de rendimiento | ROADMAP Fase 3 |
| SAOCOM / NISAR banda L–S | [RADAR_SAR.md](RADAR_SAR.md) |
| Cooperativa/comprador, aseguradora/banco, white-label | ROADMAP Fase 4 |
| Permisos reales por rol (5 roles) | ROADMAP Fase 4 — ver P1 |

---

## 4.1 Ideas anotadas, no empezadas

**Dibujar parcelas con IA.** En vez del wizard actual (el usuario dibuja el polígono
punto por punto sobre el mapa), que el usuario *seleccione* la parcela con un clic y
la IA proponga y pula el contorno — segmentación sobre la imagen satelital, ajustada
a los bordes reales del lote (caminos, canales, cambios de cobertura).

Estado: **solo anotado, sin empezar.** Nadie debe arrancarlo sin confirmarlo antes:
toca el flujo de alta de parcela, que es el primer contacto del usuario con el
producto y hoy funciona. Anotado el 2026-08-01 a pedido del dueño del proyecto.

**Aviso del próximo paso satelital.** Decirle al usuario qué día y a qué hora vuelve
a pasar el satélite sobre su parcela. Es calculable sin servicios externos: cada
sensor tiene su ciclo de repetición (`app/services/sensors.py`, `revisit_days`) y la
fecha del último paso ya está en `indices`. Para la hora exacta hace falta el TLE de
la órbita (Celestrak) más una librería tipo `skyfield`; para el MVP alcanza con
"próxima imagen esperada: 3 de agosto (Sentinel-2)". No empezado.

**Explicar los datos, no solo mostrarlos.** Cuando un índice se sale de su media,
poder responder *por qué*: ¿llovió ese día?, ¿hubo nube?, ¿fue un cambio de sensor?
Los insumos ya están: `weather` trae precipitación diaria, y `extra_meta` guarda el
sensor de cada observación. Lo que falta es cruzarlos y redactar la explicación junto
al punto del gráfico. No empezado — pedido explícitamente para "después".

**Enmascarado de nubes.** ✅ Hecho el 2026-08-01 (`_clear_mask` en
`satellite_ingestion.py`, tests en `tests/test_cloud_mask.py`). Descarta las clases
SCL 0, 1, 2, 3, 8, 9, 10 y 11, remuestrea la SCL con vecino más cercano y guarda la
fracción descartada en `extra_meta.masked_frac`. **Sólo aplica a escenas nuevas**: las
observaciones ya ingeridas siguen sin filtrar hasta que se reingesten.

**Aviso del próximo paso satelital.** ✅ Hecho el 2026-08-01
(`app/services/overpass.py`, endpoint `GET /fields/{id}/analysis/next-pass`, se muestra
bajo la tira de miniaturas). Da fecha, no hora: la hora exacta necesita propagar la
órbita real (TLE + SGP4) y estimarla de un promedio sería falsa precisión. Avisa
cuando hubo 2+ pasos sin imagen, que casi siempre es nubosidad.

**Calendario agrícola real.** ✅ Hecho el 2026-08-01 (`app/services/crop_calendar.py`).
Superficies y ventanas del ciclo Otoño-Invierno del Valle de Mexicali tomadas de los
boletines de SADER Baja California, con la liga de cada fuente en el módulo. El mes de
cosecha por cultivo **no** está publicado en esas fuentes y queda explícitamente en
`None` — un dato ausente marcado como ausente vale más que uno inventado. Falta
cablearlo al alta de parcela para que proponga la fecha de siembra por defecto.

---

## 4.2 Calidad del dato satelital — corregido el 2026-08-01

Cuatro errores en el pipeline de ingesta, encontrados al investigar por qué la serie
de NDVI "subía y bajaba de la nada". Ninguno cambia un solo valor ya guardado:
**todos requieren reingesta para surtir efecto.**

| # | Qué estaba mal | Efecto medido |
|---|---|---|
| 1 | Sin enmascarado de nubes | `NDVI = -0.002` en lechuga a media temporada |
| 2 | EVI de Sentinel-2 calculado sobre enteros, no reflectancia | EVI medio 0.562 con NDVI 0.153; mínimo −0.688 |
| 3 | Falta el desplazamiento BOA (−1000) de Sentinel-2 | NDVI 0.150 en vez de 0.243; azul 0.173 (imposible) |
| 4 | El escalado de Landsat convertía "sin dato" en −0.2 de reflectancia | Bordes de parcela contaminados por interpolación |

El #3 es el importante: explica **casi todo** el supuesto "desfase entre sensores"
de −0.157 contra Landsat. Con la corrección la brecha baja a 0.027. No era que los
satélites midieran distinto — era que a uno le faltaba una resta.

También corregido: la ingesta de radar guardaba la colección STAC
(`sentinel-1-rtc`) donde el resto del sistema espera la clave del registro (`s1`),
así que Sentinel-1 aparecía sin nombre y quedaba fuera del aviso de próximo paso
teniendo 20 observaciones.

**Regla nueva de resolución.** Un sensor que daría menos de 50 píxeles para el lote
ya no se ingiere. MODIS cubría un lote de 19 ha en **3 píxeles** y aportó **1
observación de 61**, pero al ser la más reciente secuestraba toda selección de
"última imagen". La regla es por tamaño de lote, no por sensor: con 312 ha o más,
MODIS vuelve solo.

**Pendiente y bloqueante para que esto valga:** reingestar. Hasta entonces los
valores en pantalla son los viejos.

---

## 5. El WIP sin commitear, en el contexto del MVP

Hay ~1.032 líneas sin commitear en `main` que implementan **Active Learning Fase 0** (el
"qué construir primero" de ACTIVE_LEARNING.md): etiquetas de ground-truth en `FieldPhoto`,
migración `008`, extracción de `pest_weather.py`, muestreo de prioridad en `task_generator`,
y 2 archivos de tests nuevos.

**Está verde**: 119 tests pasan, migración aplicada, sin regresiones de lint ni de tipos.

**Decisión pendiente:** el diff mezcla la feature de etiquetas con cambios grandes en
`analysis.py` (+593/−332) y `roles.py` (+293). Antes de seguir con el MVP conviene cerrarlo:
commitear (junto o partido) para que el árbol quede limpio y P0 no se construya encima de
1.000 líneas sin versionar.

Fase 0 **no es un desvío del MVP**: mejora la priorización de scouting hoy, sin ningún modelo.
Lo que sí queda congelado son las Fases 1–4.

---

## 6. Orden de ejecución

```
1. Cerrar el WIP (commit)                    — deja el árbol limpio
2. P0.6 Deploy + dominio + secretos rotados  — sin esto no hay dónde probar nada
3. P0.7 Sentry + PostHog                     — antes de que entre gente, no después
4. P0.2 Email transaccional + verificación   — desbloquea también el reset roto
5. P0.1 MercadoPago TEST → LIVE              — el flujo de plata, punta a punta
6. P0.3 Medición de cuota de IA              — antes de abrir el registro
7. P0.4 Fotos a S3 + validación de upload    — antes de que haya fotos que perder
8. P0.5 Barrida de datos demo                — última pasada antes de abrir
9. Prueba de aceptación completa (§0)        — con una cuenta nueva de verdad
```

P0.6 y P0.7 primero porque todo lo demás se prueba **contra el deploy**, no contra
`localhost`. P0.3 antes de abrir el registro porque es el único ítem que sangra dinero de
forma silenciosa.

---

## 7. Cómo se verificó este documento

```bash
docker exec agrovision-api-1 pytest -q          # 119 passed in 7.03s
docker exec agrovision-api-1 alembic current    # 008 (head)
docker exec agrovision-api-1 alembic heads      # 008 (head) — un solo head
docker exec agrovision-api-1 ruff check app tests   # 5 errores, todos preexistentes
docker exec agrovision-api-1 mypy app               # 60 errores en 21 archivos (baseline)
git status --short --branch                     # main...origin/main, 12 archivos sin commitear
```

Las variables de entorno se auditaron por nombre y por "seteada / vacía" únicamente; ningún
valor de `.env` fue leído ni se reproduce acá.
