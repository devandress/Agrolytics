# Active Learning — arquitectura (curación → validación)

## Punto de partida real (no especulativo)

Hoy **no hay modelo entrenado**. El riesgo de plagas es 100% reglas agronómicas
calibradas con literatura (UC IPM), no con datos del rancho — ver
[pest_model.py](../app/services/pest_model.py) y [pest_catalog.py](../app/services/pest_catalog.py).
La única señal de campo que ya se captura es
[`FieldPhoto`](../app/models/field_photo.py): una foto + `alert_confirmed`
(`True`/`False`/`None`) — confirma o corrige una alerta, sin especie ni severidad.
[PEST_STRATEGY.md](PEST_STRATEGY.md) ya nombra esto como "Capa 4 — Verdad de campo
(roadmap)": este documento es esa capa, en detalle y accionable.

Esta arquitectura describe cómo llegar de "reglas + un booleano" a "modelo
calibrado con datos reales del rancho", sin asumir volumen de datos que no existe
todavía (cold start real: probablemente docenas de fotos al principio, no miles).

## Por qué "activo" y no solo "recolección pasiva"

Recolección pasiva = el agricultor sube fotos cuando quiere. Eso sesga el dataset
hacia lo obvio (alertas "alto" que ya eran evidentes) y nunca junta negativos
limpios. **Active learning real** = el sistema elige *qué pedir que se fotografíe*
para maximizar lo que aprende por cada foto, priorizando los casos donde el
modelo actual está más inseguro.

Punto clave: el modelo de reglas **ya calcula incertidumbre**, aunque no sea ML.
`assess_pest()` en pest_model.py devuelve `confidence`/`confidence_pct` (cuántas
señales reales tenía) y `level` (bajo/medio/alto). Un caso `level=medio` o
`confidence=baja` es exactamente el caso más informativo para pedir una foto —
la política de muestreo activo puede arrancar **hoy, sin ningún modelo de ML**,
priorizando esos casos en la cola de "confirmá en campo". Esto es la Fase 0.

## Fases

```
Fase 0  Muestreo activo sobre el modelo de reglas (sin ML)     ← construible ya
Fase 1  Taxonomía de etiquetado real + cola de anotación        ← más UI/schema
Fase 2  Clasificador bootstrap (transfer learning, pocos datos) ← primer modelo
Fase 3  Active learning con incertidumbre del modelo real       ← loop completo
Fase 4  Producción: sombra → canary → reemplazo parcial de reglas
```

No saltar fases. Un clasificador entrenado con 40 fotos desbalanceadas es peor
que las reglas actuales — el `is_model:true` / `confidence` ya comunican esa
honestidad, no se puede perder eso al meter ML.

---

## 1. Curación — qué pedir fotografiar, y cuándo

**Política de muestreo (Fase 0, sin ML):**

| Señal existente | Prioridad de pedir foto |
|---|---|
| `level=alto`, `confidence=alta` | Baja — el modelo ya está seguro, la foto solo audita |
| `level=medio` (cualquier confianza) | **Alta** — zona de decisión, máxima información por foto |
| `level=alto`, `confidence=baja/media` | **Alta** — score alto con pocos datos reales, foto barata de validar |
| `level=bajo` | Media, muestreo aleatorio esporádico (necesario para negativos limpios) |

Regla dura: **nunca pedir foto solo en positivos**. Sin negativos etiquetados el
clasificador aprende a decir "sí, plaga" siempre. Forzar un % fijo (ej. 20%) de
pedidos de foto en `level=bajo` al azar, aunque el usuario no lo pediría solo.

**Dónde vive esto:** nuevo campo derivado, no persistido — se calcula al generar
tareas (`task_generator.py`) y al mostrar la tabla de Plagas en el panel derecho
(`static/index.html`, tab "Plagas"). Tareas de tipo `REVISAR` ya existen; agregar
`photo_priority: alta|media|baja` al payload de tarea generado, y que el mobile
"Nuevo reporte" (mockup ya diseñado) lo muestre como badge.

**Fase 3 (con modelo real):** reemplazar la tabla de prioridad fija por
incertidumbre real del clasificador — margen entre las dos clases con mayor
probabilidad, o entropía de la distribución softmax. Mismo mecanismo, señal más
fina.

## 2. Taxonomía de etiquetado

Lo que hay hoy (`alert_confirmed: bool|None`) no alcanza para entrenar un
clasificador de especie. Ampliar el modelo de dato, no reemplazarlo:

```python
class FieldPhoto(Base):
    ...
    alert_confirmed: bool | None       # se mantiene (compat)
    pest_key: str | None                # FK lógica a PEST_CATALOG (ej. "downy_mildew")
    severity: str | None                # "bajo" | "medio" | "alto" — coincide con mockup mobile
    label_source: str                   # "farmer" | "agronomist" | "model_assisted"
    label_confidence: str | None        # cuánto confía el ANOTADOR, no el modelo
    reviewed_by: uuid | None            # agrónomo que auditó (control de calidad, ver §5)
    reviewed_at: datetime | None
```

