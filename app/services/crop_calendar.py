"""Calendario agrícola real del Valle de Mexicali, Baja California.

Hasta ahora las fechas de siembra de la demo salían de la nada, y eso contamina todo
lo que depende de ellas: la fenología compara contra un ciclo inventado, el vigor
esperado no significa nada y las tareas se generan contra una etapa equivocada.

Estos datos son de fuentes oficiales publicadas, con la liga de cada uno. Lo que la
fuente NO dice tampoco se inventa acá: el mes de cosecha por cultivo no está en los
boletines consultados, así que sólo se registra la ventana del ciclo, que sí es
oficial. Un dato ausente marcado como ausente vale más que uno plausible inventado.

Fuentes:
  - SADER/Agricultura Baja California, "Comienza la siembra de hortalizas del ciclo
    agrícola Otoño-Invierno 2019-2020 en el Valle de Mexicali".
    https://www.gob.mx/agricultura|bajacalifornia/articulos/comienza-la-siembra-de-hortalizas-del-ciclo-agricola-otono-invierno-2019-2020-en-el-valle-de-mexicali
  - SADER/Agricultura Baja California, "Sembradas más de 10,431 hectáreas con
    hortalizas en el Valle de Mexicali" (ciclo 2020-2021).
    https://www.gob.mx/agricultura|bajacalifornia/articulos/sembradas-mas-de-10-431-hectareas-con-hortalizas-en-el-valle-de-mexicali
  - SIAP, Calendario Agrícola y Estacionalidad (definición del ciclo Otoño-Invierno).
    https://nube.agricultura.gob.mx/calendario_agricola/
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

# El ciclo Otoño-Invierno arranca oficialmente el 1 de octubre y corre hasta marzo.
# En el Valle de Mexicali las siembras de hortaliza empiezan en noviembre.
CYCLE_OI_START = (10, 1)          # 1 de octubre
CYCLE_OI_END = (3, 31)            # fin de marzo
VEG_SOWING_START = (11, 1)        # noviembre: arranque real de siembra de hortaliza


@dataclass(frozen=True)
class CropRecord:
    """Superficie sembrada real de un cultivo en el Valle de Mexicali."""

    crop: str                 # nombre como lo usa el resto del sistema
    label: str                # nombre del boletín oficial
    hectares_2019_20: float | None
    hectares_2020_21: float | None
    # Mes típico de siembra dentro del ciclo. Sale del arranque documentado de
    # siembras de hortaliza; NO es un dato por cultivo publicado por la fuente.
    sowing_month: int = 11
    # Los boletines consultados no publican mes de cosecha por cultivo.
    harvest_month: int | None = None


# Superficies textuales de los boletines. Orden = el del boletín 2019-2020.
MEXICALI_OI: tuple[CropRecord, ...] = (
    CropRecord("Lechuga", "Lechuga (bola, orgánica y romana)", 804.0, 1897.0),
    CropRecord("Col de Bruselas", "Col de Bruselas", 486.0, None),
    CropRecord("Espinaca", "Espinaca china", 245.0, None),
    CropRecord("Brócoli", "Brócoli", 242.0, None),
    CropRecord("Apio", "Apio (orgánico y tradicional)", 195.0, None),
    CropRecord("Brocoleta", "Brocolette", 160.0, None),
    CropRecord("Col", "Col", 154.0, None),
    CropRecord("Cilantro", "Cilantro", 145.0, None),
)

_BY_CROP = {c.crop.lower(): c for c in MEXICALI_OI}


def find(crop: str | None) -> CropRecord | None:
    """Registro del cultivo, o ``None`` si no está en el calendario documentado."""
    return _BY_CROP.get((crop or "").strip().lower())


def typical_planting_date(crop: str | None, today: date | None = None) -> date | None:
    """Fecha de siembra típica del ciclo Otoño-Invierno vigente para ese cultivo.

    Devuelve ``None`` para cultivos fuera del calendario documentado — mejor no
    proponer fecha que proponer una inventada. Antes del 1 de octubre se refiere al
    ciclo anterior (el que está en campo hoy), no al que todavía no arrancó.
    """
    rec = find(crop)
    if rec is None:
        return None
    today = today or date.today()
    year = today.year if today >= date(today.year, *CYCLE_OI_START) else today.year - 1
    return date(year, rec.sowing_month, 1)
