"""Transactional email — currently just the password-reset link.

No-ops (loudly, via logger.warning) when SMTP_HOST is unset, same pattern as
DEEPSEEK_API_KEY / MERCADOPAGO_ACCESS_TOKEN / BACKUP_S3_*: local dev and demos
keep working, production needs the env vars set to actually deliver mail.
"""

from __future__ import annotations

import smtplib
from email.mime.text import MIMEText

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
