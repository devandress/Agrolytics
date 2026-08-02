"""Validación de archivos subidos por el usuario.

La subida de fotos hacía `dest.write_bytes(await file.read())`: leía el archivo
entero en memoria, sin límite, y le ponía la extensión que dijera el nombre que
mandó el cliente. Tres problemas en una línea —

1. **Sin tope de tamaño.** Un archivo de 2 GB entra completo a la RAM del proceso.
   Con varios a la vez, el servicio se cae y no hace falta mala intención: una foto
   moderna de teléfono ya pesa entre 3 y 12 MB.
2. **La extensión venía del cliente.** `foto.jpg.svg` o `foto.html` se guardaban tal
   cual y después se sirven de vuelta; un SVG lleva JavaScript adentro.
3. **Sin verificar que sea una imagen.** El `content-type` del multipart también lo
   elige el cliente, así que tampoco sirve de prueba.

Acá se lee de a pedazos con tope, y el tipo se decide por los **números mágicos**
del contenido, no por lo que el cliente afirme. Sin dependencias nuevas: son unos
pocos bytes al principio del archivo.
"""

from __future__ import annotations

# Formatos que sale un teléfono. HEIC entra porque es lo que graban los iPhone por
# defecto desde iOS 11 — rechazarlo dejaría afuera a la mitad del campo.
_SIGNATURES: tuple[tuple[bytes, str], ...] = (
    (b"\xff\xd8\xff", ".jpg"),
    (b"\x89PNG\r\n\x1a\n", ".png"),
)
_HEIF_BRANDS = frozenset({b"heic", b"heix", b"hevc", b"heim", b"heis", b"mif1", b"msf1"})

# Con compresión del lado del cliente una foto de campo queda en ~300 KB. 12 MB deja
# margen de sobra para una original sin comprimir y sigue estando lejos de un
# tamaño que ponga en riesgo al proceso.
MAX_PHOTO_BYTES = 12 * 1024 * 1024
_CHUNK = 64 * 1024


class UploadTooLarge(Exception):
    """El archivo supera el tope permitido."""


class UnsupportedImage(Exception):
    """El contenido no es una imagen de un formato aceptado."""


def sniff_image_extension(head: bytes) -> str | None:
    """Extensión según los primeros bytes del archivo, o ``None`` si no es imagen.

    Se mira el contenido y no el nombre ni el content-type porque los dos los
    elige quien sube el archivo.
    """
    for magic, ext in _SIGNATURES:
        if head.startswith(magic):
            return ext
    # WebP: "RIFF" + 4 bytes de tamaño + "WEBP"
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return ".webp"
    # HEIF/HEIC: caja `ftyp` con una marca conocida en los bytes 4..12
    if head[4:8] == b"ftyp" and head[8:12] in _HEIF_BRANDS:
        return ".heic"
    return None


async def read_image_upload(file, max_bytes: int = MAX_PHOTO_BYTES) -> tuple[bytes, str]:
    """Leer una imagen subida con tope de tamaño. Devuelve ``(bytes, extensión)``.

    Lee de a pedazos y corta apenas se pasa del tope, así un archivo enorme no llega
    nunca a estar entero en memoria. Levanta :class:`UploadTooLarge` o
    :class:`UnsupportedImage`; el endpoint las traduce a un mensaje para el usuario.
    """
    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = await file.read(_CHUNK)
        if not chunk:
            break
        total += len(chunk)
        if total > max_bytes:
            raise UploadTooLarge(f"El archivo supera {max_bytes // (1024 * 1024)} MB.")
        chunks.append(chunk)

    data = b"".join(chunks)
    if not data:
        raise UnsupportedImage("El archivo llegó vacío.")
    ext = sniff_image_extension(data[:16])
    if ext is None:
        raise UnsupportedImage("El archivo no es una imagen (se aceptan JPG, PNG, WebP y HEIC).")
    return data, ext
