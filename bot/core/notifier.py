"""Canais de alerta: e-mail (Gmail SMTP) e push notification (Web Push/VAPID)."""
from __future__ import annotations

import json
import smtplib
from email.mime.text import MIMEText

from pywebpush import WebPushException, webpush

from config.settings import settings
from db.models import PushSubscription
from db.session import get_session


def send_email_alert(subject: str, body: str) -> None:
    msg = MIMEText(body, "plain", "utf-8")
    msg["Subject"] = f"[IvanVestAI] {subject}"
    msg["From"] = settings.smtp_user
    msg["To"] = settings.alert_email_to

    with smtplib.SMTP(settings.smtp_host, settings.smtp_port) as server:
        server.starttls()
        server.login(settings.smtp_user, settings.smtp_app_password)
        server.send_message(msg)


def send_push_alert(title: str, body: str) -> None:
    with get_session() as session:
        subscriptions = session.query(PushSubscription).all()
        for sub in subscriptions:
            try:
                webpush(
                    subscription_info={
                        "endpoint": sub.endpoint,
                        "keys": sub.keys_json,
                    },
                    data=json.dumps({"title": title, "body": body}),
                    vapid_private_key=settings.vapid_private_key,
                    vapid_claims={"sub": settings.vapid_claims_email},
                )
            except WebPushException:
                # subscription expirada/inválida — poderia remover do banco aqui
                continue


def alert(subject: str, body: str) -> None:
    """Dispara em todos os canais configurados (e-mail + push)."""
    send_email_alert(subject, body)
    send_push_alert(subject, body)
