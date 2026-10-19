"""Importação de extratos CSV (seção 13, RN051–RN060).

Fluxo: upload → mapeamento de colunas → prévia validada → seleção → confirmação → relatório.

Segurança do arquivo (documentada em docs/SECURITY.md):
* apenas extensão .csv; conteúdo validado (texto decodificável, sem bytes nulos, estrutura CSV);
* tamanho e número de linhas limitados por configuração (RN058);
* o arquivo nunca é gravado em disco nem usa o nome enviado como caminho; o conteúdo é
  tratado como texto puro — fórmulas, macros ou scripts nunca são avaliados (RN057);
* lotes e linhas pertencem ao usuário e só são acessíveis por ele (RN059).

Duplicatas (RN054/RN055):
* com identificador bancário: duplicata certa — não pode ser importada novamente;
* sem identificador: fingerprint SHA-256 de (conta, data, valor com sinal, descrição
  normalizada, ordem da repetição no arquivo) + comparação com lançamentos manuais de mesma
  data/valor/descrição → "possível duplicata", desmarcada por padrão, revisão humana.
"""

from __future__ import annotations

import builtins
import csv
import hashlib
import io
import re
import unicodedata
import uuid
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import PurePath
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.clock import Clock
from app.core.config import Settings
from app.core.errors import (
    BusinessRuleViolation,
    InsufficientFunds,
    PayloadTooLarge,
    ValidationFailed,
)
from app.domain.ledger import TransactionOrigin, TransactionStatus, TransactionType
from app.domain.money import MoneyParseError, parse_brl
from app.models import (
    Category,
    CategoryKind,
    ImportBatch,
    ImportRow,
    ImportRowStatus,
    ImportStatus,
    Transaction,
)
from app.models.finance import MAX_CENTS
from app.repositories.base import get_owned
from app.services.accounts import AccountService
from app.services.audit import AuditService
from app.services.idempotency import IdempotencyGuard
from app.services.ledger import Ledger

DATE_FORMATS = {
    "dd/mm/aaaa": "%d/%m/%Y",
    "aaaa-mm-dd": "%Y-%m-%d",
    "dd-mm-aaaa": "%d-%m-%Y",
    "mm/dd/aaaa": "%m/%d/%Y",
    "dd/mm/aa": "%d/%m/%y",
}
_HEADER_HINTS = {
    "date_col": ("data", "date", "dt", "lancamento", "lançamento"),
    "description_col": ("descri", "historico", "histórico", "memo", "estabelecimento", "detalhe"),
    "amount_col": ("valor", "amount", "montante", "quantia"),
    "type_col": ("tipo", "type", "d/c", "natureza", "sinal"),
    "category_col": ("categoria", "category"),
    "external_id_col": (
        "id",
        "identificador",
        "documento",
        "fitid",
        "transacao",
        "transação",
        "nsu",
    ),
}
_CREDIT = {"c", "cr", "credito", "crédito", "receita", "entrada", "+", "credit", "in"}
_DEBIT = {"d", "db", "debito", "débito", "despesa", "saida", "saída", "-", "debit", "out"}


@dataclass(frozen=True)
class Mapping:
    date_col: int
    description_col: int
    amount_col: int
    date_format: str
    has_header: bool
    type_col: int | None = None
    category_col: int | None = None
    external_id_col: int | None = None
    income_category_id: uuid.UUID | None = None
    expense_category_id: uuid.UUID | None = None

    def as_json(self) -> dict[str, Any]:
        return {k: (str(v) if isinstance(v, uuid.UUID) else v) for k, v in self.__dict__.items()}


@dataclass(frozen=True)
class PreviewStats:
    valid: int
    invalid: int
    duplicate: int
    possible_duplicate: int
    selected: int


def normalize_description(text: str) -> str:
    folded = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode().lower()
    return " ".join(re.sub(r"[^a-z0-9 ]+", " ", folded).split())


def fingerprint(
    account_id: uuid.UUID, day: date, signed_cents: int, description: str, occurrence: int
) -> str:
    raw = f"{account_id}|{day.isoformat()}|{signed_cents}|{normalize_description(description)}|{occurrence}"
    return hashlib.sha256(raw.encode()).hexdigest()


def safe_display_name(filename: str | None) -> str:
    """Somente para exibição — nunca usado como caminho no filesystem."""
    base = PurePath((filename or "extrato.csv").replace("\\", "/")).name
    cleaned = re.sub(r"[^\w.\- ()]+", "_", base, flags=re.UNICODE).strip() or "extrato.csv"
    return cleaned[:120]


