"""Exceções de domínio. Traduzidas para HTTP pelos handlers globais (app/core/handlers.py)."""

from __future__ import annotations


class AppError(Exception):
    status_code = 400
    title = "Requisição inválida"

    def __init__(
        self, detail: str, *, code: str | None = None, field_errors: dict[str, str] | None = None
    ) -> None:
        super().__init__(detail)
        self.detail = detail
        self.code = code
        self.field_errors = field_errors or {}


class NotFoundError(AppError):
    """Recurso inexistente OU pertencente a outro usuário (não revela existência)."""

    status_code = 404
    title = "Não encontrado"

    def __init__(self, detail: str = "Recurso não encontrado.") -> None:
        super().__init__(detail, code="not_found")


class ValidationFailed(AppError):
    status_code = 422
    title = "Dados inválidos"


class BusinessRuleViolation(AppError):
    status_code = 422
    title = "Operação não permitida"


class InsufficientFunds(BusinessRuleViolation):
    def __init__(self, account_name: str) -> None:
        super().__init__(
            f"Saldo insuficiente na conta “{account_name}”, que não permite saldo negativo.",
            code="RN008",
        )


class ConflictError(AppError):
    status_code = 409
    title = "Conflito"


class IdempotencyConflict(ConflictError):
    def __init__(self) -> None:
        super().__init__(
            "Esta chave de idempotência já foi usada com dados diferentes.",
            code="idempotency_conflict",
        )


class AuthenticationRequired(AppError):
    status_code = 401
    title = "Autenticação necessária"

    def __init__(self, detail: str = "Faça login para continuar.") -> None:
        super().__init__(detail, code="auth_required")


class EmailNotVerified(AppError):
    status_code = 403
    title = "E-mail não verificado"

    def __init__(self) -> None:
        super().__init__("Confirme seu e-mail para acessar esta área.", code="email_not_verified")


class PermissionDenied(AppError):
    status_code = 403
    title = "Acesso negado"


class CsrfError(PermissionDenied):
    def __init__(self) -> None:
        super().__init__(
            "Falha na verificação de segurança (CSRF). Recarregue a página.", code="csrf"
        )


class RateLimited(AppError):
    status_code = 429
    title = "Muitas requisições"

    def __init__(self, retry_after: int) -> None:
        super().__init__(
            f"Muitas tentativas. Tente novamente em {retry_after} segundos.", code="rate_limited"
        )
        self.retry_after = retry_after


class PayloadTooLarge(AppError):
    status_code = 413
    title = "Arquivo muito grande"


class InvalidCredentials(AppError):
    """Mensagem única para e-mail inexistente ou senha incorreta (não revela qual)."""

    status_code = 401
    title = "Credenciais inválidas"

    def __init__(self) -> None:
        super().__init__("E-mail ou senha inválidos.", code="invalid_credentials")


def expect[T](value: T | None, message: str = "estado inconsistente") -> T:
    """Invariante interna (não depende de `assert`, que some com `python -O`)."""
    if value is None:
        raise RuntimeError(message)
    return value
