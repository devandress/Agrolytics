# QA — estado al 2026-08-01

Dos partes: lo que se verificó automáticamente (con el resultado), y **lo que una
persona tiene que probar a mano** porque depende de credenciales, cámara o dinero.

Antes de nada, la sección 3: **fallas conocidas**. Que el tester la lea primero
ahorra media jornada de reportar cosas que ya sabemos.

---

## 1. Verificado automáticamente

| Chequeo | Resultado |
|---|---|
| Suite de tests | **175 pasan** |
| Lint (`ruff`) | 2 avisos preexistentes (C416), ninguno nuevo |
| Sintaxis del frontend | JS válido, CSS con llaves balanceadas |
| Handlers del frontend | Ninguna función invocada desde HTML que no exista |
| **Pertenencia de datos** | **11/11 endpoints con `{field_id}` filtran por dueño** |
| Endpoints sin autenticación | 7, **todos correctos**: registro, login, refresh, logout, recuperación de contraseña y el webhook de MercadoPago |
| Arranque en configuración de producción | OK — `/docs` oculto, `/health` presente, CORS restringido |
| Secretos en archivos versionados | Ninguno. `.env` está en `.gitignore` |
| Migraciones | `009 (head)`, una sola cabeza |

### Sobre el chequeo de pertenencia

Una primera pasada marcó 11 endpoints como "sin verificar dueño". **Era un falso
positivo del análisis**: esos endpoints no llaman a un helper `_own()`, filtran
inline con `Field.user_id == current_user.id` dentro del `WHERE`. Se revisaron los
11 uno por uno y todos filtran correctamente, incluidos los de escritura
(`PATCH /pest-catalog`) y los que sirven archivos (`/rasters/render`).

---

## 2. Lo que tiene que probar una persona

Yo no puedo: no inicio sesión con credenciales ajenas, no hago pagos, y la cámara
necesita un dispositivo real.

### Cuenta y sesión
- [ ] Registro con correo nuevo → entra directo
- [ ] Login y logout
- [ ] Cambio de contraseña
- [ ] **Recuperar contraseña**: llega el correo y el enlace abre (ver §3 — hoy no hay SMTP)
- [ ] Sesión expirada (30 min): ¿vuelve al login sin perder lo que estaba haciendo?

### Alta de parcela
- [ ] Dibujar polígono en el mapa
- [ ] Buscar un lugar por nombre
- [ ] Pegar coordenadas a mano
- [ ] Elegir cultivo → **aparece la sugerencia de fecha de siembra** del calendario oficial y el botón "Usar esta fecha"
- [ ] Esperar la ingesta (1–3 min la primera vez): ¿la pantalla de espera se actualiza sola?
- [ ] Superar el límite de parcelas del plan → mensaje claro, no error 500

### Mapa y análisis
- [ ] **Rueda del mouse sobre el mapa: baja la página, NO hace zoom.** `Ctrl`/`⌘` + rueda sí hace zoom, con aviso la primera vez
- [ ] Tira de miniaturas: cada fecha muestra su vista previa; clic cambia el mapa grande
- [ ] Flechas ‹ › recorren fechas
- [ ] Cambiar de índice (NDVI/NDMI/NDRE/EVI/RIESGO) mantiene la fecha elegida
- [ ] Panel lateral: abre, cierra, y **no aplasta el mapa**
- [ ] Redimensionar la ventana: el mapa se reajusta sin franjas negras
- [ ] Entre 769 y 1200 px de ancho: la barra lateral se reduce a iconos y se despliega al pasar el mouse

### Tareas
- [ ] Agrupadas por urgencia con su conteo
- [ ] **Toda tarea tiene pin**; distingue "Punto exacto" de "Toda la parcela"
- [ ] Botón de pin centra el mapa y abre el globo
- [ ] "Cómo llegar" abre Google Maps en el punto correcto
- [ ] Marcar hecha desaparece de pendientes y baja el contador del lote en la barra lateral
- [ ] Crear tarea con clic en el mapa
- [ ] **En teléfono**: la tarjeta no parte el texto en dos palabras por renglón

