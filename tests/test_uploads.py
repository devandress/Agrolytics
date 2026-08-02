"""Validación de fotos subidas.

Lo que se fija acá: que el tipo se decida por el contenido y no por lo que el
cliente afirme, y que un archivo grande se corte antes de estar entero en memoria.
"""

import pytest

from app.services.uploads import (
    MAX_PHOTO_BYTES,
    UnsupportedImage,
    UploadTooLarge,
    read_image_upload,
    sniff_image_extension,
)

_JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 40
_PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 40
_WEBP = b"RIFF" + b"\x00\x00\x00\x00" + b"WEBP" + b"\x00" * 40
_HEIC = b"\x00\x00\x00\x18" + b"ftyp" + b"heic" + b"\x00" * 40


class _FakeUpload:
    """Mínimo UploadFile: sólo `read(n)`, que es lo único que usamos."""

    def __init__(self, data: bytes, filename: str = "foto.jpg"):
        self._buf = data
        self._pos = 0
        self.filename = filename

    async def read(self, n: int = -1) -> bytes:
        if n < 0:
            n = len(self._buf) - self._pos
        out = self._buf[self._pos : self._pos + n]
        self._pos += len(out)
        return out


@pytest.mark.parametrize(
    ("data", "esperado"),
    [(_JPEG, ".jpg"), (_PNG, ".png"), (_WEBP, ".webp"), (_HEIC, ".heic")],
)
def test_recognises_the_formats_a_phone_produces(data, esperado):
    assert sniff_image_extension(data[:16]) == esperado


def test_heic_is_accepted():
    """Es lo que graban los iPhone por defecto: rechazarlo deja afuera medio campo."""
    assert sniff_image_extension(_HEIC[:16]) == ".heic"


def test_non_image_is_rejected():
    assert sniff_image_extension(b"<svg xmlns=...") is None
    assert sniff_image_extension(b"<?php echo 1;") is None
    assert sniff_image_extension(b"") is None


async def test_extension_comes_from_content_not_from_the_name():
    """El nombre lo elige quien sube el archivo. `foto.jpg.svg` guardado tal cual se
    sirve después con JavaScript adentro."""
    data, ext = await read_image_upload(_FakeUpload(_PNG, filename="payload.jpg.svg"))
    assert ext == ".png"
    assert data == _PNG


async def test_a_disguised_script_is_rejected():
    with pytest.raises(UnsupportedImage):
        await read_image_upload(_FakeUpload(b"<svg onload=alert(1)>", filename="foto.jpg"))


async def test_empty_upload_is_rejected():
    with pytest.raises(UnsupportedImage):
        await read_image_upload(_FakeUpload(b"", filename="foto.jpg"))


async def test_oversized_upload_is_cut_off():
    grande = _JPEG + b"\x00" * (MAX_PHOTO_BYTES + 1)
    with pytest.raises(UploadTooLarge):
        await read_image_upload(_FakeUpload(grande))


async def test_the_limit_is_enforced_before_reading_everything():
    """El tope tiene que cortar durante la lectura, no después: si primero se lee
    entero, el archivo enorme ya ocupó la memoria que queríamos evitar."""
    payload = _JPEG + b"\x00" * (5 * 1024 * 1024)
    up = _FakeUpload(payload)
    with pytest.raises(UploadTooLarge):
        await read_image_upload(up, max_bytes=1024)
    assert up._pos < len(payload)


async def test_a_normal_photo_passes():
    foto = _JPEG + b"\x00" * (300 * 1024)
    data, ext = await read_image_upload(_FakeUpload(foto))
    assert ext == ".jpg"
    assert len(data) == len(foto)
