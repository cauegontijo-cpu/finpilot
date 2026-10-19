"""Telas de autenticação: cadastro, verificação, login, logout e recuperação de senha."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Form, Query, Request
from fastapi.responses import Response

from app.core.config import Settings
from app.core.deps import AppSettings, Auth, CurrentSession, OptionalSession, session_cookie
from app.core.errors import InvalidCredentials, RateLimited, ValidationFailed
from app.core.security import safe_next_url
from app.services.auth import InvalidToken
from app.web.render import page, redirect

router = APIRouter(include_in_schema=False)


def set_session_cookie(response: Response, settings: Settings, raw_token: str) -> None:
    response.set_cookie(
        settings.session_cookie_effective_name,
        raw_token,
        max_age=settings.session_absolute_timeout_hours * 3600,
        path="/",
        httponly=True,
        secure=settings.cookie_secure,
        samesite="lax",
    )


def clear_session_cookie(response: Response, settings: Settings) -> None:
    response.delete_cookie(
        settings.session_cookie_effective_name,
        path="/",
        httponly=True,
        secure=settings.cookie_secure,
        samesite="lax",
    )


# ---------------------------------------------------------------- cadastro
@router.get("/cadastro")
def register_form(request: Request, session: OptionalSession) -> Response:
    if session is not None:
        return redirect(request, "/app")
    return page(request, "pages/auth/register.html", {"form": {}, "errors": {}})


@router.post("/cadastro")
def register_submit(
    request: Request,
    auth: Auth,
    full_name: Annotated[str, Form(max_length=200)] = "",
    email: Annotated[str, Form(max_length=320)] = "",
    password: Annotated[str, Form(max_length=256)] = "",
    password_confirm: Annotated[str, Form(max_length=256)] = "",
    accept_privacy: Annotated[bool, Form()] = False,
) -> Response:
    form = {"full_name": full_name, "email": email, "accept_privacy": accept_privacy}
    try:
        auth.register(
            full_name=full_name,
            email=email,
            password=password,
            password_confirm=password_confirm,
            accept_privacy=accept_privacy,
        )
    except ValidationFailed as exc:
        return page(
            request,
            "pages/auth/register.html",
            {"form": form, "errors": exc.field_errors, "error": exc.detail},
            status_code=422,
        )
    return page(request, "pages/auth/check_email.html", {"email": email.strip()})


@router.get("/verificar-email")
def verify_email_page(request: Request, token: Annotated[str, Query(max_length=128)] = "") -> Response:
    # A confirmação exige um POST: pré-carregadores de links em clientes de e-mail não consomem o token.
    return page(
        request,
        "pages/auth/confirm_token.html",
        {"token": token, "action": "/verificar-email", "title": "Confirmar e-mail"},
    )


@router.post("/verificar-email")
def verify_email(request: Request, auth: Auth, token: Annotated[str, Form(max_length=128)] = "") -> Response:
    try:
        auth.verify_email(token)
    except InvalidToken as exc:
        return page(request, "pages/auth/token_invalid.html", {"detail": exc.detail}, status_code=400)
    return redirect(request, "/login", message="E-mail confirmado! Faça login para começar.")


@router.get("/verifique-email")
def verify_pending(request: Request, session: CurrentSession) -> Response:
    if session.user.is_verified:
        return redirect(request, "/app")
    return page(request, "pages/auth/verify_pending.html", {"email": session.user.email})


@router.post("/verifique-email/reenviar")
def resend_verification(request: Request, session: CurrentSession, auth: Auth) -> Response:
    try:
        auth.resend_verification(session.user)
    except RateLimited as exc:
        return redirect(request, "/verifique-email", message=exc.detail, kind="error")
    return redirect(request, "/verifique-email", message="Enviamos um novo link de confirmação.")


# ---------------------------------------------------------------- login
@router.get("/login")
def login_form(
    request: Request, session: OptionalSession, next_url: Annotated[str | None, Query(alias="next")] = None
) -> Response:
    if session is not None:
        return redirect(request, safe_next_url(next_url))
    return page(request, "pages/auth/login.html", {"next": safe_next_url(next_url), "form": {}})


@router.post("/login")
def login_submit(
    request: Request,
    auth: Auth,
    settings: AppSettings,
    email: Annotated[str, Form(max_length=320)] = "",
    password: Annotated[str, Form(max_length=256)] = "",
    next_url: Annotated[str | None, Form(alias="next")] = None,
) -> Response:
    target = safe_next_url(next_url)
    try:
        user = auth.authenticate(email, password)
    except (InvalidCredentials, RateLimited) as exc:
        status = 429 if isinstance(exc, RateLimited) else 401
        headers = {"Retry-After": str(exc.retry_after)} if isinstance(exc, RateLimited) else None
        return page(
            request,
            "pages/auth/login.html",
            {"next": target, "form": {"email": email}, "error": exc.detail},
            status_code=status,
            headers=headers,
        )
    raw = auth.start_session(user, previous_raw_token=session_cookie(request, settings))
    response = redirect(request, target if user.is_verified else "/verifique-email")
    set_session_cookie(response, settings, raw)
    return response


@router.post("/logout")
def logout(request: Request, auth: Auth, settings: AppSettings, session: OptionalSession) -> Response:
    if session is not None:
        auth.logout(session)
    response = redirect(request, "/login", message="Você saiu da sua conta.")
    clear_session_cookie(response, settings)
    return response


# ---------------------------------------------------------------- recuperação
@router.get("/recuperar-senha")
def forgot_form(request: Request) -> Response:
    return page(request, "pages/auth/forgot.html", {"sent": False})


@router.post("/recuperar-senha")
def forgot_submit(request: Request, auth: Auth, email: Annotated[str, Form(max_length=320)] = "") -> Response:
    try:
        auth.request_password_reset(email)
    except RateLimited as exc:
        return page(request, "pages/auth/forgot.html", {"sent": False, "error": exc.detail}, status_code=429)
    return page(request, "pages/auth/forgot.html", {"sent": True})


@router.get("/redefinir-senha")
def reset_form(request: Request, auth: Auth, token: Annotated[str, Query(max_length=128)] = "") -> Response:
    if not auth.check_reset_token(token):
        return page(
            request,
            "pages/auth/token_invalid.html",
            {"detail": InvalidToken().detail, "retry_url": "/recuperar-senha"},
            status_code=400,
        )
    return page(request, "pages/auth/reset.html", {"token": token, "errors": {}})


@router.post("/redefinir-senha")
def reset_submit(
    request: Request,
    auth: Auth,
    settings: AppSettings,
    token: Annotated[str, Form(max_length=128)] = "",
    password: Annotated[str, Form(max_length=256)] = "",
    password_confirm: Annotated[str, Form(max_length=256)] = "",
) -> Response:
    try:
        auth.reset_password(token, password, password_confirm)
    except InvalidToken as exc:
        return page(
            request,
            "pages/auth/token_invalid.html",
            {"detail": exc.detail, "retry_url": "/recuperar-senha"},
            status_code=400,
        )
    except ValidationFailed as exc:
        return page(
            request,
            "pages/auth/reset.html",
            {"token": token, "errors": exc.field_errors, "error": exc.detail},
            status_code=422,
        )
    response = redirect(request, "/login", message="Senha redefinida. Todas as sessões foram encerradas.")
    clear_session_cookie(response, settings)
    return response


@router.get("/confirmar-email")
def confirm_email_change_page(request: Request, token: Annotated[str, Query(max_length=128)] = "") -> Response:
    return page(
        request,
        "pages/auth/confirm_token.html",
        {"token": token, "action": "/confirmar-email", "title": "Confirmar novo e-mail"},
    )


@router.post("/confirmar-email")
def confirm_email_change(
    request: Request, auth: Auth, token: Annotated[str, Form(max_length=128)] = ""
) -> Response:
    try:
        auth.confirm_email_change(token)
    except InvalidToken as exc:
        return page(request, "pages/auth/token_invalid.html", {"detail": exc.detail}, status_code=400)
    return redirect(request, "/app/configuracoes", message="E-mail atualizado com sucesso.")
