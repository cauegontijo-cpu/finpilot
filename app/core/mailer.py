"""Envio de e-mails transacionais (SMTP; backend em memória para testes)."""

from __future__ import annotations

import logging
import smtplib
import ssl
import threading
from dataclasses import dataclass
from email.message import EmailMessage
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


def build_mailer(settings: Settings) -> Mailer:
    return MemoryMailer() if settings.email_backend == "memory" else SmtpMailer(settings)
