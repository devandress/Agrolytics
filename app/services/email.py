"""Transactional email — password reset and the daily digest.

No-ops (loudly, via logger.warning) when SMTP_HOST is unset, same pattern as
DEEPSEEK_API_KEY / MERCADOPAGO_ACCESS_TOKEN / BACKUP_S3_*: local dev and demos
keep working, production needs the env vars set to actually deliver mail.
"""

from __future__ import annotations

import smtplib
from email.mime.text import MIMEText
from email.utils import formataddr, parseaddr

from loguru import logger

from app.core.config import settings


def send_password_reset_email(to_email: str, reset_link: str) -> None:
    if not settings.SMTP_HOST:
        logger.warning(
            f"SMTP not configured — password reset link for {to_email} (would have been emailed): {reset_link}"
        )
        return

    body = (
        "Recibimos una solicitud para restablecer tu contraseña de Agrolytics.\n\n"
        f"Restablecer contraseña: {reset_link}\n\n"
        "Este enlace vence en 30 minutos. Si no pediste esto, ignora el mensaje — "
        "tu contraseña actual sigue funcionando."
    )
    msg = MIMEText(body)
    msg["Subject"] = "Restablecer tu contraseña — Agrolytics"
    msg["From"] = settings.SMTP_FROM
    msg["To"] = to_email

    try:
        with smtplib.SMTP(settings.SMTP_HOST, settings.SMTP_PORT, timeout=10) as server:
            server.starttls()
            if settings.SMTP_USER:
                server.login(settings.SMTP_USER, settings.SMTP_PASSWORD)
            server.send_message(msg)
    except Exception as exc:
        logger.error(f"Failed to send password reset email to {to_email}: {exc}")


def send_email(to_email: str, subject: str, body: str) -> bool:
    """Enviar un correo de texto plano. Devuelve si salió de verdad.

    Texto plano y no HTML a propósito: el destinatario lo lee en el teléfono, con
    señal de campo, y un correo de texto llega, se lee rápido y no cae en spam por
    tener imágenes remotas. Cuando haga falta HTML será por una razón, no por
    costumbre.
    """
    if not settings.SMTP_HOST:
        logger.warning(f"SMTP sin configurar — no se envió '{subject}' a {to_email}")
        return False

    msg = MIMEText(body, _charset="utf-8")
    msg["Subject"] = subject
    name, addr = parseaddr(settings.SMTP_FROM)
    msg["From"] = formataddr((name, addr)) if name else addr
    msg["To"] = to_email
    try:
        with smtplib.SMTP(settings.SMTP_HOST, settings.SMTP_PORT, timeout=15) as server:
            server.starttls()
            if settings.SMTP_USER:
                server.login(settings.SMTP_USER, settings.SMTP_PASSWORD)
            server.send_message(msg)
        return True
    except Exception as exc:
        logger.error(f"Falló el envío de '{subject}' a {to_email}: {exc}")
        return False
