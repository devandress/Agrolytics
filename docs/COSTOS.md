# Costo real de operar Agrolytics

**Medido sobre el sistema corriendo el 2026-08-01**, no estimado. Cada número tiene
abajo cómo se obtuvo, para poder recalcularlo cuando cambie algo.

La conclusión adelantada, porque cambia cómo conviene cobrar:

> **El costo casi no depende de las hectáreas.** Es fijo (el servidor) más dos cosas
> que escalan con el *uso*, no con la superficie: las fotos que sube el usuario y las
> llamadas al modelo de lenguaje. Cobrar por hectárea es una decisión de **valor**,
> no de recuperación de costo — y está bien que lo sea, pero conviene saberlo.

---

## 1. Almacenamiento de rásters — irrelevante

Medido sobre `norte`, la única parcela con ingesta real:

```
19.1 ha · 185 rásters · 932 KB en total
```

Eso da **~5 KB por ráster** y **~50 KB por hectárea por año**.

Cuadra con la aritmética: 19.1 ha a 10 m/píxel son ~1.900 píxeles; en float32 son
7,6 KB crudos, y con compresión deflate quedan ~5 KB. Los archivos se nombran por
fecha e índice (`NDVI_20260723.tif`), así que una reingesta sobrescribe en vez de
acumular.

| Superficie | Rásters/año |
|---|---|
| 20 ha | 1 MB |
| 100 ha | 5 MB |
| 1.000 ha | 50 MB |
| 10.000 ha | 500 MB |

**Diez mil hectáreas caben en medio giga por año.** A precio de objeto en S3/R2
(~US$0.015/GB/mes) son **menos de un dólar al año**. Esto no es un costo, es ruido.

## 2. Base de datos — irrelevante

20 MB para 1.424 filas de índices, o sea ~14 KB por fila contando índices y
overhead. Una parcela genera ~300 filas al año (unas 100 pasadas × ~3 índices).

**~4 MB por parcela por año.** Mil parcelas son 4 GB: el plan más chico de cualquier
Postgres gestionado.

## 3. Fotos de campo — **este sí es el costo de almacenamiento**

Una foto de teléfono pesa entre 2 y 5 MB. Con una foto por semana por parcela:

| | Por parcela/año | 100 parcelas/año |
|---|---|---|
| Rásters | 1 MB | 100 MB |
| Filas de DB | 4 MB | 400 MB |
| **Fotos** | **~150 MB** | **~15 GB** |

**Las fotos pesan unas 100 veces más que todo el dato satelital junto.** Y a
diferencia de los rásters, no se pueden regenerar: son el activo de Active Learning.

Dos consecuencias directas:
- Hay que **comprimir del lado del cliente** antes de subir (1600 px de lado largo,
  JPEG calidad 80 baja una foto de 4 MB a ~300 KB sin perder nada útil para ver una
  mancha en una hoja). Eso solo divide el costo por diez.
- Hay que **poner un límite de tamaño en el endpoint**. Hoy no hay ninguno: la subida
  lee el archivo entero en memoria sin validar.

## 4. Reportes con IA — más barato de lo que parece

Medido: el payload que se le manda al modelo para `norte` son **1.509 caracteres ≈
503 tokens**. Sumando el prompt de sistema, la entrada ronda los **900 tokens**; la
salida está topeada en 1.100 y en la práctica da ~700.

Con DeepSeek V4-Flash (US$0.14 por millón de entrada, US$0.28 de salida):

```
entrada   900 tokens × 0.14/1M = US$0.000126
salida    700 tokens × 0.28/1M = US$0.000196
                                 ─────────────
por reporte                      US$0.00032
```

**Un reporte cuesta tres décimas de milésimo de dólar.** Mil reportes: **32 centavos.**
Y el prompt de sistema se cachea (US$0.0028 por millón en cache hit), así que a
volumen baja todavía más.

Generar tareas con IA cuesta parecido (~US$0.0002, el tope de salida es 700).

**Implicancia:** el "costo de IA" no es motivo para limitar reportes. Un usuario
tendría que pedir **3.000 reportes** para gastar un dólar. El límite mensual
(`ai_monthly`) sirve como freno contra abuso automatizado, no como recuperación de
costo.

## 5. Cómputo — casi todo es la ingesta

El modelo de riesgo por píxel es NumPy vectorizado sobre ~2.300 píxeles: son
milisegundos. Correrlo "diario" para mil parcelas no se nota.

Lo que sí cuesta CPU es **bajar y recortar las bandas**: unos 5–15 segundos por
escena por parcela. A ~100 escenas al año son **~20 minutos de CPU por parcela por
año**. Mil parcelas = ~14 días-CPU al año, o sea **menos de un núcleo corriendo
todo el tiempo**.

El dato satelital en sí es **gratis** (Copernicus, USGS y Planetary Computer no
cobran ni por consulta ni por descarga).

## 6. Costo fijo — el que de verdad manda

| Componente | Referencia mensual |
|---|---|
| API + worker | US$14–50 |
| Postgres con PostGIS | US$0–25 |
| Redis | US$0–10 |
| Almacenamiento de objetos | US$1–5 |
| Sentry + PostHog | US$0 (plan gratis alcanza al principio) |
| **Total** | **~US$25–90/mes** |

Esto se paga **con un cliente o con mil**. Es el piso.

---

## 7. Qué significa para el precio

Con costo variable prácticamente nulo, el punto de equilibrio lo fija el costo fijo:

```
US$50/mes de infraestructura ÷ precio del plan = clientes para no perder plata
```

A US$25/mes por cliente, **dos clientes** cubren la operación. Todo lo demás es
margen, hasta escalas muy grandes.

Los tres números que conviene vigilar cuando crezca, en este orden:

1. **Gigas de fotos.** Es lo único que crece rápido y no se puede tirar.
2. **Horas de worker.** Si la ingesta se vuelve lenta, hay que pagar más worker, y
   ahí la regla de resolución (no ingerir sensores demasiado gruesos para el lote)
   ya ahorra trabajo inútil.
3. **Llamadas al modelo**, pero recién importa arriba de decenas de miles al mes.

**Lo que NO conviene hacer:** limitar reportes o hectáreas "por costo". No cuesta.
Si se limitan, que sea por posicionamiento de producto, y decirlo así internamente
para no terminar creyendo una restricción que no existe.
