"""
bot/notifier.py — Notificaciones al finalizar un run.

Soporta:
  - Email (SMTP Gmail con App Password)
  - Webhook HTTP (Discord, Slack, ntfy.sh)

Configuración en .env:
    NOTIFY_EMAIL=tu@gmail.com
    SMTP_HOST=smtp.gmail.com
    SMTP_PORT=587
    SMTP_USER=tu@gmail.com
    SMTP_PASS=tu-app-password
    NOTIFY_WEBHOOK=https://ntfy.sh/tu-canal
"""
from __future__ import annotations

import logging
import os
import json
from datetime import datetime

log = logging.getLogger("applyjob.notifier")


def _env(key: str, default: str = "") -> str:
    return os.getenv(key, default).strip()


def send_summary(
    portals: list[str],
    applied: int,
    external: int,
    filtered: int,
    errors: int,
    duration_s: float = 0,
) -> None:
    """
    Envía resumen por email y/o webhook.
    Llama a esta función al finalizar cada run desde engine.py o gui_server.py.
    """
    if applied == 0 and external == 0:
        log.debug("[NOTIFIER] 0 postulaciones — omitiendo notificación.")
        return

    total      = applied + external
    portals_s  = ", ".join(p.capitalize() for p in portals) if portals else "—"
    dur_min    = int(duration_s // 60)
    dur_sec    = int(duration_s % 60)
    timestamp  = datetime.now().strftime("%d/%m/%Y %H:%M")

    subject = f"ApplyJob — {total} postulaciones ({timestamp})"
    body = (
        f"Run finalizado: {timestamp}\n"
        f"Portales      : {portals_s}\n"
        f"Postuladas    : {applied}\n"
        f"Externas      : {external}\n"
        f"Filtradas     : {filtered}\n"
        f"Errores       : {errors}\n"
        f"Duración      : {dur_min}m {dur_sec:02d}s\n"
    )

    _send_email(subject, body)
    _send_webhook(subject, body, applied, external)


# ── Email ─────────────────────────────────────────────────────────────────────

def _send_email(subject: str, body: str) -> None:
    to_addr   = _env("NOTIFY_EMAIL")
    smtp_host = _env("SMTP_HOST", "smtp.gmail.com")
    smtp_port = int(_env("SMTP_PORT", "587"))
    smtp_user = _env("SMTP_USER")
    smtp_pass = _env("SMTP_PASS")

    if not (to_addr and smtp_user and smtp_pass):
        log.debug("[NOTIFIER] Email no configurado — omitiendo.")
        return

    try:
        import smtplib
        from email.mime.text import MIMEText

        msg = MIMEText(body, "plain", "utf-8")
        msg["Subject"] = subject
        msg["From"]    = smtp_user
        msg["To"]      = to_addr

        with smtplib.SMTP(smtp_host, smtp_port, timeout=10) as s:
            s.starttls()
            s.login(smtp_user, smtp_pass)
            s.sendmail(smtp_user, [to_addr], msg.as_string())

        log.info("[NOTIFIER] Email enviado a %s", to_addr)
    except Exception as exc:
        log.warning("[NOTIFIER] Error enviando email: %s", exc)


# ── Webhook ───────────────────────────────────────────────────────────────────

def _send_webhook(subject: str, body: str, applied: int, external: int) -> None:
    webhook_url = _env("NOTIFY_WEBHOOK")
    if not webhook_url:
        log.debug("[NOTIFIER] Webhook no configurado — omitiendo.")
        return

    try:
        import urllib.request

        # Detectar tipo de webhook
        if "discord.com" in webhook_url:
            payload = json.dumps({
                "content": None,
                "embeds": [{
                    "title": subject,
                    "description": f"```\n{body}\n```",
                    "color": 0x10b981 if (applied + external) > 0 else 0xef4444,
                }]
            }).encode("utf-8")
            headers = {"Content-Type": "application/json"}

        elif "hooks.slack.com" in webhook_url:
            payload = json.dumps({
                "text": f"*{subject}*\n```{body}```"
            }).encode("utf-8")
            headers = {"Content-Type": "application/json"}

        else:
            # ntfy.sh u otro webhook genérico — enviar texto plano
            payload = body.encode("utf-8")
            headers = {
                "Title":    subject,
                "Priority": "default",
                "Tags":     "briefcase",
            }

        req = urllib.request.Request(
            webhook_url, data=payload, headers=headers, method="POST"
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            log.info("[NOTIFIER] Webhook enviado (%d)", resp.status)

    except Exception as exc:
        log.warning("[NOTIFIER] Error enviando webhook: %s", exc)


def send_alert(subject: str, message: str, screenshot_path: str | None = None) -> None:
    """
    Envío rápido de alerta (webhook + email opcional) para eventos urgentes
    como detección de CAPTCHA. Adjunta la ruta del screenshot al cuerpo.
    """
    full = message
    if screenshot_path:
        full = full + f"\nScreenshot: {screenshot_path}"

    # Enviar email si configurado
    try:
        _send_email(subject, full)
    except Exception:
        log.debug("[NOTIFIER] _send_email falló en send_alert (ignorado)")

    # Enviar webhook
    try:
        _send_webhook(subject, full, 0, 0)
    except Exception:
        log.debug("[NOTIFIER] _send_webhook falló en send_alert (ignorado)")
