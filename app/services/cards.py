"""Cartões de crédito, compras parceladas, faturas e pagamentos (seção 14, RN061–RN070).

Separação dos regimes (ADR-007):
* COMPETÊNCIA — cada parcela é despesa na competência da sua fatura;
* CAIXA — o pagamento da fatura é uma saída da conta bancária (`card_payment`), que
  NÃO é despesa de consumo (evita dupla contagem — RN066);
* LIMITE — utilizado = parcelas ativas (líquidas de estornos) − pagamentos; nunca é um
  contador armazenado, logo não há duplicidade (RN064/RN068).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import date
from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session, joinedload

from app.core.clock import Clock
from app.core.errors import BusinessRuleViolation, ValidationFailed
from app.domain.dates import Month
from app.domain.installments import MAX_INSTALLMENTS, plan_purchase, statement_dates, statement_month_for_purchase
from app.domain.money import format_brl
from app.domain.ledger import TransactionOrigin, TransactionStatus, TransactionType
from app.models import (
    CardInstallment,
    CardPayment,
    CardPurchase,
    CardStatement,
    CategoryKind,
    CreditCard,
    InstallmentKind,
    InstallmentStatus,
    PaymentStatus,
    PurchaseStatus,
    Transaction,
)
from app.models.base import model_snapshot
from app.models.finance import MAX_CENTS
from app.repositories.base import get_owned
from app.services.accounts import AccountService
from app.services.audit import AuditService, diff
from app.services.card_queries import StatementBalance, card_debt, statement_balances
from app.services.categories import CategoryService
from app.services.idempotency import IdempotencyGuard
from app.services.ledger import Ledger
from app.utils.validation import clean_text, optional_text, validate_color

_CARD_FIELDS = ["name", "institution", "limit_cents", "closing_day", "due_day", "last4", "color", "archived_at"]


@dataclass(frozen=True)
class CardInput:
    name: str
    limit_cents: int
    closing_day: int
    due_day: int
    institution: str | None = None
    last4: str | None = None
    color: str = "#0b1b3f"


@dataclass(frozen=True)
class PurchaseInput:
    category_id: str | uuid.UUID
    description: str
    purchase_date: date
    total_cents: int
    installments: int = 1

    def fingerprint(self) -> dict[str, Any]:
        return {k: str(v) for k, v in self.__dict__.items()}


@dataclass
class CardSummary:
    card: CreditCard
    debt: int
    current: StatementBalance | None
    next_due: StatementBalance | None

    @property
    def available(self) -> int:
        return self.card.limit_cents - self.debt

    @property
    def used_pct(self) -> float:
        return round(self.debt * 100 / self.card.limit_cents, 1) if self.card.limit_cents else 0.0


class CardService:
    def __init__(self, db: Session, clock: Clock, audit: AuditService) -> None:
        self.db = db
        self.clock = clock
        self.audit = audit
        self.accounts = AccountService(db, clock, audit)
        self.categories = CategoryService(db, clock, audit)

    # ---------------------------------------------------------------- cartões
    def get(self, user_id: uuid.UUID, card_id: str | uuid.UUID, *, for_update: bool = False) -> CreditCard:
        return get_owned(self.db, CreditCard, card_id, user_id, for_update=for_update, message="Cartão não encontrado.")

    def list(self, user_id: uuid.UUID, *, include_archived: bool = False) -> list[CreditCard]:
        stmt = select(CreditCard).where(CreditCard.user_id == user_id)
        if not include_archived:
            stmt = stmt.where(CreditCard.archived_at.is_(None))
        return list(self.db.scalars(stmt.order_by(CreditCard.name)))

    def summaries(self, user_id: uuid.UUID) -> list[CardSummary]:
        today = self.clock.today()
        balances = statement_balances(self.db, user_id)
        result = []
        for card in self.list(user_id):
            mine = [b for b in balances if b.card_id == card.id]
            current_month = statement_month_for_purchase(today, card.closing_day)
            current = next((b for b in mine if (b.year, b.month) == (current_month.year, current_month.month)), None)
            pending = [b for b in mine if b.remaining_cents > 0 and b.closing_date < today]
            result.append(CardSummary(card, card_debt(self.db, user_id, card.id), current, pending[0] if pending else None))
        return result

    def _validate_card(self, data: CardInput) -> CardInput:
        errors: dict[str, str] = {}
        name = clean_text(data.name, max_len=60)
        if not name:
            errors["name"] = "Informe o nome do cartão."
        if data.limit_cents <= 0 or data.limit_cents > MAX_CENTS:
            errors["limit"] = "Informe um limite positivo."
        if not 1 <= data.closing_day <= 31:
            errors["closing_day"] = "Dia entre 1 e 31."
        if not 1 <= data.due_day <= 31:
            errors["due_day"] = "Dia entre 1 e 31."
        last4 = (data.last4 or "").strip() or None
        if last4 is not None and (len(last4) != 4 or not last4.isdigit()):
            errors["last4"] = "Informe apenas os 4 últimos dígitos."
        if errors:
            raise ValidationFailed("Revise os campos destacados.", field_errors=errors)
        return CardInput(
            name=name, limit_cents=data.limit_cents, closing_day=data.closing_day, due_day=data.due_day,
            institution=optional_text(data.institution, max_len=80), last4=last4, color=validate_color(data.color, "#0b1b3f"),
        )

    def create(self, user_id: uuid.UUID, data: CardInput) -> CreditCard:
        data = self._validate_card(data)
        card = CreditCard(user_id=user_id, **data.__dict__)
        self.db.add(card)
        self.db.flush()
        self.audit.record("card.created", user_id=user_id, entity_type="card", entity_id=card.id, changes=model_snapshot(card, _CARD_FIELDS))
        self.db.commit()
        return card

    def update(self, user_id: uuid.UUID, card_id: str, data: CardInput) -> CreditCard:
        """Novos dias de fechamento/vencimento valem para faturas ainda não criadas."""
        card = self.get(user_id, card_id, for_update=True)
        data = self._validate_card(data)
        debt = card_debt(self.db, user_id, card.id)
        if data.limit_cents < debt:
            raise BusinessRuleViolation(
                "O novo limite é menor que o valor já comprometido no cartão.", code="RN064",
                field_errors={"limit": "Abaixo do utilizado"},
            )
        before = model_snapshot(card, _CARD_FIELDS)
        for key, value in data.__dict__.items():
            setattr(card, key, value)
        self.audit.record("card.updated", user_id=user_id, entity_type="card", entity_id=card.id, changes=diff(before, model_snapshot(card, _CARD_FIELDS)))
        self.db.commit()
        return card

    def set_archived(self, user_id: uuid.UUID, card_id: str, archived: bool) -> CreditCard:
        card = self.get(user_id, card_id, for_update=True)
        if archived and card_debt(self.db, user_id, card.id) > 0:
            raise BusinessRuleViolation("Quite as faturas antes de arquivar o cartão.", code="card_has_debt")
        card.archived_at = self.clock.now() if archived else None
        self.audit.record("card.archived" if archived else "card.restored", user_id=user_id, entity_type="card", entity_id=card.id)
        self.db.commit()
        return card

    # ---------------------------------------------------------------- faturas
    def _statement_for(self, card: CreditCard, month: Month) -> CardStatement:
        """Obtém ou cria (de forma segura sob concorrência) a fatura do mês."""
        dates = statement_dates(month, card.closing_day, card.due_day)
        self.db.execute(
            insert(CardStatement)
            .values(
                id=uuid.uuid4(), user_id=card.user_id, card_id=card.id, year=month.year, month=month.month,
                closing_date=dates.closing_date, due_date=dates.due_date,
            )
            .on_conflict_do_nothing(index_elements=["card_id", "year", "month"])
        )
        return self.db.scalars(
            select(CardStatement).where(
                CardStatement.card_id == card.id, CardStatement.year == month.year, CardStatement.month == month.month
            )
        ).one()

    def statements(self, user_id: uuid.UUID, card_id: str) -> list[StatementBalance]:
        card = self.get(user_id, card_id)
        return statement_balances(self.db, user_id, card_id=card.id)

    def statement(self, user_id: uuid.UUID, statement_id: str) -> tuple[CardStatement, StatementBalance]:
        statement = get_owned(self.db, CardStatement, statement_id, user_id, message="Fatura não encontrada.")
        balance = statement_balances(self.db, user_id, statement_ids=[statement.id])[0]
        return statement, balance

    def statement_items(self, statement: CardStatement) -> list[CardInstallment]:
        return list(
            self.db.scalars(
                select(CardInstallment)
                .options(joinedload(CardInstallment.purchase).joinedload(CardPurchase.category))
                .where(CardInstallment.statement_id == statement.id)
                .order_by(CardInstallment.created_at)
            ).unique()
        )

    def statement_payments(self, statement: CardStatement) -> list[CardPayment]:
        return list(
            self.db.scalars(
                select(CardPayment).where(CardPayment.statement_id == statement.id).order_by(CardPayment.payment_date, CardPayment.created_at)
            )
        )

    # ---------------------------------------------------------------- compras
    def purchases(self, user_id: uuid.UUID, card_id: str, limit: int = 50) -> list[CardPurchase]:
        card = self.get(user_id, card_id)
        return list(
            self.db.scalars(
                select(CardPurchase)
                .options(joinedload(CardPurchase.category), joinedload(CardPurchase.installments))
                .where(CardPurchase.card_id == card.id)
                .order_by(CardPurchase.purchase_date.desc(), CardPurchase.created_at.desc())
                .limit(limit)
            ).unique()
        )

    def purchase(self, user_id: uuid.UUID, card_id: str, data: PurchaseInput, *, idempotency_key: str) -> CardPurchase:
        guard = IdempotencyGuard(self.db, self.clock, user_id, idempotency_key, "card.purchase")
        replay = guard.begin(data.fingerprint())
        if replay is not None:
            return get_owned(self.db, CardPurchase, replay, user_id)
        card = self.get(user_id, card_id, for_update=True)  # serializa compras do cartão (limite)
        if not card.is_active:
            raise BusinessRuleViolation("Cartão arquivado não aceita compras.", code="card_archived")
        errors: dict[str, str] = {}
        description = clean_text(data.description, max_len=140)
        if not description:
            errors["description"] = "Informe a descrição."
        if data.total_cents <= 0 or data.total_cents > MAX_CENTS:
            errors["amount"] = "Informe um valor positivo."
        if not 1 <= data.installments <= MAX_INSTALLMENTS:
            errors["installments"] = f"Entre 1 e {MAX_INSTALLMENTS} parcelas."
        elif data.total_cents < data.installments:
            errors["installments"] = "Cada parcela deve ter ao menos R$ 0,01."  # RN063
        if data.purchase_date > self.clock.today():
            errors["purchase_date"] = "A data da compra não pode ser futura."
        if errors:
            raise ValidationFailed("Revise os campos destacados.", field_errors=errors)
        category = self.categories.require_for_type(user_id, data.category_id, CategoryKind.EXPENSE)
        available = card.limit_cents - card_debt(self.db, user_id, card.id)
        if data.total_cents > available:
            raise BusinessRuleViolation(
                f"Limite disponível insuficiente ({format_brl(available)}).", code="RN064"
            )
        plan = plan_purchase(data.purchase_date, data.total_cents, data.installments, card.closing_day, card.due_day)
        assert sum(p.amount_cents for p in plan) == data.total_cents  # RN061 (garantido pelo domínio)
        purchase = CardPurchase(
            user_id=user_id, card_id=card.id, category_id=category.id, description=description,
            purchase_date=data.purchase_date, total_cents=data.total_cents, installments_count=data.installments,
            status=PurchaseStatus.ACTIVE,
        )
        self.db.add(purchase)
        self.db.flush()
        for item in plan:
            statement = self._statement_for(card, item.statement.month)
            self.db.add(
                CardInstallment(
                    user_id=user_id, purchase_id=purchase.id, statement_id=statement.id, number=item.number,
                    kind=InstallmentKind.CHARGE, amount_cents=item.amount_cents,
                    competence_date=statement.closing_date, status=InstallmentStatus.ACTIVE,
                )
            )
        guard.complete(purchase.id)
        self.audit.record(
            "card.purchase_created", user_id=user_id, entity_type="card_purchase", entity_id=purchase.id,
            changes={"card_id": str(card.id), "total_cents": data.total_cents, "parcelas": data.installments,
                     "valores": [p.amount_cents for p in plan], "data": data.purchase_date.isoformat()},
        )
        self.db.commit()
        return purchase

    def refund(self, user_id: uuid.UUID, purchase_id: str) -> CardPurchase:
        """Estorno: cancela parcelas de faturas abertas e credita as já faturadas (RN070)."""
        purchase = get_owned(self.db, CardPurchase, purchase_id, user_id, for_update=True, message="Compra não encontrada.")
        if purchase.status is PurchaseStatus.REFUNDED:
            return purchase
        card = self.get(user_id, purchase.card_id, for_update=True)
        today = self.clock.today()
        billed = 0
        cancelled = 0
        installments = list(
            self.db.scalars(
                select(CardInstallment)
                .options(joinedload(CardInstallment.statement))
                .where(CardInstallment.purchase_id == purchase.id, CardInstallment.kind == InstallmentKind.CHARGE, CardInstallment.status == InstallmentStatus.ACTIVE)
            )
        )
        for inst in installments:
            if inst.statement.closing_date >= today:
                inst.status = InstallmentStatus.CANCELLED
                cancelled += inst.amount_cents
            else:
                billed += inst.amount_cents
        if billed:
            target = self._statement_for(card, statement_month_for_purchase(today, card.closing_day))
            self.db.add(
                CardInstallment(
                    user_id=user_id, purchase_id=purchase.id, statement_id=target.id, number=1,
                    kind=InstallmentKind.CREDIT, amount_cents=billed, competence_date=target.closing_date,
                    status=InstallmentStatus.ACTIVE,
                )
            )
        purchase.status = PurchaseStatus.REFUNDED
        purchase.refunded_at = self.clock.now()
        self.audit.record(
            "card.purchase_refunded", user_id=user_id, entity_type="card_purchase", entity_id=purchase.id,
            changes={"parcelas_canceladas_cents": cancelled, "credito_em_fatura_cents": billed},
        )
        self.db.commit()
        return purchase

    # ---------------------------------------------------------------- pagamentos
    def pay(
        self, user_id: uuid.UUID, statement_id: str, account_id: str | uuid.UUID | None, amount_cents: int, payment_date: date,
        *, idempotency_key: str,
    ) -> CardPayment:
        guard = IdempotencyGuard(self.db, self.clock, user_id, idempotency_key, "card.payment")
        replay = guard.begin({"statement": statement_id, "account": str(account_id), "amount": amount_cents, "date": payment_date.isoformat()})
        if replay is not None:
            return get_owned(self.db, CardPayment, replay, user_id)
        statement = get_owned(self.db, CardStatement, statement_id, user_id, for_update=True, message="Fatura não encontrada.")
        balance = statement_balances(self.db, user_id, statement_ids=[statement.id])[0]
        if amount_cents <= 0:
            raise ValidationFailed("Informe um valor positivo.", field_errors={"amount": "Valor inválido"})
        if amount_cents > balance.remaining_cents:
            # RN069: crédito positivo no cartão não é suportado — pagamento acima do devido é recusado.
            raise BusinessRuleViolation(
                f"O pagamento excede o saldo devido da fatura ({format_brl(balance.remaining_cents)}).",
                code="RN069", field_errors={"amount": "Acima do devido"},
            )
        if payment_date > self.clock.today():
            raise ValidationFailed("A data do pagamento não pode ser futura.", field_errors={"payment_date": "Data futura"})
        account = self.accounts.require_active(user_id, account_id)
        if payment_date < account.start_date:
            raise ValidationFailed("Data anterior ao início da conta.", field_errors={"payment_date": "Data inválida"})
        card = self.get(user_id, statement.card_id)
        ledger = Ledger(self.db)
        ledger.lock(user_id, [account.id])
        tx = Transaction(
            user_id=user_id, account_id=account.id, type=TransactionType.CARD_PAYMENT, status=TransactionStatus.SETTLED,
            origin=TransactionOrigin.CARD, description=f"Pagamento fatura {card.name} {statement.month:02d}/{statement.year}",
            amount_cents=amount_cents, competence_date=payment_date, settlement_date=payment_date,
        )
        self.db.add(tx)
        self.db.flush()
        ledger.apply(tx)  # RN065: reduz o dinheiro na conta de origem (com checagem de saldo)
        payment = CardPayment(
            user_id=user_id, statement_id=statement.id, account_id=account.id, transaction_id=tx.id,
            amount_cents=amount_cents, payment_date=payment_date, status=PaymentStatus.ACTIVE,
        )
        self.db.add(payment)
        self.db.flush()
        guard.complete(payment.id)
        self.audit.record(
            "card.payment_created", user_id=user_id, entity_type="card_statement", entity_id=statement.id,
            changes={"amount_cents": amount_cents, "account_id": str(account.id), "restante_antes_cents": balance.remaining_cents},
        )
        self.db.commit()
        return payment

    def cancel_payment(self, user_id: uuid.UUID, payment_id: str) -> CardPayment:
        payment = get_owned(self.db, CardPayment, payment_id, user_id, for_update=True, message="Pagamento não encontrado.")
        if payment.status is PaymentStatus.CANCELLED:
            return payment
        get_owned(self.db, CardStatement, payment.statement_id, user_id, for_update=True)
        tx = self.db.get(Transaction, payment.transaction_id)
        assert tx is not None
        ledger = Ledger(self.db)
        ledger.lock(user_id, [tx.account_id])
        ledger.reverse(tx)  # devolve o dinheiro à conta
        tx.status = TransactionStatus.CANCELLED
        tx.cancelled_at = self.clock.now()
        payment.status = PaymentStatus.CANCELLED
        payment.cancelled_at = self.clock.now()
        self.audit.record(
            "card.payment_cancelled", user_id=user_id, entity_type="card_statement", entity_id=payment.statement_id,
            changes={"amount_cents": payment.amount_cents},
        )
        self.db.commit()
        return payment

