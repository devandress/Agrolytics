# Agrolytics — Roadmap

## Hecho (esta entrega)
- **5 vistas por rol** sobre un solo login (selector en la barra): Dueño, Mayordomo, Regador,
  Agrónomo, Trabajador.
- **Tareas de campo persistentes** (`field_tasks`): generadas desde los índices satelitales y
  umbrales por cultivo ([anomaly.py](../app/services/anomaly.py)); priorizadas; se marcan "hecho".
- **Fotos de validación** (`field_photos`): el usuario sube una foto y confirma/corrige la alerta.
- **Salud del rancho** agregada (score 0–100), portafolio con riesgo de plaga (demo), plan de riego.
- Cifras de dinero y agua/SGMA como **valores de demostración** claramente etiquetados
  ([demo_data.py](../app/core/demo_data.py)).

## Fase 2 — Funciones transversales (alto valor, diferidas)
Sirven a casi todos los roles y ningún competidor genérico las resuelve bien para este mercado:

1. **WhatsApp / SMS** (Twilio) — alertas y tareas donde el usuario ya vive; no requiere instalar app.
2. **Voz en español** — instrucciones habladas (TTS) para usuarios con lectura limitada.
3. **Modo offline** — PWA + service worker; el campo no tiene señal.
4. **Foto → entrenamiento de IA** — las fotos de validación alimentan un volante de datos que mejora
   la detección con cada cliente (ya estamos capturando `alert_confirmed`).

## Fase 3 — Datos reales (reemplazar demos)
- Integración contable real para ahorros (precio agua/insumos por cliente).
- Asignación SGMA real por API/regulador en lugar de `demo_data.py`.
- Humedad de suelo con **SAOCOM / NISAR banda L–S** además de Sentinel-1 (ver [RADAR_SAR.md](RADAR_SAR.md)).
- Calibración de rendimiento con cosechas históricas del productor.

## Fase 4 — Roles enterprise + seguridad por rol
- **Cooperativa / comprador** (Driscoll's, Dole): tablero agregado de proveedores, predicción de
  volumen, reportes de sostenibilidad, benchmarking entre ranchos.
- **Aseguradora / banco**: API de verificación de condición de cultivo, paquetes de evidencia
  histórica para reclamos, score de riesgo por parcela.
- **Control por rol real en auth** (hoy el selector de rol es de demostración; cualquiera cambia de
  vista). Asociar el rol al usuario y gatear endpoints/vistas según permisos.
- **White-label** para el agrónomo/PCA: reportes con su marca para sus clientes.
