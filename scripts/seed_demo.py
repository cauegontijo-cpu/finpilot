"""Gera um cenário fictício de 12 meses para demonstração (somente ENV=development).

Uso:
    python -m scripts.seed_demo            # cria se o usuário demo não existir (idempotente)
    python -m scripts.seed_demo --reset    # apaga SOMENTE o usuário demo e recria

Credenciais: variáveis DEMO_EMAIL e DEMO_PASSWORD (sem valor padrão no código).
Todos os dados são criados pelos serviços da aplicação — saldos, parcelas e limites
obedecem às mesmas regras de produção. Ao final, os saldos são reconciliados.
"""

from __future__ import annotations

import argparse
import random
import sys
import uuid
from datetime import UTC, date, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.clock import Clock
from app.core.config import Environment, get_settings
from app.core.database import get_sessionmaker
from app.core.errors import ValidationFailed
from app.core.mailer import MemoryMailer
from app.core.ratelimit import MemoryRateLimiter
from app.domain.dates import Month, clamp_day
from app.domain.ledger import TransactionStatus, TransactionType
from app.models import Account, AccountType, Category, CategoryKind, Transaction, User
from app.services.accounts import AccountInput, AccountService
from app.services.audit import AuditService, RequestMeta
from app.services.auth import AuthService
from app.services.budgets import BudgetService
from app.services.transactions import TransactionInput, TransactionService
from app.services.transfers import TransferInput, TransferService

META = RequestMeta(ip="127.0.0.1", user_agent="seed-demo", request_id="seed-demo")
SEED = 20261016


