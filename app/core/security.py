"""Primitivas de segurança: hash de senha (Argon2id), tokens e CSRF."""

from __future__ import annotations

import hashlib
import hmac
import secrets
from urllib.parse import urlsplit

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

# Parâmetros alinhados à recomendação OWASP para Argon2id (m=19 MiB, t=2, p=1) com folga.
_hasher = PasswordHasher(time_cost=3, memory_cost=19 * 1024, parallelism=1, hash_len=32, salt_len=16)

# Hash fixo usado para equalizar o tempo de resposta quando o e-mail não existe.
_DUMMY_HASH = _hasher.hash(secrets.token_urlsafe(16))

MIN_PASSWORD_LENGTH = 10
MAX_PASSWORD_LENGTH = 128


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password_hash: str | None, password: str) -> bool:
    """Verifica a senha em tempo aproximadamente constante (mesmo sem usuário)."""
    try:
        return _hasher.verify(password_hash or _DUMMY_HASH, password) and password_hash is not None
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False


def needs_rehash(password_hash: str) -> bool:
    return _hasher.check_needs_rehash(password_hash)


def password_problems(password: str, *, email: str = "", name: str = "") -> list[str]:
    """Política de senha: comprimento + bloqueio de senhas triviais (NIST 800-63B)."""
    problems: list[str] = []
    if len(password) < MIN_PASSWORD_LENGTH:
        problems.append(f"A senha deve ter ao menos {MIN_PASSWORD_LENGTH} caracteres.")
    if len(password) > MAX_PASSWORD_LENGTH:
        problems.append(f"A senha deve ter no máximo {MAX_PASSWORD_LENGTH} caracteres.")
    lowered = password.lower()
    local = email.split("@")[0].lower()
    if (local and len(local) >= 4 and local in lowered) or (
        name and any(len(p) >= 4 and p.lower() in lowered for p in name.split())
    ):
        problems.append("A senha não pode conter seu nome ou e-mail.")
    if len(set(password)) < 5:
        problems.append("A senha é muito repetitiva.")
    if lowered in _COMMON_PASSWORDS:
        problems.append("Essa senha é muito comum.")
    return problems


_COMMON_PASSWORDS = {
    "1234567890", "12345678910", "senha12345", "password123", "qwertyuiop", "abcdefghij",
    "0123456789", "senha@1234", "admin12345", "brasil2026", "iloveyou12", "123456789a",
}


def generate_token(nbytes: int = 32) -> str:
    """Token criptograficamente seguro para URLs e cookies."""
    return secrets.token_urlsafe(nbytes)


def hash_token(token: str) -> str:
    """SHA-256 do token: armazenamos apenas o hash (tokens têm alta entropia)."""
    return hashlib.sha256(token.encode()).hexdigest()


def hmac_hex(secret: str, message: str) -> str:
    return hmac.new(secret.encode(), message.encode(), hashlib.sha256).hexdigest()


def csrf_token_for(secret: str, binding: str) -> str:
    """Token CSRF derivado (HMAC) do segredo do servidor e da sessão/visitante."""
    return hmac_hex(secret, f"csrf:{binding}")


def tokens_match(a: str | None, b: str | None) -> bool:
    if not a or not b:
        return False
    return hmac.compare_digest(a.encode(), b.encode())


def safe_next_url(candidate: str | None, default: str = "/app") -> str:
    """Previne open redirect: aceita apenas caminhos relativos internos."""
    if not candidate:
        return default
    if not candidate.startswith("/") or candidate.startswith(("//", "/\\")):
        return default
    parts = urlsplit(candidate)
    if parts.scheme or parts.netloc or any(c in candidate for c in "\r\n\t\\"):
        return default
    return candidate


def truncate_ip(ip: str | None) -> str | None:
    """Minimização de dados: IPv4 /24, IPv6 /48."""
    if not ip:
        return None
    if ":" in ip:
        return ":".join(ip.split(":")[:3]) + "::/48"
    parts = ip.split(".")
    if len(parts) == 4:
        return ".".join([*parts[:3], "0"]) + "/24"
    return None
