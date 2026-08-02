# Satélites y fusión multi-sensor

Agrolytics combina varios satélites gratuitos para maximizar la **densidad temporal**
manteniendo el **detalle espacial**, normalizando todo a una escala comparable.

## Sensores

| Sensor | Resolución | Revisita | Colección STAC | Uso | Estado |
|---|---|---|---|---|---|
| **Sentinel-2** | 10–20 m | ~5 días | `sentinel-2-l2a` | Backbone óptico de alta resolución | activo |
| **Landsat 8/9** | 30 m | ~8 días | `landsat-c2-l2` | Óptico complementario (más fechas) | activo |
| **MODIS** | 250 m | 16 días (diario en MOD09GA) | `modis-13Q1-061` | Densificación temporal gruesa | activo |
| **Sentinel-1** | 10 m | ~6 días | `sentinel-1-rtc` | Radar, atraviesa nubes | activo |
| **VIIRS / MODIS-09GA diario** | 375–500 m | diario | — | Diario real | requiere NASA Earthdata (configurable) |

Todos vía Microsoft Planetary Computer (URLs firmadas, sin clave) salvo los que
requieren NASA Earthdata. Registro en [sensors.py](../app/services/sensors.py).

**Regla de resolución vs. tamaño de lote.** Un sensor que daría menos de 50 píxeles
para la parcela no se ingiere (`useful_for_field`). MODIS a 250 m cubre un lote de
19 ha en **3 píxeles**: eso no es un mapa, es un número con forma de imagen, y encima
entra en la serie con otra calibración. La regla mira el tamaño del lote, no el
nombre del sensor — con 312 ha o más, MODIS vuelve solo.

---

## Notas a futuro — más resolución

Ninguno de estos está integrado. Se anotan con sus especificaciones reales y, sobre
todo, con **el motivo concreto por el que todavía no están**, que casi nunca es
técnico.

### Gratuitos, sin integrar

| Sensor | Resolución | Revisita | Bandas útiles | Qué falta para encenderlo |
|---|---|---|---|---|
| **HLS** (NASA, Landsat+Sentinel-2 armonizado) | 30 m | 2–3 días | igual que L8/S2 | Es el que yo agregaría **primero**: viene **ya calibrado entre sensores**, o sea que elimina de raíz el desfase que hoy corregimos a mano. Vive en LP DAAC, requiere NASA Earthdata. |
| **CBERS-4A / MUX** (INPE, chino-brasileño) | 16 m | 5 días | azul, verde, rojo, NIR | STAC propio del INPE / Brazil Data Cube (otro cliente). Sin SWIR ni red-edge: sirve para NDVI y EVI, **no** para NDMI ni NDRE. Cobertura centrada en Sudamérica — **verificar que haya escenas sobre el norte de México antes de invertir**. |
| **CBERS-4A / WPM** | 8 m multiespectral · 2 m pan | **31 días** | ídem | La resolución es tentadora, pero un mes entre pasadas no sirve para seguir un cultivo de ciclo corto. |
| **Gaofen-6 / WFV** (China) | 16 m | **4 días** | + **2 bandas red-edge** + amarilla | Único fuera de Sentinel-2 con red-edge, o sea el único que habilitaría NDRE en más fechas. Primer satélite chino dedicado a agricultura de precisión. **El bloqueo es el acceso**: se distribuye vía CRESDA con registro, no con descarga abierta. Hay que resolver si se consigue acceso programático desde México. |
| **Gaofen-1 / WFV** | 16 m | 4 días | azul, verde, rojo, NIR | Mismo bloqueo de acceso, y sin red-edge aporta menos que GF-6. |
| **ECOSTRESS** (térmico, ISS) | 70 m | irregular | temperatura de superficie | Mide **estrés hídrico y evapotranspiración de verdad**, no modelados. Hoy la ET0 sale de un modelo de clima; esto la mediría. Revisita irregular por la órbita de la ISS. |
| **Banda térmica de Landsat (TIRS)** | 100 m (remuestreada a 30) | ~8 días | temperatura de superficie | Ya estamos bajando Landsat: es la fruta más al alcance de la mano para agregar temperatura. |

### De pago

| Sensor | Resolución | Revisita | Nota |
|---|---|---|---|
| **PlanetScope** | **3 m** | **diaria** | Lo que realmente se busca. Suscripción por área monitoreada. |
| **SkySat** | 0.5 m | por encargo | Para inspección puntual, no para monitoreo continuo. |
| **Pléiades / WorldView** | 0.3–0.5 m | por encargo | Sobra para agricultura de lote; su caso de uso es peritaje o litigio. |