### Reportes
- [ ] "Generar con IA" produce un parte legible en español
- [ ] El desplegable "Mediciones con las que se escribió" muestra el JSON
- [ ] **Verificar que cada número del texto exista en ese JSON** (la IA tiene prohibido inventar; si aparece un dato que no está ahí, **es un bug y hay que reportarlo**)
- [ ] Parcela sin datos → dice que faltan mediciones, no inventa un reporte
- [ ] "Descargar PDF" imprime sólo el reporte

### WhatsApp
- [ ] El mensaje **no tiene asteriscos ni guiones bajos sueltos**
- [ ] Se lee ordenado: parcela · qué pasa · qué hacer · dónde · cómo llegar · cuándo
- [ ] El enlace "Cómo llegar" abre el punto correcto
- [ ] Los tres pasos (qué mando → a quién → mensaje) se entienden sin explicación

### Tema y accesibilidad
- [ ] Botón de tema alterna claro/oscuro y lo recuerda al recargar
- [ ] Sin preferencia guardada, respeta el tema del sistema operativo
- [ ] Al recargar en oscuro **no hay destello blanco**
- [ ] Legible al sol en un teléfono (es el caso de uso real)

### Cobro — sólo con credenciales de prueba cargadas
- [ ] `/billing/plans` deja de responder `"sandbox": true`
- [ ] Checkout devuelve `init_point` y abre MercadoPago
- [ ] Pago de prueba aprobado → **el webhook llega y el plan pasa a `pro` en la base**
- [ ] Cancelar → vuelve a `free`
- [ ] Los límites del plan se aplican del lado del servidor, no sólo en la interfaz

---

## 3. Fallas conocidas — no reportar

1. **Los valores de índices están sesgados hacia abajo.** Se corrigieron cuatro
   errores del pipeline (nubes, escala del EVI, desplazamiento BOA, "sin dato" de
   Landsat) pero **ninguno toca los datos ya guardados**. El NDVI de Sentinel-2 se ve
   ~0.15 donde debería estar en ~0.24. Se arregla reingestando.
2. **No hay verificación de correo ni SMTP configurado.** Recuperar contraseña no
   envía nada.
3. **El cobro está en modo preview** hasta que se carguen las credenciales de
   MercadoPago: no genera `init_point` ni cobra.
4. **Las fotos se guardan en disco efímero.** En un deploy sin volumen persistente
   se pierden al reiniciar. Tampoco hay límite de tamaño en la subida.
5. **Las etiquetas de plaga (`pest_key`, `severity`) no tienen interfaz.** El backend
   las acepta; la pantalla todavía sólo pregunta "¿la alerta es correcta?".
6. **Los 5 roles son vistas, no permisos.** El selector no restringe nada.
7. **Cifras de dinero y agua/SGMA son de demostración.** Van etiquetadas.
8. **Hay filas duplicadas** en `indices` (108 grupos). Se deduplica al leer, así que
   no se ve, pero está.

---

## 4. Antes del deploy

Bloqueantes reales, en orden:

1. **Rotar `JWT_SECRET` y `DEEPSEEK_API_KEY`** (`openssl rand -hex 32`). Los actuales
   estuvieron en un `.env` de desarrollo.
2. **`PUBLIC_BASE_URL`** con el dominio real y `https://`. Si queda en localhost
   **la app no arranca a propósito** — los enlaces de recuperación saldrían rotos.
3. **`CORS_ORIGINS`** con dominios reales, sin `*`. También verificado al arrancar.
4. **`APP_ENV=production`** y **`DOCS_ENABLED=false`**.
5. **Base con PostGIS** y `alembic upgrade head`.
6. **Volumen persistente para `DATA_DIR`**, o las fotos se pierden.
7. **Sentry y PostHog**: el código ya está, sólo faltan las claves.
8. **MercadoPago en TEST** y probar el flujo entero antes de poner `APP_USR-`.

`render.yaml` ya declara las variables necesarias, incluidas las de cobro y
`PUBLIC_BASE_URL`, todas como `sync: false` para cargarlas en el panel.
