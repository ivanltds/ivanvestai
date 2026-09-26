"""Canais de alerta: e-mail (Gmail SMTP) e push notification (Web Push/VAPID)."""
from __future__ import annotations

import json
import logging
import smtplib
from email.mime.text import MIMEText

from pywebpush import WebPushException, webpush

from config.settings import settings
from db.models import PushSubscription
from db.session import get_session

logger = logging.getLogger("ivanvestai.notifier")


def send_email_alert(subject: str, body: str) -> None:
    if not settings.smtp_user or not settings.smtp_app_password:
        logger.warning("SMTP não configurado (SMTP_USER/SMTP_APP_PASSWORD) -- e-mail de alerta não enviado.")
        return

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
            except WebPushException as exc:
                # Achado 24/09/2026 (arquitetura-tecnica.md 9.26, item 7): antes
                # nunca removia a subscription morta -- toda subscription
                # expirada/inválida era retentada (e falhava) pra sempre, em todo
                # alerta futuro. 404/410 são os códigos padrão de "subscription
                # não existe mais" (RFC 8030) -- só esses são removidos; qualquer
                # outro código (erro transitório do serviço de push, por exemplo)
                # é deixado pra tentar de novo no próximo alerta.
                status_code = getattr(getattr(exc, "response", None), "status_code", None)
                if status_code in (404, 410):
                    logger.info("Removendo push subscription morta (status=%s): %s", status_code, sub.endpoint)
                    session.delete(sub)
                continue


def alert(subject: str, body: str) -> None:
    """Dispara em todos os canais configurados (e-mail + push). NUNCA levanta
    exceção: um canal fora do ar (SMTP mal configurado, push expirado) não pode
    derrubar o ciclo do bot -- justamente o momento em que alertas disparam
    (circuit breaker, ordem não registrada) é o pior pra abortar o ciclo. Cada
    canal é tentado independentemente do outro."""
    for channel in (send_email_alert, send_push_alert):
        try:
            channel(subject, body)
        except Exception:
            logger.exception("Falha ao enviar alerta pelo canal %s.", channel.__name__)