def decode_csv(content: bytes) -> tuple[str, str]:
    if b"\x00" in content:
        raise ValidationFailed(
            "Arquivo binário ou corrompido: não é um CSV de texto.", code="invalid_file"
        )
    for encoding in ("utf-8-sig", "cp1252"):
        try:
            return content.decode(encoding), encoding
        except UnicodeDecodeError:
            continue
    raise ValidationFailed(
        "Codificação do arquivo não suportada (use UTF-8 ou Windows-1252).", code="invalid_file"
    )


def detect_delimiter(text: str) -> str:
    sample = "\n".join(text.splitlines()[:20])
    try:
        return csv.Sniffer().sniff(sample, delimiters=";,\t|").delimiter
    except csv.Error:
        counts = {d: sample.count(d) for d in (";", ",", "\t")}
        best = max(counts, key=lambda d: counts[d])
        if counts[best] == 0:
            raise ValidationFailed(
                "Não foi possível identificar o delimitador. Use ponto e vírgula, vírgula ou tabulação.",
                code="invalid_file",
            ) from None
        return best


def _looks_like_data(cell: str) -> bool:
    cell = cell.strip()
    if re.match(r"^\d{1,4}[/-]\d{1,2}[/-]\d{2,4}$", cell):
        return True
    try:
        parse_brl(cell)
    except MoneyParseError:
        return False
    return True


