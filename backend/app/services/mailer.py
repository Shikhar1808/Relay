"""Outbound email: one SMTP sender plus the plain-text templates.

Routes hand send_email to FastAPI's BackgroundTasks, so it runs after the
response has gone out and a slow or failing mail server can never fail a
request.
"""
import asyncio
import logging
import smtplib
from email.message import EmailMessage

from app.core.config import settings

log = logging.getLogger("uvicorn.error")  # the logger uvicorn already prints


def _send_sync(to: str, subject: str, body: str) -> None:
    msg = EmailMessage()
    msg["From"] = settings.smtp_from
    msg["To"] = to
    msg["Subject"] = subject
    msg.set_content(body)
    with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=10) as smtp:
        smtp.starttls()
        smtp.login(settings.smtp_user, settings.smtp_password)
        smtp.send_message(msg)


async def send_email(to: str, subject: str, body: str) -> None:
    if not settings.smtp_password:
        # Dev fallback. The body can contain a live set-password link, so
        # never run a real deployment with SMTP_PASSWORD unset.
        log.info("EMAIL NOT SENT (SMTP_PASSWORD unset) to=%s subject=%s\n%s", to, subject, body)
        return
    try:
        await asyncio.to_thread(_send_sync, to, subject, body)  # smtplib blocks
    except Exception:
        # shortcut: a failed send is logged and dropped, add a retry queue when lost mail starts to matter
        log.exception("Email to %s failed (subject=%s)", to, subject)


def _set_password_link(token: str) -> str:
    # After "#", not "?": a URL fragment is never sent to the server, so the
    # token stays out of access logs and Referer headers.
    return f"{settings.app_base_url}/auth/set-password#token={token}"


def invite_email(name: str, token: str) -> tuple[str, str]:
    return (
        "You've been invited to Urumi",
        f"Hi {name},\n\n"
        "An admin created an Urumi account for you. Choose your password here:\n\n"
        f"{_set_password_link(token)}\n\n"
        f"The link works once and expires in {settings.invite_token_ttl_hours} hours.\n",
    )


def reset_email(name: str, token: str) -> tuple[str, str]:
    return (
        "Reset your Urumi password",
        f"Hi {name},\n\n"
        "Someone asked to reset the password for this account. Choose a new one here:\n\n"
        f"{_set_password_link(token)}\n\n"
        f"The link works once and expires in {settings.reset_token_ttl_minutes} minutes. "
        "If this wasn't you, ignore this email — your password has not changed.\n",
    )


def notice_email(name: str, subject: str, what_happened: str) -> tuple[str, str]:
    return (
        subject,
        f"Hi {name},\n\n{what_happened}\n\nIf you didn't expect this, contact your Urumi admin.\n",
    )
