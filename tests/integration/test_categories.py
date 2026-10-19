"""Categorias: unicidade por escopo/tipo, arquivamento sem perda de histórico, exclusão segura."""

from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.core.clock import Clock
from app.core.errors import BusinessRuleViolation, ConflictError, ValidationFailed
from app.models import CategoryKind, Transaction
from app.services.categories import CategoryInput, CategoryService
from tests.conftest import post_form
from tests.factories import audit, category, make_account, make_tx


def service(db: Session) -> CategoryService:
    return CategoryService(db, Clock(), audit(db))


def test_default_categories_are_per_user(db: Session, user: Any, other_user: Any) -> None:
    mine = service(db).list(user.id)
    theirs = service(db).list(other_user.id)
    assert len(mine) == len(theirs) == 14
    assert not {c.id for c in mine} & {c.id for c in theirs}


def test_duplicate_name_same_kind_rejected_but_other_kind_allowed(db: Session, user: Any) -> None:
    svc = service(db)
    with pytest.raises(ConflictError):
        svc.create(user.id, CategoryInput("alimentação", CategoryKind.EXPENSE, "#f97316", "utensils"))
    created = svc.create(user.id, CategoryInput("Alimentação", CategoryKind.INCOME, "#f97316", "utensils"))
    assert created.kind is CategoryKind.INCOME


def test_invalid_color_icon_and_empty_name(db: Session, user: Any) -> None:
    svc = service(db)
    for data in (
        CategoryInput("", CategoryKind.EXPENSE, "#f97316", "tag"),
        CategoryInput("X", CategoryKind.EXPENSE, "red", "tag"),
        CategoryInput("X", CategoryKind.EXPENSE, "#123456", "tag"),
        CategoryInput("X", CategoryKind.EXPENSE, "#f97316", "<script>"),
    ):
        with pytest.raises(ValidationFailed):
            svc.create(user.id, data)


def test_archive_preserves_history_and_blocks_new_entries(db: Session, user: Any) -> None:
    account = make_account(db, user)
    tx = make_tx(db, user, account, 1_000, category_name="Lazer")
    lazer = category(db, user, "Lazer")
    service(db).set_archived(user.id, str(lazer.id), True)
    assert db.get(Transaction, tx.id).category_id == lazer.id
    with pytest.raises(BusinessRuleViolation, match="arquivada"):
        make_tx(db, user, account, 1_000, category_name="Lazer")
    service(db).set_archived(user.id, str(lazer.id), False)
    make_tx(db, user, account, 1_000, category_name="Lazer")


def test_delete_only_without_history_and_kind_change_blocked_in_use(db: Session, user: Any) -> None:
    account = make_account(db, user)
    svc = service(db)
    make_tx(db, user, account, 1_000, category_name="Saúde")
    saude = category(db, user, "Saúde")
    with pytest.raises(BusinessRuleViolation):
        svc.delete(user.id, str(saude.id))
    with pytest.raises(BusinessRuleViolation):
        svc.update(user.id, str(saude.id), CategoryInput("Saúde", CategoryKind.INCOME, saude.color, saude.icon))
    updated = svc.update(user.id, str(saude.id), CategoryInput("Saúde e bem-estar", CategoryKind.EXPENSE, "#ef4444", "heart-pulse"))
    assert updated.name == "Saúde e bem-estar"
    unused = category(db, user, "Educação")
    svc.delete(user.id, str(unused.id))
    assert all(c.id != unused.id for c in svc.list(user.id, include_archived=True))


def test_category_web_crud(auth_client: TestClient, user: Any, db: Session) -> None:
    hx = {"HX-Request": "true"}
    assert post_form(auth_client, "/app/categorias", {"name": "Pets", "kind": "expense", "color": "#14b8a6", "icon": "tag"}, headers=hx).status_code == 204
    dup = post_form(auth_client, "/app/categorias", {"name": "pets", "kind": "expense", "color": "#14b8a6", "icon": "tag"}, headers=hx)
    assert dup.status_code == 422 and "Já existe" in dup.text
    pets = category(db, user, "Pets")
    assert auth_client.get(f"/app/categorias/{pets.id}/editar", headers=hx).status_code == 200
    assert post_form(auth_client, f"/app/categorias/{pets.id}", {"name": "Pets & Cia", "kind": "expense", "color": "#14b8a6", "icon": "tag"}, headers=hx).status_code == 204
    assert post_form(auth_client, f"/app/categorias/{pets.id}/arquivar", {}, headers=hx).status_code == 204
    assert post_form(auth_client, f"/app/categorias/{pets.id}/reativar", {}, headers=hx).status_code == 204
    assert post_form(auth_client, f"/app/categorias/{pets.id}/excluir", {}, headers=hx).status_code == 204
    assert "Pets" not in auth_client.get("/app/categorias").text
