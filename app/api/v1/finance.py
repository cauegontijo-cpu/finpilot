"""Recursos financeiros da API: contas, categorias, transações, transferências e orçamentos.

Usa exatamente os mesmos serviços das telas web — nenhuma regra é duplicada.
"""

from __future__ import annotations

from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, Header, Query, Response

from app.core.deps import Svc, VerifiedSession, financial_rate_limit
from app.domain.dates import Month
from app.domain.ledger import TransactionStatus
from app.models import CategoryKind
from app.repositories.base import parse_uuid
from app.schemas.common import PROBLEM_RESPONSES, PageOut
from app.schemas.finance import (
    AccountIn,
    AccountOut,
    BudgetIn,
    BudgetStatusOut,
    BudgetUpdate,
    CategoryIn,
    CategoryOut,
    HistoryEntryOut,
    SettleIn,
    TotalsOut,
    TransactionIn,
    TransactionOut,
    TransactionUpdate,
    TransferIn,
    TransferOut,
)
from app.services.accounts import AccountInput
from app.services.budgets import BudgetStatus
from app.services.categories import CategoryInput
from app.services.transactions import TransactionFilters, TransactionInput
from app.services.transfers import TransferInput

router = APIRouter(responses=PROBLEM_RESPONSES)
Write = Depends(financial_rate_limit)
IdemKey = Annotated[
    str,
    Header(
        alias="Idempotency-Key",
        min_length=8,
        max_length=80,
        description="Chave única por operação; repetir a requisição com a mesma chave não reaplica a operação.",
    ),
]


# ---------------------------------------------------------------- contas
@router.get("/accounts", response_model=list[AccountOut], tags=["accounts"])
def list_accounts(
    session: VerifiedSession, svc: Svc, include_archived: bool = False
) -> list[AccountOut]:
    return [
        AccountOut.model_validate(a)
        for a in svc.accounts.list(session.user.id, include_archived=include_archived)
    ]


@router.post(
    "/accounts", response_model=AccountOut, status_code=201, tags=["accounts"], dependencies=[Write]
)
def create_account(
    data: AccountIn, session: VerifiedSession, svc: Svc, response: Response
) -> AccountOut:
    account = svc.accounts.create(session.user.id, AccountInput(**data.model_dump()))
    response.headers["Location"] = f"/api/v1/accounts/{account.id}"
    return AccountOut.model_validate(account)


@router.get("/accounts/{account_id}", response_model=AccountOut, tags=["accounts"])
def get_account(account_id: str, session: VerifiedSession, svc: Svc) -> AccountOut:
    return AccountOut.model_validate(svc.accounts.get(session.user.id, account_id))


@router.put(
    "/accounts/{account_id}", response_model=AccountOut, tags=["accounts"], dependencies=[Write]
)
def update_account(
    account_id: str, data: AccountIn, session: VerifiedSession, svc: Svc
) -> AccountOut:
    return AccountOut.model_validate(
        svc.accounts.update(session.user.id, account_id, AccountInput(**data.model_dump()))
    )


@router.post(
    "/accounts/{account_id}/archive",
    response_model=AccountOut,
    tags=["accounts"],
    dependencies=[Write],
)
def archive_account(account_id: str, session: VerifiedSession, svc: Svc) -> AccountOut:
    return AccountOut.model_validate(svc.accounts.set_archived(session.user.id, account_id, True))


@router.post(
    "/accounts/{account_id}/restore",
    response_model=AccountOut,
    tags=["accounts"],
    dependencies=[Write],
)
def restore_account(account_id: str, session: VerifiedSession, svc: Svc) -> AccountOut:
    return AccountOut.model_validate(svc.accounts.set_archived(session.user.id, account_id, False))


@router.delete("/accounts/{account_id}", status_code=204, tags=["accounts"], dependencies=[Write])
def delete_account(account_id: str, session: VerifiedSession, svc: Svc) -> Response:
    svc.accounts.delete(session.user.id, account_id)
    return Response(status_code=204)


# ---------------------------------------------------------------- categorias
@router.get("/categories", response_model=list[CategoryOut], tags=["categories"])
def list_categories(
    session: VerifiedSession,
    svc: Svc,
    kind: CategoryKind | None = None,
    include_archived: bool = False,
) -> list[CategoryOut]:
    items = svc.categories.list(session.user.id, kind=kind, include_archived=include_archived)
    return [CategoryOut.model_validate(c) for c in items]


@router.post(
    "/categories",
    response_model=CategoryOut,
    status_code=201,
    tags=["categories"],
    dependencies=[Write],
)
def create_category(data: CategoryIn, session: VerifiedSession, svc: Svc) -> CategoryOut:
    return CategoryOut.model_validate(
        svc.categories.create(session.user.id, CategoryInput(**data.model_dump()))
    )


