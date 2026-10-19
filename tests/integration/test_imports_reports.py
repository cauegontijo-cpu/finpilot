"""Importação CSV (RN051–RN060) e relatórios/exportações (seção 17)."""

import re
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.clock import Clock
from app.core.config import get_settings
from app.core.errors import PayloadTooLarge, ValidationFailed
from app.models import ImportRowStatus, ImportStatus, Transaction
from app.services.exporters import neutralize_formula
from app.services.imports import ImportService, Mapping
from tests.conftest import post_form
from tests.factories import audit, make_account, make_tx

CSV = (
    "Data;Descrição;Valor;Documento\n"
    "05/10/2026;Salário Outubro;7.800,00;A1\n"
    "06/10/2026;Supermercado;-245,90;A2\n"
    "06/10/2026;Supermercado;-245,90;A3\n"
    "31/02/2026;Data impossível;-10,00;A4\n"
    "07/10/2026;Valor ruim;abc;A5\n"
    '08/10/2026;=HYPERLINK("http://evil");-1,00;A6\n'
)


def service(db: Session) -> ImportService:
    return ImportService(db, Clock(), audit(db), get_settings())


def mapping(**kw: Any) -> Mapping:
    return Mapping(
        date_col=0,
        description_col=1,
        amount_col=2,
        date_format="dd/mm/aaaa",
        has_header=True,
        external_id_col=3,
        **kw,
    )


def test_upload_parses_without_creating_transactions(db: Session, user: Any) -> None:
    account = make_account(db, user, initial=0)
    batch = service(db).upload(user.id, str(account.id), "extrato.csv", CSV.encode("utf-8"))
    assert batch.status is ImportStatus.UPLOADED and batch.delimiter == ";" and batch.has_header
    assert db.scalar(select(func.count()).select_from(Transaction)) == 0  # RN051
    guess = service(db).guess_mapping(batch)
    assert (guess["date_col"], guess["description_col"], guess["amount_col"]) == (0, 1, 2)


def test_mapping_validates_rows_with_specific_reasons(db: Session, user: Any) -> None:
    account = make_account(db, user, initial=0)
    svc = service(db)
    batch = svc.upload(user.id, str(account.id), "extrato.csv", CSV.encode())
    stats = svc.apply_mapping(user.id, str(batch.id), mapping())
    assert stats.invalid == 2
    errors = {
        r.row_number: r.errors for r in svc.rows(batch) if r.status is ImportRowStatus.INVALID
    }
    assert "Data inválida" in errors[5][0]  # RN056
    assert "Valor inválido" in errors[6][0]


def test_confirm_creates_selected_rows_atomically_and_reports(db: Session, user: Any) -> None:
    account = make_account(db, user, initial=0)
    svc = service(db)
    batch = svc.upload(user.id, str(account.id), "extrato.csv", CSV.encode())
    svc.apply_mapping(user.id, str(batch.id), mapping())
    selected = [str(r.id) for r in svc.rows(batch) if r.selected]
    report = svc.confirm(user.id, str(batch.id), selected, idempotency_key="import-confirm-0001")
    assert report["importadas"] == 4 and report["falhas"] == 0
    db.refresh(account)
    assert account.current_balance_cents == 780_000 - 2 * 24_590 - 100
    stored = db.scalars(select(Transaction).where(Transaction.description.like("=%"))).one()
    assert stored.description.startswith(
        "=HYPERLINK"
    )  # armazenado como texto, nunca avaliado (RN057)
    again = svc.confirm(user.id, str(batch.id), selected, idempotency_key="import-confirm-0002")
    assert again == report  # confirmação repetida não reaplica
    assert db.scalar(select(func.count()).select_from(Transaction)) == 4


def test_reimport_same_file_flags_duplicates(db: Session, user: Any) -> None:
    account = make_account(db, user, initial=0)
    svc = service(db)
    first = svc.upload(user.id, str(account.id), "extrato.csv", CSV.encode())
    svc.apply_mapping(user.id, str(first.id), mapping())
    svc.confirm(
        user.id,
        str(first.id),
        [str(r.id) for r in svc.rows(first) if r.selected],
        idempotency_key="reimport-0001",
    )
    second = svc.upload(user.id, str(account.id), "extrato (1).csv", CSV.encode())
    assert svc.previous_import_of_same_file(second) is not None
    stats = svc.apply_mapping(user.id, str(second.id), mapping())
    assert stats.selected == 0 and stats.duplicate == 4  # RN055: nada pré-selecionado