`pest_key` usa las mismas claves que `PEST_CATALOG` (`downy_mildew`, `sclerotinia`,
`botrytis`, ...) — cero taxonomía nueva que inventar, ya está en el catálogo y ya
es lo que el agricultor ve en pantalla. Agregar `"sano"` y `"otro"` como valores
válidos (negativo explícito y catch-all, ambos necesarios para el dataset).

Clasificación de imagen completa (whole-image multi-label), **no detección de
objetos** con bounding boxes — con el volumen de datos esperado, pedirle al
agricultor que dibuje una caja es fricción que mata la adopción, y un
clasificador simple ya cubre "qué plaga es esta foto" que es la pregunta real.

## 3. Flujo de etiquetado (anotación)

Dos fuentes de etiqueta, con jerarquía de confianza distinta:

- **Farmer-in-the-loop** (ya existe la mecánica, falta el campo): el flujo mobile
  "Nuevo reporte" ya diseñado (tipo → plaga → severidad → foto → confirmar) ES el
  formulario de etiquetado. No hay que construir una UI de labeling separada —
  el labeling tool y la UX del agricultor son el mismo flujo. Esto es la decisión
  de diseño más importante: pedirle a un agricultor que "etiquete datos" en un
  tool aparte no pasa; pedirle que reporte un problema en su campo, sí.
- **Auditoría por agrónomo** (nueva, liviana): cola de revisión — un agrónomo (o
  el propio equipo al principio) revisa una muestra de fotos etiquetadas por
  farmers y confirma/corrige. Alimenta `reviewed_by`/`reviewed_at`. No se audita
  el 100%; auditar el 100% no escala y no hace falta — auditar una muestra
  estratificada por `pest_key` (para no perderse las especies raras) alcanza para
  medir la tasa de acuerdo farmer-vs-agrónomo, que es la métrica de calidad de
  ese dataset.

Endpoint nuevo, mínimo, extiende el ya existente:
```
PATCH /fields/{id}/photos/{photo_id}/label
  { pest_key, severity, label_source }

POST /photos/review-queue/{photo_id}          # solo agrónomo/staff
  { reviewed_pest_key, reviewed_severity, agree: bool }
```

## 4. Dataset — almacenamiento y versionado

**Riesgo de infraestructura real, no hipotético:** el free tier de Render
(`render.yaml`, ya documentado en este repo) usa **filesystem efímero** — cada
redeploy borra `/app/data`, donde hoy se guardan las fotos
(`DATA_DIR/photos/{field_id}/...`). Esto no es aceptable para datos de
entrenamiento: perder el dataset en cada deploy tira el trabajo de curación.
**Antes de acumular fotos con intención de entrenar, mover el storage de fotos a
objeto persistente** (Cloudflare R2 — ya está en el roadmap de backups del propio
`render.yaml`/DEPLOYMENT.md, mismo mecanismo sirve para ambos). Bloqueante real
para cualquier fase después de la 1.

Versionado del dataset de entrenamiento (no versionar fotos individuales, sí el
**snapshot usado para entrenar cada modelo**):
```
datasets/
  v1_2026-08/
    manifest.json      # photo_id, pest_key, severity, label_source, split
    train/  val/  test/  (symlinks o referencias a object storage, no copias)
```
`manifest.json` es lo que se versiona en git (chico, texto); las fotos quedan en
R2. Split train/val/test **estratificado por `field_id`**, no solo por foto — dos
fotos del mismo lote en la misma semana están correlacionadas; si caen una en
train y otra en test, el val score miente (data leakage por proximidad espacial
y temporal, además del riesgo agronómico ya bien entendido en este repo respecto
a Sentinel-2 revisit).

## 5. Entrenamiento

**Cold start real:** con docenas/cientos de fotos, entrenar desde cero no
funciona. Transfer learning sobre un backbone de visión pre-entrenado
(EfficientNet/ResNet/ViT chico, congelado salvo la cabeza) es la única opción
razonable en Fase 2. Reentrenar la cabeza solamente, no el backbone completo,
hasta tener miles de ejemplos por clase.

Clases: una por `pest_key` activo en el catálogo + `sano` + `otro`. Desbalanceado
por diseño (algunas plagas son raras) — usar class weights o focal loss, no
accuracy plana como métrica (ver §6).

Reentrenamiento: **no continuo por defecto**. Job manual/programado
(`app/tasks/`, patrón Celery ya existente en el repo) que dispara cuando:
- se junta un mínimo de N etiquetas nuevas desde el último snapshot (ej. 50), **o**
- pasa un intervalo fijo (ej. mensual) si hay al menos algunas etiquetas nuevas.

Reentrenar por cada foto individual es ruido, no señal, con este volumen.

## 6. Validación — antes de que el modelo toque a un usuario real

Tres puertas, en orden, **todas** obligatorias:

1. **Holdout offline.** Métrica por clase (recall por plaga, no accuracy global —
   fallar en detectar la plaga rara es el error caro, no el frecuente). Reportar
   matriz de confusión completa, no un solo número.
2. **Shadow mode.** El modelo corre en paralelo a las reglas actuales, sobre
   tráfico real, **sin mostrarse al usuario**. Se loguea el desacuerdo
   modelo-vs-reglas. Mínimo 2-4 semanas o N alertas evaluadas (lo que llegue
   primero) antes de pasar a canary. Esto es lo que evita que un modelo con buen
   holdout pero mal comportamiento en producción (drift, inputs que el holdout no
   cubría) llegue a un agricultor real.
3. **Canary con agrónomo humano en el loop.** El modelo se muestra, pero cada
   predicción `alto` que el modelo genere y las reglas NO generaban (o viceversa)
   se marca para revisión antes de convertirse en alerta visible. Ampliar
   gradualmente el % de tráfico solo si la tasa de desacuerdo con el agrónomo se
   mantiene baja.

**Regla de reemplazo:** el modelo de ML **complementa**, no reemplaza, el score
de reglas hasta que en shadow mode supere al modelo de reglas en recall por
clase con intervalo de confianza que no se solape (no "un punto porcentual mejor
en una corrida"). Mientras tanto, mostrar ambos con el mismo patrón de honestidad
que ya usa `confidence` hoy: nunca presentar una predicción de ML como hecho
verificado en campo — mismo principio que ya está en `_accion()` de
pest_model.py, se hereda tal cual.

## 7. Despliegue

- **Registro de modelos:** aunque sea un directorio versionado en object storage
  (`models/v{n}/model.onnx` + `metadata.json` con métricas de validación) alcanza
  para el volumen esperado — no hace falta MLflow/similar todavía.
- **Servido:** el modelo corre en un task de Celery (patrón ya existente,
  `app/tasks/`), no inline en el request path del API — inferencia de imagen no
  debe bloquear un endpoint síncrono, mismo principio ya aplicado a la ingesta
  satelital en este repo.
- **Rollback:** un solo puntero (`current_model_version`) en config; volver a la
  versión anterior es cambiar ese puntero, no un redeploy.
- **Fallback obligatorio:** si el servicio de inferencia falla o no hay modelo
  desplegado, caer al modelo de reglas silenciosamente — el usuario nunca debe
  ver un error por esto, las reglas son el piso, no el fallback de emergencia.

## 8. Monitoreo y loop continuo

- **Drift de entrada:** distribución de `pest_key`/`severity` reportado por
  farmers, por semana. Un cambio brusco (nueva plaga apareciendo, o un cultivo
  nuevo sin cobertura en el catálogo) es señal de re-curar, no de re-entrenar a
  ciegas.
- **Tasa de desacuerdo farmer-vs-agrónomo** (de la cola de auditoría, §3): si
  sube, el problema es la calidad del etiquetado de campo, no el modelo — atacar
  ahí primero (mejor UI de reporte, ejemplos visuales de referencia por plaga en
  el flujo mobile) antes de tocar el modelo.
- **La cola de muestreo activo se retroalimenta:** los casos donde farmer y
  modelo (una vez que exista) discrepan son automáticamente alta prioridad para
  la próxima ronda de fotos — cierra el loop de Fase 3 sin trabajo manual
  adicional, reusando el mismo mecanismo del §1.

## 9. Gobernanza y riesgos

- **Privacidad:** las fotos son de campos privados con GPS. Confirmar que
  `privacy.html`/`terms.html` (ya existen en `static/`) cubren explícitamente el
  uso de fotos para entrenar modelos — hoy no está claro que lo hagan, revisar
  antes de la Fase 1 real (no bloqueante para diseñar, sí para operar).
- **Sesgo geográfico:** el catálogo y el modelo de reglas están calibrados para
  vegetales costeros de California (ver PEST_STRATEGY.md). Un dataset que crece
  solo con usuarios de una región va a producir un modelo que generaliza mal a
  otras zonas — esto ya es cierto hoy con las reglas, pero un modelo de ML lo
  vuelve invisible (menos auditable que un umbral explícito). Trackear
  `field.location`/región en el manifest del dataset, no asumir homogeneidad.
- **No hay atajos de volumen:** con el tamaño de usuario base actual, la Fase 2
  (primer clasificador) probablemente tarda meses en tener suficientes etiquetas
  por clase, no semanas. Diseñar la Fase 0/1 para que aporten valor real
  (mejores prioridades de scouting, mejor UX de reporte) sin depender de que la
  Fase 2 llegue rápido — si el ML nunca despega, Fase 0/1 igual valieron la pena.

## Qué construir primero (si hay que elegir uno)

**Fase 0 + el campo `pest_key`/`severity` en `FieldPhoto` (§1 + primera mitad de
§2).** Es el cambio de menor riesgo, no requiere ningún modelo, mejora la
priorización de scouting ya hoy, y es el prerequisito de datos para todo lo
demás. Sin esto, ninguna fase posterior tiene con qué entrenar.