### Lo que cambia si se paga imagen

Hoy el costo de Agrolytics **casi no depende de las hectáreas** (ver
[COSTOS.md](COSTOS.md)). La imagen comercial es la única entrada que rompe eso: se
cobra por superficie monitoreada, así que a partir del día que se contrate,
**el costo pasa a ser proporcional al área** y el precio por hectárea deja de ser
una decisión de valor para volverse recuperación de costo. Es un cambio de modelo de
negocio, no una mejora técnica: conviene decidirlo con esa cabeza.

### Antes de sumar cualquier satélite

Dos cosas rinden más que un sensor nuevo, y ninguna cuesta dinero:

1. **Enmascarado de nubes** en todos los sensores, no sólo Sentinel-2 (Landsat trae
   su propia banda `QA_PIXEL`). Más fechas útiles sin bajar una imagen más.
2. **Reingestar** con las correcciones de escala y desplazamiento ya hechas. Los
   valores guardados siguen sesgados; sumar un satélite arriba de datos mal
   escalados sólo agrega otra serie que no cierra con las demás.

### Sobre el pan-sharpening de Landsat

La banda pancromática de Landsat 8/9 es de **15 m**, y combinada con las bandas de
color da una imagen que *parece* de 15 m. Pero esa banda cubre 0.50–0.68 µm — verde
y rojo — y **no incluye el infrarrojo cercano**, que es la mitad de la fórmula del
NDVI. El detalle extra en el NIR no se mide: se interpola guiado por la luz visible.

Para ver bordes de parcela o caminos, excelente. Para decidir dónde regar, es
estructura inventada con forma de dato. Si se agrega, va etiquetado como *realzado*,
nunca como medido — igual que el `is_model`/`confidence` del modelo de plagas.

## La verdad física (por qué fusión)

Ningún satélite gratuito da **diario + alta resolución** a la vez:
- Diario ⇒ sensores gruesos (MODIS/VIIRS 250–750 m).
- Alta resolución (10–30 m) ⇒ Sentinel-2/Landsat cada 2–16 días.

La solución es **fusión**: un backbone de alta resolución (Sentinel-2) + relleno
temporal con el sensor grueso, todo normalizado a una grilla común.

## Cómo se normaliza ([fusion.py](../app/services/fusion.py))

1. **Grilla común** — `resample_to_grid` reproyecta/remuestrea cada ráster a una
   forma común (bilineal), salvando las distintas resoluciones nativas.
2. **Normalización inter-sensor** — sobre fechas casi-coincidentes (±8 días) entre
   un sensor y el backbone, `normalize_gain_offset` ajusta una recta
   `backbone ≈ gain·fuente + offset`. Así un NDVI de Landsat se alinea al de
   Sentinel-2. Ganancias no positivas (pocos pares/ruido) caen a identidad.
3. **Escala fija de color** — `INDEX_SCALE` en
   [raster_renderer.py](../app/services/raster_renderer.py) usa el mismo rango por
   índice (ej. NDVI 0–0.9) para que capas de distintos sensores/fechas sean
   comparables visualmente en el visor.

## Fusión temporal — STARFM-lite (honesto)

`fuse_gap` estima un día sin observación de alta resolución transfiriendo el cambio
temporal del sensor grueso sobre la última imagen de alta resolución:

```
alta(t) ≈ alta(t0) + remuestreo( grueso(t) − grueso(t0) )
```

**Limitaciones (importante):**
- Es una **aproximación**, no STARFM certificado (no modela BRDF ni mezcla espectral
  por endmembers). Los puntos así generados se etiquetan **"fusionado (estimado)"**.
- Un producto fusionado **nunca supera realmente** el detalle de su fuente de alta
  resolución; "rellena" temporalmente, no inventa resolución espacial.
- Si la cobertura de alta resolución ya es densa (pocos días nublados), habrá pocos o
  ningún hueco que rellenar — y eso es correcto.

## API ([analysis.py](../app/api/v1/endpoints/analysis.py))

- `GET /fields/{id}/analysis/timeseries?index=NDVI` — serie multi-sensor etiquetada.
- `GET /fields/{id}/analysis/layers?index=NDVI` — capas (fecha+sensor) para el visor.
- `GET /fields/{id}/analysis/render?index=&date=&sensor=` — PNG normalizado + bounds.
- `POST /fields/{id}/analysis/fuse?index=NDVI` — coeficientes de normalización + serie
  diaria con huecos rellenos.

Ver también [RADAR_SAR.md](RADAR_SAR.md) para el detalle del radar Sentinel-1/SAOCOM.