class DemoBuilder:
    def __init__(self, db: Session, user: User, today: date) -> None:
        self.db = db
        self.user = user
        self.today = today
        self.clock = Clock()
        self.audit = AuditService(db, META)
        self.rng = random.Random(SEED)
        self.accounts = AccountService(db, self.clock, self.audit)
        self.tx = TransactionService(db, self.clock, self.audit)
        self.transfers = TransferService(db, self.clock, self.audit)
        self.budgets = BudgetService(db, self.clock, self.audit)
        self.cats = {
            (c.name, c.kind): c for c in db.scalars(select(Category).where(Category.user_id == user.id))
        }

    def cat(self, name: str, kind: CategoryKind = CategoryKind.EXPENSE) -> uuid.UUID:
        return self.cats[(name, kind)].id

    def add(
        self,
        account: Account,
        tx_type: TransactionType,
        category: str,
        description: str,
        cents: int,
        when: date,
        *,
        planned: bool = False,
        due: date | None = None,
    ) -> None:
        if when > self.today and not planned:
            return
        kind = CategoryKind.INCOME if tx_type is TransactionType.INCOME else CategoryKind.EXPENSE
        self.tx.create(
            self.user.id,
            TransactionInput(
                type=tx_type,
                account_id=account.id,
                category_id=self.cat(category, kind),
                description=description,
                amount_cents=cents,
                competence_date=when,
                status=TransactionStatus.PLANNED if planned else TransactionStatus.SETTLED,
                settlement_date=None if planned else when,
                due_date=due,
            ),
            idempotency_key=uuid.uuid4().hex,
        )

    def spend(self, account: Account, category: str, description: str, cents: int, when: date) -> None:
        self.add(account, TransactionType.EXPENSE, category, description, cents, when)

    def money(self, low: int, high: int) -> int:
        """Valor aleatório determinístico em centavos (múltiplos de 10)."""
        return self.rng.randrange(low // 10, high // 10) * 10

    def build(self) -> None:
        start_month = Month.of(self.today).shift(-11)
        start = start_month.first_day - timedelta(days=5)
        checking = self.accounts.create(self.user.id, AccountInput("Conta Corrente Nubank", AccountType.CHECKING, 320_000, start, institution="Nubank", color="#8b5cf6", icon="landmark"))
        savings = self.accounts.create(self.user.id, AccountInput("Poupança Caixa", AccountType.SAVINGS, 850_000, start, institution="Caixa", color="#0ea5e9", icon="piggy-bank"))
        wallet = self.accounts.create(self.user.id, AccountInput("Carteira", AccountType.WALLET, 30_000, start, color="#f97316", icon="banknote"))
        digital = self.accounts.create(self.user.id, AccountInput("Conta Digital Inter", AccountType.DIGITAL, 60_000, start, institution="Banco Inter", color="#f97316", icon="smartphone"))

        for i in range(12):
            month = start_month.shift(i)
            y, m = month.year, month.month
            d = lambda day: clamp_day(y, m, day)  # noqa: E731

            self.add(checking, TransactionType.INCOME, "Salário", "Salário — Empresa Exemplo Ltda.", 780_000, d(5))
            if month.month in (3, 6, 8, 11) or self.rng.random() < 0.25:
                self.add(digital, TransactionType.INCOME, "Freelance", "Projeto freelance de design", self.money(80_000, 250_000), d(18))
            if month.month == 12:
                self.add(checking, TransactionType.INCOME, "Salário", "13º salário", 780_000, d(20))
            if self.rng.random() < 0.3:
                self.add(savings, TransactionType.INCOME, "Investimentos", "Rendimento CDB", self.money(3_000, 9_000), d(28))

            self.spend(checking, "Moradia", "Aluguel do apartamento", 210_000, d(10))
            self.spend(checking, "Moradia", "Conta de energia", self.money(16_000, 26_000), d(14))
            self.spend(checking, "Moradia", "Internet fibra", 11_990, d(15))
            self.spend(checking, "Assinaturas", "Streaming de vídeo", 5_590, d(12))
            self.spend(checking, "Assinaturas", "Streaming de música", 2_190, d(12))
            self.spend(checking, "Educação", "Curso de inglês", 45_000, d(8))
            for k in range(self.rng.randint(4, 6)):
                self.spend(checking, "Alimentação", self.rng.choice(["Supermercado", "Feira", "Padaria", "Hortifruti"]), self.money(9_000, 42_000), d(3 + k * 5))
            for k in range(self.rng.randint(5, 9)):
                self.spend(digital if k % 3 == 0 else checking, "Transporte", self.rng.choice(["Aplicativo de transporte", "Combustível", "Estacionamento"]), self.money(1_800, 9_500), d(2 + k * 3))
            for k in range(self.rng.randint(2, 4)):
                self.spend(checking, "Lazer", self.rng.choice(["Cinema", "Restaurante", "Show", "Bar com amigos"]), self.money(4_000, 18_000), d(6 + k * 7))
            if self.rng.random() < 0.4:
                self.spend(checking, "Saúde", self.rng.choice(["Farmácia", "Consulta médica", "Exames"]), self.money(6_000, 35_000), d(21))
            if month.month in (11, 12):
                self.spend(checking, "Compras", "Presentes de fim de ano" if month.month == 12 else "Black Friday", self.money(40_000, 90_000), d(24))
            self.spend(wallet, "Alimentação", "Lanche", self.money(1_500, 4_000), d(19))
            if d(1) <= self.today:
                self.transfers.create(
                    self.user.id,
                    TransferInput(checking.id, wallet.id, 15_000, d(1), "Saque para a carteira"),
                    idempotency_key=uuid.uuid4().hex,
                )
                self.transfers.create(
                    self.user.id,
                    TransferInput(checking.id, digital.id, 20_000, d(1), "Recarga da conta digital"),
                    idempotency_key=uuid.uuid4().hex,
                )
            if d(25) <= self.today:
                self.transfers.create(
                    self.user.id,
                    TransferInput(checking.id, savings.id, 80_000, d(25), "Reserva mensal"),
                    idempotency_key=uuid.uuid4().hex,
                )

        current = Month.of(self.today)
        nxt = current.shift(1)
        # Contas a vencer (previstas) — alimentam a previsão de caixa.
        for when, desc, cat, cents in [
            (self.today + timedelta(days=3), "IPVA — parcela", "Transporte", 38_000),
            (self.today + timedelta(days=9), "Seguro residencial", "Moradia", 12_500),
            (clamp_day(nxt.year, nxt.month, 10), "Aluguel do apartamento", "Moradia", 210_000),
            (clamp_day(nxt.year, nxt.month, 14), "Conta de energia", "Moradia", 21_000),
        ]:
            self.add(checking, TransactionType.EXPENSE, cat, desc, cents, when, planned=True, due=when)
        self.add(checking, TransactionType.INCOME, "Salário", "Salário — Empresa Exemplo Ltda.", 780_000, clamp_day(nxt.year, nxt.month, 5), planned=True)
        self.add(digital, TransactionType.INCOME, "Reembolsos", "Reembolso de despesas de viagem", 34_000, self.today + timedelta(days=6), planned=True)

        for month in (current.shift(-1), current):
            for name, limit in [("Alimentação", 160_000), ("Transporte", 45_000), ("Lazer", 30_000), ("Assinaturas", 9_000), ("Moradia", 260_000), ("Compras", 50_000), ("Educação", 45_000)]:
                self.budgets.create(self.user.id, self.cat(name), month, limit)

    def reconcile(self) -> None:
        for account in self.db.scalars(select(Account).where(Account.user_id == self.user.id)):
            rows = self.db.execute(
                select(Transaction.type, func.sum(Transaction.amount_cents))
                .where(Transaction.account_id == account.id, Transaction.status == "settled")
                .group_by(Transaction.type)
            ).all()
            expected = account.initial_balance_cents + sum(
                int(t) if tx_type.value in ("income", "transfer_in") else -int(t) for tx_type, t in rows
            )
            if expected != account.current_balance_cents:
                raise SystemExit(f"Inconsistência de saldo em {account.name}: {expected} != {account.current_balance_cents}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reset", action="store_true", help="apaga e recria somente o usuário demo")
    args = parser.parse_args()

    settings = get_settings()
    if settings.env is not Environment.DEVELOPMENT:
        sys.exit("seed_demo só pode ser executado com ENV=development.")
    if not settings.demo_email or not settings.demo_password:
        sys.exit("Defina DEMO_EMAIL e DEMO_PASSWORD para criar o usuário de demonstração.")
    email = settings.demo_email.lower()
    password = settings.demo_password.get_secret_value()

    with get_sessionmaker()() as db:
        existing = db.scalars(select(User).where(func.lower(User.email) == email)).one_or_none()
        if existing and not args.reset:
            print(f"Usuário demo já existe ({email}). Use --reset para recriar.")
            return
        if existing:
            db.delete(existing)
            db.commit()
        auth = AuthService(db, settings, Clock(), MemoryRateLimiter(), MemoryMailer(), META)
        try:
            auth.register(
                full_name="Usuária Demonstração", email=email, password=password,
                password_confirm=password, accept_privacy=True,
            )
        except ValidationFailed as exc:
            sys.exit(f"Dados do usuário demo inválidos: {exc.field_errors}")
        user = db.scalars(select(User).where(func.lower(User.email) == email)).one()
        user.email_verified_at = datetime.now(UTC)
        db.commit()
        builder = DemoBuilder(db, user, Clock().today())
        builder.build()
        builder.reconcile()
        count = db.scalar(select(func.count()).select_from(Transaction).where(Transaction.user_id == user.id))
        print(f"Cenário de demonstração criado para {email}: {count} lançamentos. Saldos reconciliados.")


if __name__ == "__main__":
    main()
