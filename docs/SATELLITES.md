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