@router.put(
    "/categories/{category_id}",
    response_model=CategoryOut,
    tags=["categories"],
    dependencies=[Write],
)
def update_category(
    category_id: str, data: CategoryIn, session: VerifiedSession, svc: Svc
) -> CategoryOut:
    return CategoryOut.model_validate(
        svc.categories.update(session.user.id, category_id, CategoryInput(**data.model_dump()))
    )


@router.post(
    "/categories/{category_id}/archive",
    response_model=CategoryOut,
    tags=["categories"],
    dependencies=[Write],
)
def archive_category(category_id: str, session: VerifiedSession, svc: Svc) -> CategoryOut:
    return CategoryOut.model_validate(
        svc.categories.set_archived(session.user.id, category_id, True)
    )


@router.delete(
    "/categories/{category_id}", status_code=204, tags=["categories"], dependencies=[Write]
)
def delete_category(category_id: str, session: VerifiedSession, svc: Svc) -> Response:
    svc.categories.delete(session.user.id, category_id)
    return Response(status_code=204)


# ---------------------------------------------------------------- transações
@router.get("/transactions", response_model=PageOut[TransactionOut], tags=["transactions"])
def list_transactions(
    session: VerifiedSession,
    svc: Svc,
    start: date | None = None,
    end: date | None = None,
    account_id: str | None = None,
    category_id: str | None = None,
    status: TransactionStatus | None = None,
    kind: Annotated[str | None, Query(pattern="^(income|expense|transfer|card_payment)$")] = None,
    q: Annotated[str | None, Query(max_length=100)] = None,
    sort: Annotated[
        str, Query(description="Campo permitido; prefixo '-' para decrescente")
    ] = "-competence_date",
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 25,
) -> PageOut[TransactionOut]:
    filters = TransactionFilters(
        start=start,
        end=end,
        account_id=parse_uuid(account_id),
        category_id=parse_uuid(category_id),
        status=status,
        kind=kind,
        q=q,
        sort=sort,
        page=page,
        page_size=page_size,
    )
    result = svc.transactions.list(session.user.id, filters)
    return PageOut[TransactionOut](
        items=[TransactionOut.model_validate(t) for t in result.items],
        total=result.total,
        page=result.page,
        page_size=result.page_size,
    )


@router.get("/transactions/totals", response_model=TotalsOut, tags=["transactions"])
def transaction_totals(
    session: VerifiedSession, svc: Svc, start: date | None = None, end: date | None = None
) -> TotalsOut:
    totals = svc.transactions.totals(session.user.id, TransactionFilters(start=start, end=end))
    return TotalsOut(**totals.__dict__, net_settled=totals.net_settled)


@router.post(
    "/transactions",
    response_model=TransactionOut,
    status_code=201,
    tags=["transactions"],
    dependencies=[Write],
)
def create_transaction(
    data: TransactionIn,
    session: VerifiedSession,
    svc: Svc,
    idempotency_key: IdemKey,
    response: Response,
) -> TransactionOut:
    tx = svc.transactions.create(
        session.user.id, TransactionInput(**data.model_dump()), idempotency_key=idempotency_key
    )
    response.headers["Location"] = f"/api/v1/transactions/{tx.id}"
    return TransactionOut.model_validate(tx)


@router.get("/transactions/{tx_id}", response_model=TransactionOut, tags=["transactions"])
def get_transaction(tx_id: str, session: VerifiedSession, svc: Svc) -> TransactionOut:
    return TransactionOut.model_validate(svc.transactions.get(session.user.id, tx_id))


@router.put(
    "/transactions/{tx_id}",
    response_model=TransactionOut,
    tags=["transactions"],
    dependencies=[Write],
)
def update_transaction(
    tx_id: str, data: TransactionUpdate, session: VerifiedSession, svc: Svc
) -> TransactionOut:
    current = svc.transactions.get(session.user.id, tx_id)
    payload = TransactionInput(type=current.type, **data.model_dump())
    return TransactionOut.model_validate(svc.transactions.update(session.user.id, tx_id, payload))


@router.post(
    "/transactions/{tx_id}/settle",
    response_model=TransactionOut,
    tags=["transactions"],
    dependencies=[Write],
)
def settle_transaction(
    tx_id: str, data: SettleIn, session: VerifiedSession, svc: Svc
) -> TransactionOut:
    return TransactionOut.model_validate(
        svc.transactions.settle(session.user.id, tx_id, data.settlement_date)
    )


@router.post(
    "/transactions/{tx_id}/cancel",
    response_model=TransactionOut,
    tags=["transactions"],
    dependencies=[Write],
)
def cancel_transaction(tx_id: str, session: VerifiedSession, svc: Svc) -> TransactionOut:
    """Cancela (prevista) ou estorna (efetivada) — nunca apaga o registro."""
    return TransactionOut.model_validate(svc.transactions.cancel(session.user.id, tx_id))


