"""E-mails transacionais renderizados a partir de templates (texto + HTML)."""

from __future__ import annotations

from typing import Any

from app.core.config import Settings
from app.core.mailer import OutgoingEmail
from app.core.templating import templates
from app.models import User


class EmailRenderer:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def _build(self, to: str, subject: str, template: str, **context: Any) -> OutgoingEmail:
        context.update(app_name=self.settings.app_name, base_url=self.settings.base_url.rstrip("/"))
        html = templates.env.get_template(f"emails/{template}.html").render(subject=subject, **context)
        text = templates.env.get_template(f"emails/{template}.txt").render(subject=subject, **context)
        return OutgoingEmail(to=to, subject=f"{subject} · {self.settings.app_name}", text=text, html=html)

    def _url(self, path: str) -> str:
        return self.settings.base_url.rstrip("/") + path

    def verify_email(self, user: User, token: str) -> OutgoingEmail:
        return self._build(
            user.email,
            "Confirme seu e-mail",
            "verify_email",
            name=user.first_name,
            link=self._url(f"/verificar-email?token={token}"),
            hours=self.settings.email_verification_ttl_hours,
        )

    def account_exists(self, user: User) -> OutgoingEmail:
        return self._build(
            user.email,
            "Tentativa de cadastro com seu e-mail",
            "account_exists",
            name=user.first_name,
            link=self._url("/recuperar-senha"),
        )

    def password_reset(self, user: User, token: str) -> OutgoingEmail:
        return self._build(
            user.email,
            "Redefinição de senha",
            "password_reset",
            name=user.first_name,
            link=self._url(f"/redefinir-senha?token={token}"),
            minutes=self.settings.password_reset_ttl_minutes,
        )

    def password_changed(self, user: User) -> OutgoingEmail:
        return self._build(
            user.email, "Sua senha foi alterada", "password_changed", name=user.first_name,
            link=self._url("/recuperar-senha"),
        )

    def confirm_email_change(self, user: User, new_email: str, token: str) -> OutgoingEmail:
        return self._build(
            new_email,
            "Confirme seu novo e-mail",
            "confirm_email_change",
            name=user.first_name,
            link=self._url(f"/confirmar-email?token={token}"),
            hours=self.settings.email_verification_ttl_hours,
        )

    def email_change_notice(self, user: User, new_email: str) -> OutgoingEmail:
        masked = new_email[0] + "***@" + new_email.split("@")[-1]
        return self._build(
            user.email, "Solicitação de troca de e-mail", "email_change_notice",
            name=user.first_name, masked=masked,
        )
