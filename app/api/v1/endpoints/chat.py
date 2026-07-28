"""DeepSeek-powered agronomic chatbot endpoint."""

import httpx
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from app.api.deps import AiUser, CurrentUser
from app.core.config import settings
from app.core.limiter import limiter

router = APIRouter()

_DEEPSEEK_URL = "https://api.deepseek.com/v1/chat/completions"
_MODEL = "deepseek-v4-flash"

_SYSTEM_PROMPT = """Eres AgroBot, un asistente agrónomo experto integrado en la plataforma Agrolytics.
Tu rol es ayudar a agricultores y agrónomos con:
- Interpretación de índices satelitales (NDVI, NDMI, EVI, NDRE)
- Recomendaciones de manejo de cultivos
- Diagnóstico de estrés hídrico y nutricional
- Interpretación de datos climáticos y fenológicos
- Alertas y decisiones de riego, fertilización y cosecha

Cuando el usuario te comparta datos de su parcela, analízalos en detalle.
Responde siempre en español, de forma concisa y práctica.
Si no tienes suficiente información, pregunta por el cultivo, etapa fenológica y región."""


class ChatMessage(BaseModel):
    role: str
    content: str


class ChatRequest(BaseModel):
    messages: list[ChatMessage]
    field_context: dict | None = None
    stream: bool = False


@router.post("/chat")
@limiter.limit(settings.AI_RATE_LIMIT)
async def chat(
    request: Request,
    payload: ChatRequest,
    current_user: AiUser,
):
    """Proxy to DeepSeek with agronomic system prompt and optional field context.

    Gated to plans that include AI (Free → HTTP 402) and rate-limited per IP —
    this is a cost floor, not the real per-plan monthly quota (not metered yet)."""
    if not settings.DEEPSEEK_API_KEY:
        raise HTTPException(503, "Chatbot not configured (missing API key).")

    system_content = _SYSTEM_PROMPT
    if payload.field_context:
        ctx = payload.field_context
        system_content += "\n\n## Contexto de la parcela actual:\n"
        for k, v in ctx.items():
            system_content += f"- {k}: {v}\n"

    messages = [{"role": "system", "content": system_content}]
    messages += [{"role": m.role, "content": m.content} for m in payload.messages]

    body = {
        "model": _MODEL,
        "messages": messages,
        "stream": payload.stream,
        "max_tokens": 1024,
        "temperature": 0.7,
    }
    headers = {
        "Authorization": f"Bearer {settings.DEEPSEEK_API_KEY}",
        "Content-Type": "application/json",
    }

    if payload.stream:
        async def generate():
            async with httpx.AsyncClient(timeout=60) as client:
                async with client.stream("POST", _DEEPSEEK_URL, json=body, headers=headers) as resp:
                    async for line in resp.aiter_lines():
                        if line.startswith("data: "):
                            yield line + "\n\n"
        return StreamingResponse(generate(), media_type="text/event-stream")

    async with httpx.AsyncClient(timeout=60) as client:
        resp = await client.post(_DEEPSEEK_URL, json=body, headers=headers)
    if resp.status_code != 200:
        from loguru import logger
        logger.error(f"DeepSeek {resp.status_code}: {resp.text[:400]}")
        raise HTTPException(resp.status_code, f"AgroBot error ({resp.status_code}): {resp.text[:300]}")
    data = resp.json()
    return {"reply": data["choices"][0]["message"]["content"]}


@router.get("/chat/test")
async def test_chat_api(current_user: CurrentUser) -> dict:
    """Verify that the DeepSeek API key is valid and responsive."""
    if not settings.DEEPSEEK_API_KEY:
        return {"ok": False, "reason": "DEEPSEEK_API_KEY not set in .env"}

    headers = {
        "Authorization": f"Bearer {settings.DEEPSEEK_API_KEY}",
        "Content-Type": "application/json",
    }
    body = {
        "model": _MODEL,
        "messages": [{"role": "user", "content": "Hi"}],
        "max_tokens": 5,
    }
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(_DEEPSEEK_URL, json=body, headers=headers)
        if resp.status_code == 200:
            return {"ok": True, "model": _MODEL}
        return {"ok": False, "status": resp.status_code, "reason": resp.text[:200]}
    except Exception as exc:
        return {"ok": False, "reason": str(exc)}