@router.get(
    "/transactions/{tx_id}/history", response_model=list[HistoryEntryOut], tags=["transactions"]
)
def transaction_history(tx_id: str, session: VerifiedSession, svc: Svc) -> list[HistoryEntryOut]:
    return [
        HistoryEntryOut.model_validate(log)
        for log in svc.transactions.history(session.user.id, tx_id)
    ]


# ---------------------------------------------------------------- transferências
@router.get("/transfers", response_model=PageOut[TransferOut], tags=["transfers"])
def list_transfers(
    session: VerifiedSession,
    svc: Svc,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 25,
) -> PageOut[TransferOut]:
    result = svc.transfers.list(session.user.id, page, page_size)
    return PageOut[TransferOut](
        items=[TransferOut.model_validate(t) for t in result.items],
        total=result.total,
        page=result.page,
        page_size=result.page_size,
    )


@router.post(
    "/transfers",
    response_model=TransferOut,
    status_code=201,
    tags=["transfers"],
    dependencies=[Write],
)
def create_transfer(
    data: TransferIn, session: VerifiedSession, svc: Svc, idempotency_key: IdemKey
) -> TransferOut:
    return TransferOut.model_validate(
        svc.transfers.create(
            session.user.id, TransferInput(**data.model_dump()), idempotency_key=idempotency_key
        )
    )


@router.get("/transfers/{transfer_id}", response_model=TransferOut, tags=["transfers"])
def get_transfer(transfer_id: str, session: VerifiedSession, svc: Svc) -> TransferOut:
    return TransferOut.model_validate(svc.transfers.get(session.user.id, transfer_id))


@router.post(
    "/transfers/{transfer_id}/settle",
    response_model=TransferOut,
    tags=["transfers"],
    dependencies=[Write],
)
def settle_transfer(transfer_id: str, session: VerifiedSession, svc: Svc) -> TransferOut:
    return TransferOut.model_validate(svc.transfers.settle(session.user.id, transfer_id))


@router.post(
    "/transfers/{transfer_id}/cancel",
    response_model=TransferOut,
    tags=["transfers"],
    dependencies=[Write],
)
def cancel_transfer(transfer_id: str, session: VerifiedSession, svc: Svc) -> TransferOut:
    return TransferOut.model_validate(svc.transfers.cancel(session.user.id, transfer_id))


# ---------------------------------------------------------------- orçamentos
def _budget_out(item: BudgetStatus) -> BudgetStatusOut:
    b = item.budget
    return BudgetStatusOut(
        id=b.id,
        category_id=b.category_id,
        category_name=b.category.name,
        year=b.year,
        month=b.month,
        limit_cents=item.limit,
        used_cents=item.used,
        planned_cents=item.planned,
        remaining_cents=item.remaining,
        previous_used_cents=item.previous_used,
        percent_used=item.pct,
        level=item.level,
    )


@router.get("/budgets", response_model=list[BudgetStatusOut], tags=["budgets"])
def list_budgets(
    session: VerifiedSession,
    svc: Svc,
    year: Annotated[int, Query(ge=2000, le=2100)],
    month: Annotated[int, Query(ge=1, le=12)],
) -> list[BudgetStatusOut]:
    return [_budget_out(i) for i in svc.budgets.overview(session.user.id, Month(year, month)).items]


@router.post(
    "/budgets",
    response_model=BudgetStatusOut,
    status_code=201,
    tags=["budgets"],
    dependencies=[Write],
)
def create_budget(data: BudgetIn, session: VerifiedSession, svc: Svc) -> BudgetStatusOut:
    month = Month(data.year, data.month)
    budget = svc.budgets.create(session.user.id, data.category_id, month, data.limit_cents)
    item = next(
        i for i in svc.budgets.overview(session.user.id, month).items if i.budget.id == budget.id
    )
    return _budget_out(item)


@router.patch(
    "/budgets/{budget_id}", response_model=BudgetStatusOut, tags=["budgets"], dependencies=[Write]
)
def update_budget(
    budget_id: str, data: BudgetUpdate, session: VerifiedSession, svc: Svc
) -> BudgetStatusOut:
    budget = svc.budgets.update(session.user.id, budget_id, data.limit_cents)
    month = Month(budget.year, budget.month)
    return _budget_out(
        next(
            i
            for i in svc.budgets.overview(session.user.id, month).items
            if i.budget.id == budget.id
        )
    )


@router.delete("/budgets/{budget_id}", status_code=204, tags=["budgets"], dependencies=[Write])
def delete_budget(budget_id: str, session: VerifiedSession, svc: Svc) -> Response:
    svc.budgets.archive(session.user.id, budget_id)
    return Response(status_code=204)
