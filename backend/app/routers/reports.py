from collections import defaultdict
from datetime import date
from decimal import Decimal

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..database import get_db
from ..deps import staff_or_admin
from ..models import InvStatus, Invoice, Payment, Plan, SubStatus, Subscription, SubscriptionLine, User
from ..services.billing import monthly_factor, q

router = APIRouter(prefix="/reports", tags=["reports"])


@router.get("/summary")
def summary(date_from: date | None = None, date_to: date | None = None,
            db: Session = Depends(get_db), _: User = Depends(staff_or_admin)):
    """One call powers the whole finance dashboard (a handful of indexed aggregates)."""
    today = date.today()
    counts = dict(db.execute(select(Subscription.status, func.count()).group_by(Subscription.status)).all())

    pay = select(func.coalesce(func.sum(Payment.amount), 0))
    if date_from: pay = pay.where(Payment.paid_on >= date_from)
    if date_to: pay = pay.where(Payment.paid_on <= date_to)
    revenue = db.scalar(pay)

    open_ = (Invoice.status == InvStatus.confirmed)
    outstanding = db.scalar(select(func.coalesce(func.sum(Invoice.total - Invoice.amount_paid), 0)).where(open_))
    overdue_n, overdue_amt = db.execute(
        select(func.count(), func.coalesce(func.sum(Invoice.total - Invoice.amount_paid), 0))
        .where(open_, Invoice.due_date < today)).one()
    inv_counts = dict(db.execute(select(Invoice.status, func.count()).group_by(Invoice.status)).all())

    # MRR: active subscriptions normalised to a month
    rows = db.execute(
        select(Plan.billing_period, func.sum(SubscriptionLine.quantity * SubscriptionLine.unit_price))
        .join(Subscription, Subscription.plan_id == Plan.id)
        .join(SubscriptionLine, SubscriptionLine.subscription_id == Subscription.id)
        .where(Subscription.status == SubStatus.active).group_by(Plan.billing_period)).all()
    mrr = sum((Decimal(v or 0) * monthly_factor(p) for p, v in rows), Decimal("0"))

    return {
        "subscriptions": {k.value: v for k, v in counts.items()},
        "active_subscriptions": counts.get(SubStatus.active, 0),
        "revenue_collected": q(Decimal(revenue)),
        "outstanding_amount": q(Decimal(outstanding)),
        "overdue": {"count": overdue_n, "amount": q(Decimal(overdue_amt))},
        "invoices": {k.value: v for k, v in inv_counts.items()},
        "mrr": q(mrr), "arr": q(mrr * 12),
    }


@router.get("/revenue-trend")
def revenue_trend(months: int = 12, db: Session = Depends(get_db), _: User = Depends(staff_or_admin)):
    daily = db.execute(select(Payment.paid_on, func.sum(Payment.amount)).group_by(Payment.paid_on)).all()
    by_month: dict[str, Decimal] = defaultdict(Decimal)
    for d, amt in daily:
        by_month[d.strftime("%Y-%m")] += Decimal(amt)
    return [{"month": m, "revenue": q(v)} for m, v in sorted(by_month.items())[-months:]]


@router.get("/overdue")
def overdue(page: int = 1, size: int = 25, db: Session = Depends(get_db), _: User = Depends(staff_or_admin)):
    stmt = select(Invoice).where(Invoice.status == InvStatus.confirmed, Invoice.due_date < date.today())
    total = db.scalar(select(func.count()).select_from(stmt.subquery()))
    rows = db.scalars(stmt.order_by(Invoice.due_date).offset((page - 1) * size).limit(size)).unique().all()
    today = date.today()
    return {"total": total, "items": [{
        "invoice": r.number, "customer": r.customer.email, "due_date": r.due_date,
        "days_overdue": (today - r.due_date).days, "balance": q(r.total - r.amount_paid)} for r in rows]}
