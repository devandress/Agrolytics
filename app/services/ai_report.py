"""Reporte de campo redactado por IA sobre datos reales.

El reporte anterior era una plantilla: los mismos párrafos con los números
cambiados. Servía para imprimir, no para entender. Acá el modelo escribe la
narrativa —qué cambió, por qué importa, qué conviene hacer— pero **no aporta ni un
solo dato**: recibe las observaciones reales de la parcela y sólo puede hablar de
ellas.

Dos reglas que hacen que esto sea usable en una decisión agronómica:

1. **Sin datos no hay reporte.** Si la parcela no tiene observaciones reales, no se
   llama al modelo. Un texto plausible sobre una parcela sin medir es exactamente
   la falsa certeza que el resto del producto evita.
2. **La IA no inventa números.** El prompt se lo prohíbe explícitamente y el
   payload viaja completo, así que cualquier cifra del reporte es verificable
   contra lo que se le pasó. El reporte se marca como generado por IA en la
   respuesta para que la interfaz lo pueda decir.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime

import httpx
from loguru import logger
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models.field import Field
from app.services.ai_tasks import _DEEPSEEK_URL, _MODEL, _gather

_SYSTEM = """Sos un agrónomo que escribe el parte diario de una parcela para el productor.

Recibís un JSON con las mediciones satelitales REALES de la parcela y su contexto.

Reglas que no podés romper:
- No inventes ningún número. Usá sólo los que están en el JSON. Si un dato no está,
  decí que no está; nunca lo estimes.
- No prometas certeza que los datos no dan. Un índice satelital muestra síntomas, no
  diagnostica una causa. Si algo puede tener varias explicaciones, decilo.
- Escribí en español rioplatense neutro, para alguien que trabaja en el campo y no
  estudió teledetección. Nada de "NDVI" a secas: explicá qué mide cuando lo nombrés.
- Nunca recomiendes aplicar un producto químico sin antes confirmar en campo.

Formato exacto, sin markdown, sin asteriscos:

RESUMEN
Dos o tres oraciones: cómo está la parcela hoy y qué cambió.

LO QUE MIDIÓ EL SATÉLITE
Una línea por índice disponible: qué mide, valor, y si sube, baja o está estable.

QUÉ SIGNIFICA
Interpretación agronómica. Distinguí lo que el dato muestra de lo que sugiere.

QUÉ HACER
Acciones concretas y verificables. Si no hace falta hacer nada, decilo.

LO QUE NO SABEMOS
Qué le falta a este reporte para ser concluyente."""


async def build_ai_report(db: AsyncSession, field: Field) -> dict:
    """Reporte del estado de la parcela. Devuelve el texto más su procedencia.

    ``is_ai`` False significa que se devolvió el motivo por el que no se pudo
    generar, no un reporte degradado: la interfaz debe mostrar eso, no disfrazarlo.
    """
    payload = await _gather(db, field)
    if not payload:
        return {
            "is_ai": False,
            "reason": "sin_datos",
            "report": (
                f"Todavía no hay observaciones satelitales de {field.name}. "
                "En cuanto llegue la primera imagen se puede generar el reporte."
            ),
            "generated_at": datetime.now(UTC).isoformat(),
        }
    if not settings.DEEPSEEK_API_KEY:
        return {
            "is_ai": False,
            "reason": "sin_credencial",
            "report": "El redactor por IA no está configurado (falta DEEPSEEK_API_KEY).",
            "data_used": payload,
            "generated_at": datetime.now(UTC).isoformat(),
        }

    body = {
        "model": _MODEL,
        "messages": [
            {"role": "system", "content": _SYSTEM},
            {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
        ],
        # Más largo que el de tareas: acá la salida es prosa, no una lista JSON.
        "max_tokens": 1100,
        # Bajo a propósito. Un parte agronómico tiene que ser aburrido y repetible;
        # la creatividad acá se traduce en adornos que el productor lee como certeza.
        "temperature": 0.2,
    }
    headers = {
        "Authorization": f"Bearer {settings.DEEPSEEK_API_KEY}",
        "Content-Type": "application/json",
    }
    try:
        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.post(_DEEPSEEK_URL, json=body, headers=headers)
        if resp.status_code != 200:
            raise RuntimeError(f"DeepSeek {resp.status_code}: {resp.text[:200]}")
        text = resp.json()["choices"][0]["message"]["content"].strip()
    except Exception as exc:
        logger.warning(f"Reporte IA falló para {field.id}: {exc}")
        return {
            "is_ai": False,
            "reason": "error_modelo",
            "report": "No se pudo generar el reporte ahora. Los datos de la parcela están abajo.",
            "data_used": payload,
            "generated_at": datetime.now(UTC).isoformat(),
        }

    return {
        "is_ai": True,
        "report": text,
        # Se devuelve el payload completo a propósito: cualquier número del reporte
        # tiene que poder contrastarse contra lo que el modelo recibió.
        "data_used": payload,
        "generated_at": datetime.now(UTC).isoformat(),
    }