def test_fingerprint_duplicates_without_bank_id_need_review(db: Session, user: Any) -> None:
    account = make_account(db, user, initial=100_000)
    make_tx(
        db,
        user,
        account,
        24_590,
        description="Supermercado",
        when=__import__("datetime").date(2026, 10, 6),
    )
    svc = service(db)
    content = "Data;Descrição;Valor\n06/10/2026;SUPERMERCADO;-245,90\n09/10/2026;Farmácia;-30,00\n"
    batch = svc.upload(user.id, str(account.id), "sem-id.csv", content.encode())
    stats = svc.apply_mapping(user.id, str(batch.id), Mapping(0, 1, 2, "dd/mm/aaaa", True))
    assert stats.possible_duplicate == 1 and stats.selected == 1  # RN054


def test_type_column_and_cp1252_encoding(db: Session, user: Any) -> None:
    account = make_account(db, user, initial=0)
    svc = service(db)
    content = "data,histórico,valor,tipo\n2026-10-01,Depósito,100.50,C\n2026-10-02,Tarifa,10.00,D\n"
    batch = svc.upload(user.id, str(account.id), "banco.csv", content.encode("cp1252"))
    assert batch.encoding == "cp1252" and batch.delimiter == ","
    svc.apply_mapping(user.id, str(batch.id), Mapping(0, 1, 2, "aaaa-mm-dd", True, type_col=3))
    amounts = [r.amount_cents for r in svc.rows(batch) if r.status is ImportRowStatus.VALID]
    assert amounts == [10_050, -1_000]


@pytest.mark.parametrize(
    ("filename", "content", "message"),
    [
        ("planilha.xlsx", b"PK\x03\x04", ".csv"),
        ("x.csv", b"MZ\x00\x00binario", "binário"),
        ("x.csv", b"", "vazio"),
        ("x.csv", b"apenas uma coluna\nsem separador\n", "delimitador"),
    ],
)
def test_invalid_uploads_rejected(
    db: Session, user: Any, filename: str, content: bytes, message: str
) -> None:
    account = make_account(db, user)
    with pytest.raises(ValidationFailed, match=message):
        service(db).upload(user.id, str(account.id), filename, content)


