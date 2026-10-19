"""Configurações: perfil, segurança, sessões, exportação e exclusão da conta."""

from __future__ import annotations

import json
from typing import Annotated

from fastapi import APIRouter, Form, Request
from fastapi.responses import Response

from app.core.deps import AppClock, AppSettings, Auth, CurrentSession, DbSession
from app.core.errors import RateLimited, ValidationFailed
from app.services.user_data import export_user_data
from app.web.auth import clear_session_cookie
from app.web.render import page, redirect

router = APIRouter(prefix="/app/configuracoes", include_in_schema=False)


def _render(
    request: Request, auth: Auth, session: CurrentSession, *, status_code: int = 200, **extra: object
) -> Response:
    context = {
        "sessions": auth.list_sessions(session.user),
        "current_session_id": session.session.id,
        "errors": {},
        "section_error": {},
        **extra,
    }
    return page(request, "pages/settings.html", context, status_code=status_code)


@router.get("")
def settings_page(request: Request, auth: Auth, session: CurrentSession) -> Response:
    return _render(request, auth, session)


@router.post("/perfil")
def update_profile(
    request: Request, auth: Auth, session: CurrentSession, full_name: Annotated[str, Form(max_length=200)] = ""
) -> Response:
    try:
        auth.change_name(session.user, full_name)
    except ValidationFailed as exc:
        return _render(request, auth, session, errors=exc.field_errors, section_error={"profile": exc.detail}, status_code=422)
    return redirect(request, "/app/configuracoes", message="Perfil atualizado.")


@router.post("/email")
def change_email(
    request: Request,
    auth: Auth,
    session: CurrentSession,
    email: Annotated[str, Form(max_length=320)] = "",
    password: Annotated[str, Form(max_length=256)] = "",
) -> Response:
    try:
        auth.request_email_change(session.user, email, password)
    except (ValidationFailed, RateLimited) as exc:
        return _render(request, auth, session, errors=exc.field_errors, section_error={"email": exc.detail}, status_code=exc.status_code)
    return redirect(request, "/app/configuracoes", message="Enviamos um link de confirmação para o novo e-mail.")


@router.post("/senha")
def change_password(
    request: Request,
    auth: Auth,
    session: CurrentSession,
    current_password: Annotated[str, Form(max_length=256)] = "",
    password: Annotated[str, Form(max_length=256)] = "",
    password_confirm: Annotated[str, Form(max_length=256)] = "",
) -> Response:
    try:
        auth.change_password(session, current_password, password, password_confirm)
    except (ValidationFailed, RateLimited) as exc:
        return _render(request, auth, session, errors=exc.field_errors, section_error={"password": exc.detail}, status_code=exc.status_code)
    return redirect(request, "/app/configuracoes", message="Senha alterada. As demais sessões foram encerradas.")


@router.post("/sessoes/encerrar-outras")
def revoke_others(request: Request, auth: Auth, session: CurrentSession) -> Response:
    count = auth.revoke_other_sessions(session)
    return redirect(request, "/app/configuracoes", message=f"{count} sessão(ões) encerrada(s).")


@router.post("/sessoes/{session_id}/encerrar")
def revoke_one(request: Request, session_id: str, auth: Auth, session: CurrentSession) -> Response:
    auth.revoke_session(session, session_id)
    return redirect(request, "/app/configuracoes", message="Sessão encerrada.")


@router.post("/exportar")
def export_data(session: CurrentSession, db: DbSession, clock: AppClock) -> Response:
    payload = export_user_data(db, session.user, clock.now())
    filename = f"finpilot-dados-{clock.today().isoformat()}.json"
    return Response(
        json.dumps(payload, ensure_ascii=False, indent=2),
        media_type="application/json",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.post("/excluir-conta")
def delete_account(
    request: Request,
    auth: Auth,
    session: CurrentSession,
    settings: AppSettings,
    password: Annotated[str, Form(max_length=256)] = "",
    email_confirmation: Annotated[str, Form(max_length=320)] = "",
) -> Response:
    try:
        auth.delete_account(session, password, email_confirmation)
    except (ValidationFailed, RateLimited) as exc:
        return _render(request, auth, session, errors=exc.field_errors, section_error={"delete": exc.detail}, status_code=exc.status_code)
    response = redirect(request, "/", message="Sua conta e seus dados foram excluídos.")
    clear_session_cookie(response, settings)
    return response
