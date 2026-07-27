# Estrategia de control de plagas — AgroVision (vegetales costeros)

## El límite honesto
Los satélites **localizan estrés, no identifican especies**. NDVI/NDMI dicen *dónde* y *cuánto*,
pero no *qué* plaga. La precisión real se logra combinando capas de datos.

## Estrategia multi-capa

**Capa 1 — Modelo clima + fenología (implementado).**
Por cada plaga del catálogo se calcula un **riesgo calibrado** con datos reales:
- **Hongos** (mildiú, botrytis, esclerotinia, oídio): ventana de temperatura + humedad relativa +
  **horas de humedad foliar** (horas con HR≥90 % de Open-Meteo). Sin agua libre → riesgo bajo
  aunque haya inóculo.
- **Insectos** (DBM, pulgón, trips, gusano soldado, mosca blanca): **grados-día acumulados**
  (base de desarrollo por especie) + ventana de temperatura actual → presión / generaciones.
- Modulado por la **etapa fenológica** del cultivo (días desde siembra).
Código: [pest_catalog.py](../app/services/pest_catalog.py) + [pest_model.py](../app/services/pest_model.py).
Cada resultado trae **drivers** (por qué) y un **tip de scouting** (qué buscar en campo).

**Capa 2 — Satélite para focalizar (implementado).**
Anomalías de NDVI/NDMI indican *dónde* ir a revisar; el visor de rásters + pines llevan al punto.

**Capa 3 — Catálogo por zona, configurable (implementado).**
Lista costera de CA predefinida + el agricultor **activa/agrega** las plagas que le importan en su
zona (`/fields/{id}/pest-catalog`). El modelo evalúa solo las activas.

**Capa 4 — Verdad de campo (roadmap).**
Para máxima precisión: registro de **scouting/trampas** (conteos) y **foto → identificación por IA
visual**. Esto convierte los umbrales en **modelos calibrados** con datos reales del rancho (el
volante de datos). Ya capturamos fotos de validación; falta el conteo y el clasificador de imagen.

## Cómo se ve en la app
Vista **Análisis → Seguimiento en el tiempo → Plagas**: tabla de interpretación por plaga (riesgo,
drivers, scouting) + "Gestionar plagas de mi zona". Pestañas hermanas: Fenología, Índices, Clima/tareas.

## Endpoints
- `GET /fields/{id}/pests` — riesgo multi-plaga (clima + grados-día + humedad foliar).
- `GET|PATCH /fields/{id}/pest-catalog` — ver/activar/agregar plagas por zona.
- `GET /fields/{id}/phenology` — etapa, días, NDVI esperado vs real, vigor.

## Para subir la precisión (siguiente fase)
1. **Scouting/trampas**: tabla de conteos por fecha → calibra umbrales y valida el modelo.
2. **Foto→IA**: clasificador de imagen sobre las fotos de campo (identifica especie/daño).
3. **Calibración** de grados-día/umbrales con el historial real del productor por cultivo y zona.