def test_upload_size_and_row_limits(db: Session, user: Any) -> None:
    account = make_account(db, user)
    big = b"a;b\n" * (get_settings().import_max_bytes // 4 + 10)
    with pytest.raises(PayloadTooLarge):
        service(db).upload(user.id, str(account.id), "grande.csv", big)
    many = ("d;v\n" + "01/10/2026;1,00\n" * (get_settings().import_max_rows + 5)).encode()
    if len(many) <= get_settings().import_max_bytes:
        with pytest.raises(ValidationFailed, match="limite"):
            service(db).upload(user.id, str(account.id), "linhas.csv", many)


def test_filename_never_used_as_path(db: Session, user: Any) -> None:
    account = make_account(db, user)
    batch = service(db).upload(
        user.id, str(account.id), "../../etc/passwd<script>.csv", CSV.encode()
    )
    assert "/" not in batch.display_name and "<" not in batch.display_name


def test_other_user_cannot_access_import(
    auth_client: TestClient, db: Session, other_user: Any
) -> None:
    account = make_account(db, other_user)
    batch = service(db).upload(other_user.id, str(account.id), "extrato.csv", CSV.encode())
    assert auth_client.get(f"/app/importacoes/{batch.id}").status_code == 404  # RN059
    assert post_form(auth_client, f"/app/importacoes/{batch.id}/descartar", {}).status_code == 404


def test_import_web_flow(auth_client: TestClient, user: Any, db: Session) -> None:
    account = make_account(db, user, initial=0)
    from tests.conftest import csrf_for

    response = auth_client.post(
        "/app/importacoes",
        data={"account_id": str(account.id), "csrf_token": csrf_for(auth_client)},
        files={"file": ("extrato.csv", CSV.encode(), "text/csv")},
        follow_redirects=False,
    )
    assert response.status_code == 303
    detail_url = response.headers["location"]
    page = auth_client.get(detail_url)
    assert "Mapeamento de colunas" in page.text
    batch_id = detail_url.rsplit("/", 1)[-1]
    form = {
        "date_col": "0",
        "description_col": "1",
        "amount_col": "2",
        "external_id_col": "3",
        "date_format": "dd/mm/aaaa",
        "has_header": "true",
    }
    assert (
        post_form(auth_client, f"/app/importacoes/{batch_id}/mapeamento", form).status_code == 303
    )
    preview = auth_client.get(f"/app/importacoes/{batch_id}").text
    rows = re.findall(r'name="rows" value="([0-9a-f-]{36})" checked', preview)
    assert len(rows) == 4
    key = re.search(r'name="idempotency_key" value="([0-9a-f]+)"', preview).group(1)
    data = {"rows": rows[:2], "idempotency_key": key, "csrf_token": csrf_for(auth_client)}
    assert (
        auth_client.post(
            f"/app/importacoes/{batch_id}/confirmar", data=data, follow_redirects=False
        ).status_code
        == 303
    )
    assert "Relatório da importação" in auth_client.get(f"/app/importacoes/{batch_id}").text
    db.refresh(account)
    assert account.current_balance_cents == 780_000 - 24_590


# ---------------------------------------------------------------- relatórios
@pytest.mark.parametrize("value", ["=1+1", "+cmd", "-2", "@SUM(A1)", "\tx", "\rx"])
def test_csv_formula_injection_neutralized(value: str) -> None:
    assert neutralize_formula(value).startswith("'")


def test_reports_render_and_export(auth_client: TestClient, user: Any, db: Session) -> None:
    account = make_account(db, user, initial=100_000)
    make_tx(db, user, account, 1_000, description="=cmd|' /C calc'!A0")
    for key in [
        "receitas-despesas",
        "categorias",
        "patrimonio",
        "comparativo",
        "orcamento",
        "movimentacoes",
        "recorrencias",
        "cartoes",
    ]:
        response = auth_client.get(f"/app/relatorios/{key}?start=2026-05&end=2026-10")
        assert response.status_code == 200, key
    csv_response = auth_client.get(
        "/app/relatorios/movimentacoes/exportar.csv?start=2026-10&end=2026-10"
    )
    assert csv_response.status_code == 200
    body = csv_response.content.decode("utf-8-sig")
    assert "Critério: Caixa" in body
    assert ";'=cmd|" in body  # fórmula neutralizada
    assert "-10,00" in body
    pdf = auth_client.get(
        "/app/relatorios/receitas-despesas/exportar.pdf?start=2026-01&end=2026-10"
    )
    assert pdf.status_code == 200 and pdf.content.startswith(b"%PDF")
    printable = auth_client.get("/app/relatorios/categorias?start=2026-10&end=2026-10&imprimir=1")
    assert "Emitido em" in printable.text and "Critério contábil" in printable.text


def test_report_period_validation_and_isolation(
    auth_client: TestClient, user: Any, other_user: Any, db: Session
) -> None:
    theirs = make_account(db, other_user, name="Bruno", initial=0)
    make_tx(
        db,
        other_user,
        make_account(db, other_user, name="B2", initial=50_000),
        777,
        description="Segredo",
    )
    assert (
        auth_client.get(
            f"/app/relatorios/movimentacoes/exportar.csv?account_id={theirs.id}"
        ).status_code
        == 404
    )
    body = auth_client.get(
        "/app/relatorios/movimentacoes/exportar.csv?start=2026-10&end=2026-10"
    ).content.decode("utf-8-sig")
    assert "Segredo" not in body
    too_long = auth_client.get("/app/relatorios/receitas-despesas?start=2020-01&end=2026-10")
    assert "Período máximo" in too_long.text
    assert auth_client.get("/app/relatorios/inexistente").status_code == 404
