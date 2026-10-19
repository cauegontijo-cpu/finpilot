"""Autenticação, sessões e gestão de credenciais (seção 5 e 18.2).

Política de sessões (docs/SECURITY.md):
* Token de sessão aleatório (256 bits) no cookie HttpOnly; o banco guarda só o SHA-256.
* Timeout de inatividade e expiração absoluta configuráveis.
* Novo identificador a cada login (previne session fixation); a sessão anterior é revogada.
* Redefinição de senha revoga TODAS as sessões; troca de senha revoga as demais sessões.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta

from email_validator import EmailNotValidError, validate_email
from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core import ratelimit
from app.core.clock import Clock
from app.core.config import Settings
from app.core.errors import AppError, InvalidCredentials, ValidationFailed, expect
from app.core.mailer import Mailer
from app.core.ratelimit import RateLimiter
from app.core.security import (
    generate_token,
    hash_password,
    hash_token,
    hmac_hex,
    needs_rehash,
    password_problems,
    verify_password,
)
from app.models import EmailToken, EmailTokenPurpose, PasswordResetToken, User, UserSession
from app.services.audit import AuditService, RequestMeta
from app.services.categories import CategoryService
from app.services.emails import EmailRenderer
from app.utils.validation import clean_text

logger = logging.getLogger(__name__)

PRIVACY_POLICY_VERSION = "2026-10"
LAST_SEEN_RESOLUTION = timedelta(seconds=60)


class InvalidToken(AppError):
    status_code = 400
    title = "Link inválido"

    def __init__(self) -> None:
        super().__init__("Este link é inválido, já foi utilizado ou expirou.", code="invalid_token")


@dataclass(frozen=True)
class AuthenticatedSession:
    user: User
    session: UserSession
    raw_token: str


def normalize_email(raw: str) -> str:
    try:
        result = validate_email((raw or "").strip(), check_deliverability=False)
    except EmailNotValidError as exc:
        raise ValidationFailed(
            "E-mail inválido.", field_errors={"email": "E-mail inválido"}
        ) from exc
    return result.normalized.lower()


class AuthService:
    def __init__(
        self,
        db: Session,
        settings: Settings,
        clock: Clock,
        limiter: RateLimiter,
        mailer: Mailer,
        meta: RequestMeta,
    ) -> None:
        self.db = db
        self.settings = settings
        self.clock = clock
        self.limiter = limiter
        self.mailer = mailer
        self.meta = meta
        self.audit = AuditService(db, meta)
        self.emails = EmailRenderer(settings)

    # ------------------------------------------------------------------ helpers
    def _ip_key(self) -> str:
        return self.meta.ip or "unknown"

    def _email_key(self, email: str) -> str:
        # Chave de rate limit/auditoria sem armazenar o e-mail em claro.
        return hmac_hex(self.settings.secret_key.get_secret_value(), f"email:{email}")[:32]

    def _find_user_by_email(self, email: str) -> User | None:
        return self.db.scalars(select(User).where(func.lower(User.email) == email)).one_or_none()

    # ------------------------------------------------------------------ cadastro
    def register(
        self,
        *,
        full_name: str,
        email: str,
        password: str,
        password_confirm: str,
        accept_privacy: bool,
    ) -> None:
        """Cadastra o usuário. A resposta é idêntica se o e-mail já existir (anti-enumeração)."""
        self.limiter.hit(ratelimit.REGISTER_IP, self._ip_key())
        errors: dict[str, str] = {}
        name = clean_text(full_name, max_len=120)
        if len(name) < 2:
            errors["full_name"] = "Informe seu nome completo."
        try:
            normalized = normalize_email(email)
        except ValidationFailed:
            errors["email"] = "E-mail inválido."
            normalized = ""
        problems = password_problems(password, email=normalized, name=name)
        if problems:
            errors["password"] = " ".join(problems)
        if password != password_confirm:
            errors["password_confirm"] = "As senhas não conferem."  # noqa: S105 - mensagem, não segredo
        if not accept_privacy:
            errors["accept_privacy"] = "É necessário aceitar a política de privacidade."
        if errors:
            raise ValidationFailed("Revise os campos destacados.", field_errors=errors)

        existing = self._find_user_by_email(normalized)
        if existing is not None:
            # Não revelamos a existência; avisamos o titular por e-mail.
            hash_password(password)  # equaliza o tempo de resposta
            self.mailer.send(self.emails.account_exists(existing))
            return

        now = self.clock.now()
        user = User(
            full_name=name,
            email=normalized,
            password_hash=hash_password(password),
            privacy_accepted_at=now,
            privacy_version=PRIVACY_POLICY_VERSION,
        )
        self.db.add(user)
        try:
            self.db.flush()
        except IntegrityError:
            # Corrida entre dois cadastros com o mesmo e-mail.
            self.db.rollback()
            return
        CategoryService(self.db, self.clock, self.audit).create_defaults(user.id)
        token = self._issue_email_token(user, EmailTokenPurpose.VERIFY)
        self.audit.record("auth.registered", user_id=user.id, entity_type="user", entity_id=user.id)
        self.db.commit()
        self.mailer.send(self.emails.verify_email(user, token))

    def _issue_email_token(
        self, user: User, purpose: EmailTokenPurpose, new_email: str | None = None
    ) -> str:
        # Tokens anteriores do mesmo propósito deixam de valer.
        self.db.execute(
            update(EmailToken)
            .where(
                EmailToken.user_id == user.id,
                EmailToken.purpose == purpose,
                EmailToken.used_at.is_(None),
            )
            .values(used_at=self.clock.now())
        )
        raw = generate_token()
        self.db.add(
            EmailToken(
                user_id=user.id,
                purpose=purpose,
                token_hash=hash_token(raw),
                new_email=new_email,
                expires_at=self.clock.now()
                + timedelta(hours=self.settings.email_verification_ttl_hours),
            )
        )
        return raw

    def _consume_email_token(self, raw: str, purpose: EmailTokenPurpose) -> EmailToken:
        self.limiter.hit(ratelimit.VERIFY_EMAIL, self._ip_key())
        token = self.db.scalars(
            select(EmailToken)
            .where(EmailToken.token_hash == hash_token(raw or ""), EmailToken.purpose == purpose)
            .with_for_update()
        ).one_or_none()
        if token is None or token.used_at is not None or token.expires_at <= self.clock.now():
            raise InvalidToken
        token.used_at = self.clock.now()
        return token

    def verify_email(self, raw_token: str) -> User:
        token = self._consume_email_token(raw_token, EmailTokenPurpose.VERIFY)
        user = expect(self.db.get(User, token.user_id))
        if user.email_verified_at is None:
            user.email_verified_at = self.clock.now()
        self.audit.record(
            "auth.email_verified", user_id=user.id, entity_type="user", entity_id=user.id
        )
        self.db.commit()
        return user

    def resend_verification(self, user: User) -> None:
        self.limiter.hit(ratelimit.RESEND_VERIFICATION, str(user.id))
        if user.is_verified:
            return
        token = self._issue_email_token(user, EmailTokenPurpose.VERIFY)
        self.db.commit()
        self.mailer.send(self.emails.verify_email(user, token))

    # ------------------------------------------------------------------ login/sessões
    def authenticate(self, email: str, password: str) -> User:
        self.limiter.hit(ratelimit.LOGIN_IP, self._ip_key())
        try:
            normalized = normalize_email(email)
        except ValidationFailed:
            verify_password(None, password)
            raise InvalidCredentials from None
        email_key = self._email_key(normalized)
        self.limiter.hit(ratelimit.LOGIN_ACCOUNT, email_key)

        user = self._find_user_by_email(normalized)
        if user is None or not verify_password(user.password_hash, password):
            self.audit.record_independent(
                "auth.login_failed",
                user_id=user.id if user else None,
                changes={"email_ref": email_key},
            )
            raise InvalidCredentials

        self.limiter.reset(ratelimit.LOGIN_ACCOUNT, email_key)
        if needs_rehash(user.password_hash):
            user.password_hash = hash_password(password)
        return user

    def start_session(self, user: User, previous_raw_token: str | None = None) -> str:
        """Cria uma NOVA sessão (rotação do identificador) e revoga a anterior, se houver."""
        now = self.clock.now()
        if previous_raw_token:
            self.db.execute(
                update(UserSession)
                .where(UserSession.token_hash == hash_token(previous_raw_token))
                .values(revoked_at=now)
            )
        raw = generate_token()
        self.db.add(
            UserSession(
                user_id=user.id,
                token_hash=hash_token(raw),
                last_seen_at=now,
                expires_at=now + timedelta(hours=self.settings.session_absolute_timeout_hours),
                ip_address=self.meta.truncated_ip,
                user_agent=(self.meta.user_agent or "")[:200] or None,
            )
        )
        user.last_login_at = now
        self.audit.record("auth.login", user_id=user.id, entity_type="user", entity_id=user.id)
        self.db.commit()
        return raw

    def resolve_session(self, raw_token: str | None) -> AuthenticatedSession | None:
        if not raw_token or len(raw_token) > 128:
            return None
        session = self.db.scalars(
            select(UserSession).where(UserSession.token_hash == hash_token(raw_token))
        ).one_or_none()
        if session is None or session.revoked_at is not None:
            return None
        now = self.clock.now()
        idle_limit = timedelta(minutes=self.settings.session_idle_timeout_minutes)
        if session.expires_at <= now or now - session.last_seen_at > idle_limit:
            session.revoked_at = now
            self.db.commit()
            return None
        if now - session.last_seen_at >= LAST_SEEN_RESOLUTION:
            session.last_seen_at = now
            self.db.commit()
        user = self.db.get(User, session.user_id)
        if user is None:
            return None
        return AuthenticatedSession(user=user, session=session, raw_token=raw_token)

    def logout(self, auth: AuthenticatedSession) -> None:
        auth.session.revoked_at = self.clock.now()
        self.audit.record(
            "auth.logout", user_id=auth.user.id, entity_type="user", entity_id=auth.user.id
        )
        self.db.commit()

    def list_sessions(self, user: User) -> list[UserSession]:
        now = self.clock.now()
        return list(
            self.db.scalars(
                select(UserSession)
                .where(
                    UserSession.user_id == user.id,
                    UserSession.revoked_at.is_(None),
                    UserSession.expires_at > now,
                )
                .order_by(UserSession.last_seen_at.desc())
            )
        )

    def revoke_other_sessions(self, auth: AuthenticatedSession) -> int:
        result = self.db.execute(
            update(UserSession)
            .where(
                UserSession.user_id == auth.user.id,
                UserSession.id != auth.session.id,
                UserSession.revoked_at.is_(None),
            )
            .values(revoked_at=self.clock.now())
        )
        self.audit.record(
            "auth.sessions_revoked",
            user_id=auth.user.id,
            entity_type="user",
            entity_id=auth.user.id,
        )
        self.db.commit()
        return int(result.rowcount or 0)  # type: ignore[attr-defined]

    def revoke_session(self, auth: AuthenticatedSession, session_id: str) -> None:
        try:
            parsed = uuid.UUID(session_id)
        except ValueError:
            return
        self.db.execute(
            update(UserSession)
            .where(UserSession.user_id == auth.user.id, UserSession.id == parsed)
            .values(revoked_at=self.clock.now())
        )
        self.db.commit()

    def _revoke_all(self, user_id: uuid.UUID, now: datetime) -> None:
        self.db.execute(
            update(UserSession)
            .where(UserSession.user_id == user_id, UserSession.revoked_at.is_(None))
            .values(revoked_at=now)
        )

    # ------------------------------------------------------------------ recuperação
    def request_password_reset(self, email: str) -> None:
        """Sempre retorna sem erro (não revela se o e-mail existe)."""
        self.limiter.hit(ratelimit.PASSWORD_RESET_IP, self._ip_key())
        try:
            normalized = normalize_email(email)
        except ValidationFailed:
            return
        self.limiter.hit(ratelimit.PASSWORD_RESET_ACCOUNT, self._email_key(normalized))
        user = self._find_user_by_email(normalized)
        if user is None:
            return
        now = self.clock.now()
        self.db.execute(
            update(PasswordResetToken)
            .where(PasswordResetToken.user_id == user.id, PasswordResetToken.used_at.is_(None))
            .values(used_at=now)
        )
        raw = generate_token()
        self.db.add(
            PasswordResetToken(
                user_id=user.id,
                token_hash=hash_token(raw),
                expires_at=now + timedelta(minutes=self.settings.password_reset_ttl_minutes),
            )
        )
        self.audit.record(
            "auth.password_reset_requested", user_id=user.id, entity_type="user", entity_id=user.id
        )
        self.db.commit()
        self.mailer.send(self.emails.password_reset(user, raw))

    def check_reset_token(self, raw: str) -> bool:
        token = self.db.scalars(
            select(PasswordResetToken).where(PasswordResetToken.token_hash == hash_token(raw or ""))
        ).one_or_none()
        return token is not None and token.used_at is None and token.expires_at > self.clock.now()

    def reset_password(self, raw: str, password: str, password_confirm: str) -> None:
        self.limiter.hit(ratelimit.VERIFY_EMAIL, self._ip_key())
        token = self.db.scalars(
            select(PasswordResetToken)
            .where(PasswordResetToken.token_hash == hash_token(raw or ""))
            .with_for_update()
        ).one_or_none()
        now = self.clock.now()
        if token is None or token.used_at is not None or token.expires_at <= now:
            raise InvalidToken
        user = expect(self.db.get(User, token.user_id))
        self._validate_new_password(user, password, password_confirm)
        token.used_at = now
        user.password_hash = hash_password(password)
        self._revoke_all(user.id, now)
        self.audit.record(
            "auth.password_reset", user_id=user.id, entity_type="user", entity_id=user.id
        )
        self.db.commit()
        self.mailer.send(self.emails.password_changed(user))

    def _validate_new_password(self, user: User, password: str, confirm: str) -> None:
        errors: dict[str, str] = {}
        problems = password_problems(password, email=user.email, name=user.full_name)
        if problems:
            errors["password"] = " ".join(problems)
        if password != confirm:
            errors["password_confirm"] = "As senhas não conferem."  # noqa: S105 - mensagem, não segredo
        if errors:
            raise ValidationFailed("Revise os campos destacados.", field_errors=errors)

    # ------------------------------------------------------------------ conta
    def _require_password(self, user: User, password: str, field: str = "current_password") -> None:
        self.limiter.hit(ratelimit.SENSITIVE_ACCOUNT, str(user.id))
        if not verify_password(user.password_hash, password):
            self.audit.record_independent("auth.reauth_failed", user_id=user.id)
            raise ValidationFailed(
                "Senha atual incorreta.", field_errors={field: "Senha incorreta"}
            )

    def change_name(self, user: User, full_name: str) -> None:
        name = clean_text(full_name, max_len=120)
        if len(name) < 2:
            raise ValidationFailed(
                "Informe seu nome completo.", field_errors={"full_name": "Obrigatório"}
            )
        before = user.full_name
        user.full_name = name
        self.audit.record(
            "user.name_changed",
            user_id=user.id,
            entity_type="user",
            entity_id=user.id,
            changes={"full_name": {"antes": before, "depois": name}},
        )
        self.db.commit()

    def change_password(
        self, auth: AuthenticatedSession, current: str, password: str, confirm: str
    ) -> None:
        user = auth.user
        self._require_password(user, current)
        self._validate_new_password(user, password, confirm)
        user.password_hash = hash_password(password)
        self.db.execute(
            update(UserSession)
            .where(
                UserSession.user_id == user.id,
                UserSession.id != auth.session.id,
                UserSession.revoked_at.is_(None),
            )
            .values(revoked_at=self.clock.now())
        )
        self.audit.record(
            "auth.password_changed", user_id=user.id, entity_type="user", entity_id=user.id
        )
        self.db.commit()
        self.mailer.send(self.emails.password_changed(user))

    def request_email_change(self, user: User, new_email: str, password: str) -> None:
        self._require_password(user, password, field="password")
        normalized = normalize_email(new_email)
        if normalized == user.email:
            raise ValidationFailed(
                "Informe um e-mail diferente do atual.", field_errors={"email": "Igual ao atual"}
            )
        token = self._issue_email_token(user, EmailTokenPurpose.CHANGE_EMAIL, new_email=normalized)
        self.audit.record(
            "user.email_change_requested", user_id=user.id, entity_type="user", entity_id=user.id
        )
        self.db.commit()
        # Sempre enviamos ao novo endereço; se já estiver em uso, a confirmação falhará.
        self.mailer.send(self.emails.confirm_email_change(user, normalized, token))
        self.mailer.send(self.emails.email_change_notice(user, normalized))

    def confirm_email_change(self, raw_token: str) -> User:
        token = self._consume_email_token(raw_token, EmailTokenPurpose.CHANGE_EMAIL)
        user = expect(self.db.get(User, token.user_id))
        if not token.new_email:
            raise InvalidToken
        if self._find_user_by_email(token.new_email) is not None:
            self.db.commit()  # token consumido
            raise InvalidToken
        before = user.email
        user.email = token.new_email
        user.email_verified_at = self.clock.now()
        self.audit.record(
            "user.email_changed",
            user_id=user.id,
            entity_type="user",
            entity_id=user.id,
            changes={"email_alterado": True, "dominio_anterior": before.split("@")[-1]},
        )
        self.db.commit()
        return user

    def delete_account(
        self, auth: AuthenticatedSession, password: str, email_confirmation: str
    ) -> None:
        """Exclusão definitiva com confirmação reforçada (senha + digitação do e-mail)."""
        user = auth.user
        self._require_password(user, password, field="password")
        if (email_confirmation or "").strip().lower() != user.email:
            raise ValidationFailed(
                "Digite seu e-mail exatamente para confirmar.",
                field_errors={"email_confirmation": "Não confere"},
            )
        user_id = user.id
        self.audit.record("user.deleted", user_id=user_id, entity_type="user", entity_id=user_id)
        self.db.delete(user)
        self.db.commit()
        logger.info("conta excluída", extra={"user_ref": str(user_id)})