class ImportService:
    def __init__(self, db: Session, clock: Clock, audit: AuditService, settings: Settings) -> None:
        self.db = db
        self.clock = clock
        self.audit = audit
        self.settings = settings
        self.accounts = AccountService(db, clock, audit)

    def get(
        self, user_id: uuid.UUID, batch_id: str | uuid.UUID, *, for_update: bool = False
    ) -> ImportBatch:
        return get_owned(
            self.db,
            ImportBatch,
            batch_id,
            user_id,
            for_update=for_update,
            message="Importação não encontrada.",
        )

    def list(self, user_id: uuid.UUID, limit: int = 20) -> builtins.list[ImportBatch]:
        return list(
            self.db.scalars(
                select(ImportBatch)
                .where(ImportBatch.user_id == user_id)
                .order_by(ImportBatch.created_at.desc())
                .limit(limit)
            )
        )

    def rows(self, batch: ImportBatch) -> builtins.list[ImportRow]:
        return list(
            self.db.scalars(
                select(ImportRow)
                .where(ImportRow.batch_id == batch.id)
                .order_by(ImportRow.row_number)
            )
        )

    # ---------------------------------------------------------------- upload
    def upload(
        self, user_id: uuid.UUID, account_id: str | None, filename: str | None, content: bytes
    ) -> ImportBatch:
        account = self.accounts.require_active(user_id, account_id)
        if not (filename or "").lower().endswith(".csv"):
            raise ValidationFailed(
                "Envie um arquivo com extensão .csv.", field_errors={"file": "Somente .csv"}
            )
        if len(content) > self.settings.import_max_bytes:
            raise PayloadTooLarge(
                f"Arquivo maior que o limite de {self.settings.import_max_bytes // 1024} KB.",
                code="RN058",
            )
        if not content.strip():
            raise ValidationFailed("O arquivo está vazio.", field_errors={"file": "Arquivo vazio"})
        text, encoding = decode_csv(content)
        delimiter = detect_delimiter(text)
        try:
            raw_rows = [
                [cell.strip()[:300] for cell in row]
                for row in csv.reader(io.StringIO(text), delimiter=delimiter, strict=True)
                if any(cell.strip() for cell in row)
            ]
        except csv.Error as exc:
            raise ValidationFailed(f"Estrutura CSV inválida: {exc}.", code="invalid_file") from exc
        if not raw_rows:
            raise ValidationFailed("Nenhuma linha encontrada no arquivo.", code="invalid_file")
        if len(raw_rows) > self.settings.import_max_rows + 1:
            raise ValidationFailed(
                f"O arquivo excede o limite de {self.settings.import_max_rows} linhas.",
                code="RN058",
            )
        column_count = max(len(r) for r in raw_rows)
        if column_count < 2 or column_count > 50:
            raise ValidationFailed("Número de colunas não suportado.", code="invalid_file")
        has_header = not any(_looks_like_data(c) for c in raw_rows[0])
        headers = raw_rows[0] if has_header else [f"Coluna {i + 1}" for i in range(column_count)]
        headers = [
            (h or f"Coluna {i + 1}")[:60]
            for i, h in enumerate(headers + [""] * (column_count - len(headers)))
        ]

        batch = ImportBatch(
            user_id=user_id,
            account_id=account.id,
            status=ImportStatus.UPLOADED,
            display_name=safe_display_name(filename),
            file_sha256=hashlib.sha256(content).hexdigest(),
            encoding=encoding,
            delimiter=delimiter,
            has_header=has_header,
            headers=headers,
            column_count=column_count,
            expires_at=self.clock.now() + timedelta(hours=self.settings.import_batch_ttl_hours),
        )
        self.db.add(batch)
        self.db.flush()
        self.db.add_all(
            ImportRow(
                batch_id=batch.id, row_number=i + 1, raw=row + [""] * (column_count - len(row))
            )
            for i, row in enumerate(raw_rows)
        )
        self.audit.record(
            "import.uploaded",
            user_id=user_id,
            entity_type="import",
            entity_id=batch.id,
            changes={
                "linhas": len(raw_rows),
                "arquivo": batch.display_name,
                "sha256": batch.file_sha256[:16],
            },
        )
        self.db.commit()
        return batch

    def previous_import_of_same_file(self, batch: ImportBatch) -> ImportBatch | None:
        return self.db.scalars(
            select(ImportBatch).where(
                ImportBatch.user_id == batch.user_id,
                ImportBatch.account_id == batch.account_id,
                ImportBatch.file_sha256 == batch.file_sha256,
                ImportBatch.status == ImportStatus.CONFIRMED,
                ImportBatch.id != batch.id,
            )
        ).first()

    def guess_mapping(self, batch: ImportBatch) -> dict[str, Any]:
        guess: dict[str, Any] = {"has_header": batch.has_header, "date_format": "dd/mm/aaaa"}
        if batch.has_header:
            lowered = [
                unicodedata.normalize("NFKD", h).encode("ascii", "ignore").decode().lower()
                for h in batch.headers
            ]
            used: set[int] = set()
            for key, hints in _HEADER_HINTS.items():
                for idx, header in enumerate(lowered):
                    if idx in used or key in guess:
                        continue
                    if any(h in header for h in hints):
                        guess[key] = idx
                        used.add(idx)
        sample = next(
            (r for r in self.rows(batch)[(1 if batch.has_header else 0) :] if r.raw), None
        )
        if sample and "date_col" in guess:
            cell = sample.raw[guess["date_col"]]
            if re.match(r"^\d{4}-\d{2}-\d{2}", cell):
                guess["date_format"] = "aaaa-mm-dd"
            elif re.match(r"^\d{2}-\d{2}-\d{4}$", cell):
                guess["date_format"] = "dd-mm-aaaa"
        return guess

    def date_ambiguity(self, batch: ImportBatch, date_col: int) -> bool:
        """RN052: há datas que fazem sentido tanto em dd/mm quanto em mm/dd?"""
        ambiguous = False
        for row in self.rows(batch)[(1 if batch.has_header else 0) :]:
            m = re.match(
                r"^(\d{1,2})/(\d{1,2})/\d{2,4}$",
                row.raw[date_col] if date_col < len(row.raw) else "",
            )
            if not m:
                continue
            a, b = int(m.group(1)), int(m.group(2))
            if a > 12 or b > 12:
                return False  # um formato já foi descartado pelo próprio arquivo
            if a != b:
                ambiguous = True
        return ambiguous

    # ---------------------------------------------------------------- mapeamento / prévia
    def apply_mapping(self, user_id: uuid.UUID, batch_id: str, mapping: Mapping) -> PreviewStats:  # noqa: PLR0915 - validação linear por linha
        batch = self.get(user_id, batch_id, for_update=True)
        if batch.status is ImportStatus.CONFIRMED:
            raise BusinessRuleViolation(
                "Esta importação já foi confirmada.", code="import_confirmed"
            )
        cols = [mapping.date_col, mapping.description_col, mapping.amount_col]
        if len(set(cols)) != 3 or any(c < 0 or c >= batch.column_count for c in cols):
            raise ValidationFailed(
                "Selecione colunas distintas e válidas para data, descrição e valor.",
                code="invalid_mapping",
            )
        if mapping.date_format not in DATE_FORMATS:
            raise ValidationFailed(
                "Formato de data inválido.", field_errors={"date_format": "Inválido"}
            )
        categories = {
            (c.kind, c.name.lower()): c
            for c in self.db.scalars(
                select(Category).where(Category.user_id == user_id, Category.archived_at.is_(None))
            )
        }
        income_default = self._default_category(
            user_id, mapping.income_category_id, CategoryKind.INCOME, categories
        )
        expense_default = self._default_category(
            user_id, mapping.expense_category_id, CategoryKind.EXPENSE, categories
        )
        batch.has_header = mapping.has_header
        batch.mapping = mapping.as_json()
        batch.status = ImportStatus.MAPPED

        rows = self.rows(batch)
        seen_external: set[str] = set()
        occurrences: dict[tuple[date, int, str], int] = {}
        existing_external = set(
            self.db.scalars(
                select(Transaction.external_id).where(
                    Transaction.account_id == batch.account_id, Transaction.external_id.is_not(None)
                )
            )
        )
        parsed: builtins.list[tuple[ImportRow, date, int, str, str | None, str]] = []
        for row in rows:
            if mapping.has_header and row.row_number == 1:
                row.status, row.selected, row.errors = (
                    ImportRowStatus.SKIPPED,
                    False,
                    ["Linha de cabeçalho"],
                )
                continue
            errors: builtins.list[str] = []
            raw = row.raw
            day = self._parse_date(raw[mapping.date_col], mapping.date_format, errors)
            description = " ".join(raw[mapping.description_col].split())[:140]
            if not description:
                errors.append("Descrição vazia")
            signed = self._parse_amount(
                raw[mapping.amount_col],
                raw[mapping.type_col] if mapping.type_col is not None else None,
                errors,
            )
            external = (
                (raw[mapping.external_id_col].strip()[:120] or None)
                if mapping.external_id_col is not None
                else None
            )
            category_name = (
                raw[mapping.category_col].strip()[:50] if mapping.category_col is not None else ""
            )
            row.occurred_on, row.description, row.amount_cents = (
                day,
                description or None,
                signed or None,
            )
            row.external_id, row.category_name = external, category_name or None
            row.duplicate_of_id = None
            if errors or day is None or not signed:
                row.status, row.selected, row.errors, row.fingerprint = (
                    ImportRowStatus.INVALID,
                    False,
                    errors,
                    None,
                )
                continue
            key = (day, signed, normalize_description(description))
            occurrences[key] = occurrences.get(key, 0) + 1
            row.fingerprint = fingerprint(
                batch.account_id, day, signed, description, occurrences[key]
            )
            row.errors = None
            if external and (external in existing_external or external in seen_external):
                row.status, row.selected = ImportRowStatus.DUPLICATE, False
                row.errors = ["Identificador bancário já importado"]
            else:
                if external:
                    seen_external.add(external)
                row.status, row.selected = ImportRowStatus.VALID, True
                parsed.append((row, day, signed, description, external, category_name))

        self._flag_possible_duplicates(batch, parsed)
        for row, _day, signed, _desc, _ext, category_name in parsed:
            kind = CategoryKind.INCOME if signed > 0 else CategoryKind.EXPENSE
            matched = categories.get((kind, category_name.lower())) if category_name else None
            row.category_name = (
                matched or (income_default if signed > 0 else expense_default)
            ).name
        self.db.commit()
        return self.stats(batch)

    def _default_category(
        self,
        user_id: uuid.UUID,
        category_id: uuid.UUID | None,
        kind: CategoryKind,
        categories: dict[tuple[CategoryKind, str], Category],
    ) -> Category:
        if category_id:
            category = get_owned(
                self.db, Category, category_id, user_id, message="Categoria não encontrada."
            )
            if category.kind is not kind or not category.is_active:
                raise ValidationFailed(
                    "Categoria padrão incompatível.", field_errors={"category": "Tipo incorreto"}
                )
            return category
        fallback = "outras receitas" if kind is CategoryKind.INCOME else "outros"
        found = categories.get((kind, fallback)) or next(
            (c for (k, _), c in categories.items() if k is kind), None
        )
        if found is None:
            raise ValidationFailed(
                "Cadastre ao menos uma categoria de receita e uma de despesa.", code="no_category"
            )
        return found

    def _flag_possible_duplicates(
        self,
        batch: ImportBatch,
        parsed: builtins.list[tuple[ImportRow, date, int, str, str | None, str]],
    ) -> None:
        if not parsed:
            return
        prints = {row.fingerprint for row, *_ in parsed}
        existing_prints = dict(
            self.db.execute(
                select(Transaction.import_fingerprint, Transaction.id).where(
                    Transaction.account_id == batch.account_id,
                    Transaction.import_fingerprint.in_(prints),
                    Transaction.status != TransactionStatus.CANCELLED,
                )
            ).all()
        )
        days = {d for _, d, *_ in parsed}
        manual = self.db.execute(
            select(
                Transaction.id,
                Transaction.competence_date,
                Transaction.type,
                Transaction.amount_cents,
                Transaction.description,
            ).where(
                Transaction.account_id == batch.account_id,
                Transaction.competence_date.in_(days),
                Transaction.status != TransactionStatus.CANCELLED,
                Transaction.type.in_(["income", "expense"]),
            )
        ).all()
        manual_index = {
            (d, (amount if t.value == "income" else -amount), normalize_description(desc)): tx_id
            for tx_id, d, t, amount, desc in manual
        }
        for row, day, signed, description, _ext, _cat in parsed:
            dup = existing_prints.get(row.fingerprint) or manual_index.get(
                (day, signed, normalize_description(description))
            )
            if dup:
                row.status, row.selected, row.duplicate_of_id = (
                    ImportRowStatus.POSSIBLE_DUPLICATE,
                    False,
                    dup,
                )
                row.errors = [
                    "Possível duplicata de lançamento existente — revise antes de importar"
                ]

    @staticmethod
    def _parse_date(cell: str, fmt_key: str, errors: builtins.list[str]) -> date | None:
        value = cell.strip()
        if not value:
            errors.append("Data vazia")
            return None
        try:
            parsed = datetime.strptime(  # noqa: DTZ007 - data civil, sem horário
                value[:10] if fmt_key == "aaaa-mm-dd" else value, DATE_FORMATS[fmt_key]
            ).date()
        except ValueError:
            errors.append(f"Data inválida para o formato {fmt_key}: {value[:20]}")
            return None
        if not 2000 <= parsed.year <= 2100:
            errors.append(f"Ano fora do intervalo aceito: {value[:20]}")
            return None
        return parsed

    @staticmethod
    def _parse_amount(cell: str, type_cell: str | None, errors: builtins.list[str]) -> int:  # noqa: PLR0911
        try:
            cents = parse_brl(cell)
        except MoneyParseError:
            errors.append(f"Valor inválido: {cell[:20] or 'vazio'}")
            return 0
        if cents == 0:
            errors.append("Valor igual a zero")
            return 0
        if abs(cents) > MAX_CENTS:
            errors.append("Valor acima do limite aceito")
            return 0
        if type_cell is not None and type_cell.strip():
            kind = unicodedata.normalize("NFC", type_cell.strip().lower())
            if kind in _CREDIT:
                return abs(cents)
            if kind in _DEBIT:
                return -abs(cents)
            errors.append(f"Tipo de movimento não reconhecido: {type_cell[:20]}")
            return 0
        return cents

    def stats(self, batch: ImportBatch) -> PreviewStats:
        counts = dict(
            self.db.execute(
                select(ImportRow.status, func.count())
                .where(ImportRow.batch_id == batch.id)
                .group_by(ImportRow.status)
            ).all()
        )
        selected = self.db.scalar(
            select(func.count())
            .select_from(ImportRow)
            .where(ImportRow.batch_id == batch.id, ImportRow.selected.is_(True))
        )
        return PreviewStats(
            valid=counts.get(ImportRowStatus.VALID, 0),
            invalid=counts.get(ImportRowStatus.INVALID, 0),
            duplicate=counts.get(ImportRowStatus.DUPLICATE, 0),
            possible_duplicate=counts.get(ImportRowStatus.POSSIBLE_DUPLICATE, 0),
            selected=int(selected or 0),
        )

    # ---------------------------------------------------------------- confirmação
    # Fluxo transacional único: validações por linha + savepoint por lançamento.
    def confirm(  # noqa: PLR0912, PLR0915
        self,
        user_id: uuid.UUID,
        batch_id: str,
        selected_row_ids: builtins.list[str],
        *,
        idempotency_key: str,
    ) -> dict[str, Any]:
        """RN051/RN060: só agora os lançamentos são criados, com relatório por linha."""
        guard = IdempotencyGuard(self.db, self.clock, user_id, idempotency_key, "import.confirm")
        batch = self.get(user_id, batch_id, for_update=True)  # serializa confirmações concorrentes
        if batch.status is ImportStatus.CONFIRMED:
            # Confirmação repetida (duplo clique, nova aba, reenvio): nada é reaplicado.
            return dict(batch.report or {})
        guard.begin({"batch": str(batch.id), "rows": sorted(selected_row_ids)})
        if batch.status is not ImportStatus.MAPPED or not batch.mapping:
            raise BusinessRuleViolation(
                "Revise o mapeamento antes de confirmar.", code="import_not_mapped"
            )
        if batch.expires_at < self.clock.now():
            raise BusinessRuleViolation(
                "Esta importação expirou. Envie o arquivo novamente.", code="import_expired"
            )

        wanted = {str(i) for i in selected_row_ids}
        rows = self.rows(batch)
        by_name = {
            (c.kind, c.name): c.id
            for c in self.db.scalars(select(Category).where(Category.user_id == user_id))
        }
        ledger = Ledger(self.db)
        account = ledger.lock(user_id, [batch.account_id])[batch.account_id]
        self.db.flush()
        imported = failed = skipped = 0
        failures: builtins.list[dict[str, Any]] = []
        for row in rows:
            selectable = row.status in (ImportRowStatus.VALID, ImportRowStatus.POSSIBLE_DUPLICATE)
            if str(row.id) not in wanted or not selectable:
                if row.status in (ImportRowStatus.VALID, ImportRowStatus.POSSIBLE_DUPLICATE):
                    row.status = ImportRowStatus.SKIPPED
                    skipped += 1
                row.selected = False
                continue
            if not (row.occurred_on and row.amount_cents and row.description):
                raise RuntimeError("linha válida sem dados normalizados")
            tx_type = TransactionType.INCOME if row.amount_cents > 0 else TransactionType.EXPENSE
            kind = (
                CategoryKind.INCOME if tx_type is TransactionType.INCOME else CategoryKind.EXPENSE
            )
            category_id = by_name.get((kind, row.category_name or ""))
            reason: str | None = None
            if row.occurred_on > self.clock.today():
                reason = "Data futura: lançamentos importados são efetivados"
            elif row.occurred_on < account.start_date:
                reason = f"Data anterior ao início da conta ({account.start_date:%d/%m/%Y})"
            elif category_id is None:
                reason = "Categoria não encontrada"
            if reason is None:
                tx = Transaction(
                    user_id=user_id,
                    account_id=batch.account_id,
                    type=tx_type,
                    status=TransactionStatus.SETTLED,
                    origin=TransactionOrigin.IMPORTED,
                    description=row.description,
                    amount_cents=abs(row.amount_cents),
                    category_id=category_id,
                    competence_date=row.occurred_on,
                    settlement_date=row.occurred_on,
                    import_batch_id=batch.id,
                    external_id=row.external_id,
                    import_fingerprint=row.fingerprint,
                )
                try:
                    with self.db.begin_nested():
                        self.db.add(tx)
                        self.db.flush()
                        ledger.apply(tx)
                        self.db.flush()
                except InsufficientFunds as exc:
                    reason = exc.detail
                except IntegrityError:
                    reason = "Identificador bancário duplicado"
            if reason is None:
                row.status, row.transaction_id = ImportRowStatus.IMPORTED, tx.id
                imported += 1
            else:
                row.status, row.errors = ImportRowStatus.FAILED, [reason]
                failed += 1
                failures.append({"linha": row.row_number, "motivo": reason})
            row.selected = False
        invalid = sum(
            1 for r in rows if r.status in (ImportRowStatus.INVALID, ImportRowStatus.DUPLICATE)
        )
        report = {
            "importadas": imported,
            "falhas": failed,
            "ignoradas": skipped,
            "invalidas_ou_duplicadas": invalid,
            "detalhes_falhas": failures[:200],
            "confirmado_em": self.clock.now().isoformat(),
        }
        batch.status = ImportStatus.CONFIRMED
        batch.confirmed_at = self.clock.now()
        batch.report = report
        guard.complete(batch.id)
        self.audit.record(
            "import.confirmed",
            user_id=user_id,
            entity_type="import",
            entity_id=batch.id,
            changes={k: v for k, v in report.items() if k != "detalhes_falhas"},
        )
        self.db.commit()
        return report

    def discard(self, user_id: uuid.UUID, batch_id: str) -> None:
        batch = self.get(user_id, batch_id, for_update=True)
        if batch.status is ImportStatus.CONFIRMED:
            raise BusinessRuleViolation(
                "Importação confirmada não pode ser descartada.", code="import_confirmed"
            )
        self.audit.record(
            "import.discarded", user_id=user_id, entity_type="import", entity_id=batch.id
        )
        self.db.delete(batch)
        self.db.commit()
