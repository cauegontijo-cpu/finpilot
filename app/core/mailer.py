"""Envio de e-mails transacionais (SMTP; backend em memória para testes)."""

from __future__ import annotations

import json
import logging
import smtplib
import ssl
import threading
import urllib.error
import urllib.request
from dataclasses import dataclass
from email.message import EmailMessage
from email.utils import parseaddr
from typing import Protocol

from app.core.config import Settings

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class OutgoingEmail:
    to: str
    subject: str
    text: str
    html: str


class Mailer(Protocol):
    def send(self, message: OutgoingEmail) -> None: ...


class MemoryMailer:
    def __init__(self) -> None:
        self.outbox: list[OutgoingEmail] = []
        self._lock = threading.Lock()

    def send(self, message: OutgoingEmail) -> None:
        with self._lock:
            self.outbox.append(message)


class SmtpMailer:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    def send(self, message: OutgoingEmail) -> None:
        s = self._settings
        msg = EmailMessage()
        msg["From"] = s.mail_from
        msg["To"] = message.to
        msg["Subject"] = message.subject
        msg.set_content(message.text)
        msg.add_alternative(message.html, subtype="html")
        try:
            with smtplib.SMTP(s.smtp_host, s.smtp_port, timeout=10) as smtp:
                if s.smtp_starttls:
                    smtp.starttls(context=ssl.create_default_context())
                if s.smtp_user and s.smtp_password:
                    smtp.login(s.smtp_user, s.smtp_password.get_secret_value())
                smtp.send_message(msg)
        except (OSError, smtplib.SMTPException):
            # Falha de entrega não deve vazar detalhes ao usuário; fica registrada no log.
            logger.exception("falha ao enviar e-mail", extra={"subject": message.subject})


class BrevoMailer:
    """Envio pela API HTTPS da Brevo (plano gratuito; dispensa porta SMTP liberada)."""

    ENDPOINT = "https://api.brevo.com/v3/smtp/email"

    def __init__(self, settings: Settings) -> None:
        if settings.brevo_api_key is None:
            raise ValueError("BREVO_API_KEY não configurada")
        self._api_key = settings.brevo_api_key.get_secret_value()
        name, address = parseaddr(settings.mail_from)
        self._sender = {"email": address, "name": name or settings.app_name}

    def send(self, message: OutgoingEmail) -> None:
        payload = {
            "sender": self._sender,
            "to": [{"email": message.to}],
            "subject": message.subject,
            "htmlContent": message.html,
            "textContent": message.text,
        }
        request = urllib.request.Request(  # noqa: S310 - URL fixa HTTPS da API
            self.ENDPOINT,
            data=json.dumps(payload).encode(),
            headers={
                "api-key": self._api_key,
                "content-type": "application/json",
                "accept": "application/json",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=10) as response:  # noqa: S310
                response.read()
        except (urllib.error.URLError, TimeoutError):
            # Falha de entrega não vaza detalhes ao usuário; fica registrada no log.
            logger.exception(
                "falha ao enviar e-mail pela Brevo", extra={"subject": message.subject}
            )


def build_mailer(settings: Settings) -> Mailer:
    if settings.email_backend == "memory":
        return MemoryMailer()
    if settings.email_backend == "brevo":
        return BrevoMailer(settings)
    return SmtpMailer(settings)
