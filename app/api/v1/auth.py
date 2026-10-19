"""Autenticação e conta do usuário via API (sessão por cookie HttpOnly + CSRF)."""

from __future__ import annotations

import uuid
from datetime import datetime

from fastapi import APIRouter, Request, Response
from pydantic import EmailStr, Field

from app.core.deps import (
    AppClock,
    AppSettings,
    Auth,
    CurrentSession,
    DbSession,
    csrf_token,
    session_cookie,
)
from app.schemas.common import PROBLEM_RESPONSES, InputModel, OutputModel
from app.services.user_data import export_user_data
from app.web.auth import clear_session_cookie, set_session_cookie

router = APIRouter(tags=["auth"], responses=PROBLEM_RESPONSES)


class CsrfOut(OutputModel):
    csrf_token: str


class RegisterIn(InputModel):
    full_name: str = Field(min_length=2, max_length=120)
    email: EmailStr
    password: str = Field(min_length=1, max_length=128)
    password_confirm: str = Field(min_length=1, max_length=128)
    accept_privacy: bool


class LoginIn(InputModel):
    email: str = Field(max_length=320)
    password: str = Field(max_length=128)


class TokenIn(InputModel):
    token: str = Field(min_length=10, max_length=128)


class ResetRequestIn(InputModel):
    email: str = Field(max_length=320)


class ResetConfirmIn(InputModel):
    token: str = Field(min_length=10, max_length=128)
    password: str = Field(max_length=128)
    password_confirm: str = Field(max_length=128)


class UserOut(OutputModel):
    id: uuid.UUID
    full_name: str
    email: str
    email_verified_at: datetime | None
    created_at: datetime


class UserUpdate(InputModel):
    full_name: str = Field(min_length=2, max_length=120)


class PasswordChangeIn(InputModel):
    current_password: str = Field(max_length=128)
    password: str = Field(max_length=128)
    password_confirm: str = Field(max_length=128)


class SessionOut(OutputModel):
    id: uuid.UUID
    created_at: datetime
    last_seen_at: datetime
    ip_address: str | None
    user_agent: str | None
    current: bool = False


class AccountDeleteIn(InputModel):
    password: str = Field(max_length=128)
    email_confirmation: str = Field(max_length=320)


@router.get("/auth/csrf", response_model=CsrfOut, summary="Token CSRF para o cliente atual")
def get_csrf(request: Request) -> CsrfOut:
    return CsrfOut(csrf_token=csrf_token(request))


@router.post(
    "/auth/register", status_code=202, summary="Cadastro (resposta idêntica se o e-mail já existir)"
)
def register(data: RegisterIn, auth: Auth) -> dict[str, str]:
    auth.register(
        full_name=data.full_name,
        email=str(data.email),
        password=data.password,
        password_confirm=data.password_confirm,
        accept_privacy=data.accept_privacy,
    )
    return {"detail": "Se o e-mail puder ser cadastrado, enviaremos um link de confirmação."}


@router.post(
    "/auth/verify-email", status_code=204, summary="Confirma o e-mail com o token recebido"
)
def verify_email(data: TokenIn, auth: Auth) -> Response:
    auth.verify_email(data.token)
    return Response(status_code=204)


@router.post(
    "/auth/login", response_model=UserOut, summary="Login: cria sessão nova (cookie HttpOnly)"
)
def login(
    data: LoginIn, request: Request, response: Response, auth: Auth, settings: AppSettings
) -> UserOut:
    user = auth.authenticate(data.email, data.password)
    raw = auth.start_session(user, previous_raw_token=session_cookie(request, settings))
    set_session_cookie(response, settings, raw)
    return UserOut.model_validate(user)


@router.post("/auth/logout", status_code=204, summary="Encerra a sessão atual")
def logout(session: CurrentSession, auth: Auth, settings: AppSettings) -> Response:
    auth.logout(session)
    response = Response(status_code=204)
    clear_session_cookie(response, settings)
    return response


@router.post(
    "/auth/password-reset",
    status_code=202,
    summary="Solicita redefinição (não revela se o e-mail existe)",
)
def password_reset(data: ResetRequestIn, auth: Auth) -> dict[str, str]:
    auth.request_password_reset(data.email)
    return {"detail": "Se houver uma conta com esse e-mail, enviaremos um link."}


@router.post(
    "/auth/password-reset/confirm",
    status_code=204,
    summary="Redefine a senha e revoga todas as sessões",
)
def password_reset_confirm(data: ResetConfirmIn, auth: Auth) -> Response:
    auth.reset_password(data.token, data.password, data.password_confirm)
    return Response(status_code=204)


@router.get("/users/me", response_model=UserOut, tags=["users"])
def me(session: CurrentSession) -> UserOut:
    return UserOut.model_validate(session.user)


@router.patch("/users/me", response_model=UserOut, tags=["users"])
def update_me(data: UserUpdate, session: CurrentSession, auth: Auth) -> UserOut:
    auth.change_name(session.user, data.full_name)
    return UserOut.model_validate(session.user)


@router.post(
    "/users/me/password", status_code=204, tags=["users"], summary="Troca a senha (exige a atual)"
)
def change_password(data: PasswordChangeIn, session: CurrentSession, auth: Auth) -> Response:
    auth.change_password(session, data.current_password, data.password, data.password_confirm)
    return Response(status_code=204)


@router.get("/users/me/sessions", response_model=list[SessionOut], tags=["users"])
def sessions(session: CurrentSession, auth: Auth) -> list[SessionOut]:
    return [
        SessionOut.model_validate(s).model_copy(update={"current": s.id == session.session.id})
        for s in auth.list_sessions(session.user)
    ]


@router.delete(
    "/users/me/sessions", status_code=204, tags=["users"], summary="Encerra as outras sessões"
)
def revoke_other_sessions(session: CurrentSession, auth: Auth) -> Response:
    auth.revoke_other_sessions(session)
    return Response(status_code=204)


@router.get(
    "/users/me/export",
    tags=["users"],
    summary="Exporta todos os dados pessoais e financeiros (JSON)",
)
def export(session: CurrentSession, db: DbSession, clock: AppClock) -> dict[str, object]:
    return export_user_data(db, session.user, clock.now())


@router.delete(
    "/users/me", status_code=204, tags=["users"], summary="Exclui a conta (senha + e-mail)"
)
def delete_me(
    data: AccountDeleteIn, session: CurrentSession, auth: Auth, settings: AppSettings
) -> Response:
    auth.delete_account(session, data.password, data.email_confirmation)
    response = Response(status_code=204)
    clear_session_cookie(response, settings)
    return response
