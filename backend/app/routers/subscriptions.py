from datetime import date, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..database import get_db
from ..deps import audit, current_user, staff_or_admin
from ..models import (Plan, Product, ProductVariant, QuotationTemplate, Role, SubStatus,
                      Subscription, SubscriptionLine, User)
from ..schemas import FromTemplateIn, StatusIn, SubLineIn, SubscriptionIn, SubscriptionOut

router = APIRouter(prefix="/subscriptions", tags=["subscriptions"])

# Draft -> Quotation -> Confirmed -> Active -> Closed (+ Paused when the plan allows it)
TRANSITIONS = {
    SubStatus.draft: {SubStatus.quotation, SubStatus.closed},
    SubStatus.quotation: {SubStatus.draft, SubStatus.confirmed, SubStatus.closed},
    SubStatus.confirmed: {SubStatus.active, SubStatus.closed},
    SubStatus.active: {SubStatus.paused, SubStatus.closed},
    SubStatus.paused: {SubStatus.active, SubStatus.closed},
    SubStatus.closed: set(),
}


def _line(db: Session, plan: Plan, li: SubLineIn) -> SubscriptionLine:
    product = db.get(Product, li.product_id)
    if not product:
        raise HTTPException(404, f"Product {li.product_id} not found")
    if li.quantity < plan.min_quantity:
        raise HTTPException(422, f"Plan '{plan.name}' requires a minimum quantity of {plan.min_quantity}")
    price, desc = product.sales_price, product.name
    if li.variant_id:
        v = db.get(ProductVariant, li.variant_id)
        if not v or v.product_id != product.id:
            raise HTTPException(422, "Variant does not belong to this product")
        price += v.extra_price                      # e.g. brand Odoo -> +560
        desc += f" ({v.attribute}: {v.value})"
    return SubscriptionLine(product_id=product.id, variant_id=li.variant_id, description=desc,
                            quantity=li.quantity, unit_price=price, tax_id=li.tax_id)


def _new_sub(db: Session, data: SubscriptionIn, valid_until: date | None = None) -> Subscription:
    plan = db.get(Plan, data.plan_id)
    if not plan:
        raise HTTPException(404, "Plan not found")
    if not db.get(User, data.customer_id):
        raise HTTPException(404, "Customer not found")
    if plan.start_date and data.start_date < plan.start_date or plan.end_date and data.start_date > plan.end_date:
        raise HTTPException(422, "Plan is not available on that start date")
    sub = Subscription(number="TMP", customer_id=data.customer_id, plan_id=plan.id, start_date=data.start_date,
                       expiry_date=data.expiry_date, payment_terms=data.payment_terms,
                       payment_terms_days=data.payment_terms_days, discount_id=data.discount_id,
                       quote_valid_until=valid_until)
    sub.lines = [_line(db, plan, l) for l in data.lines]
    db.add(sub)
    db.flush()
    sub.number = f"SUB-{sub.id:05d}"
    return sub


@router.post("", response_model=SubscriptionOut, status_code=201)
def create(data: SubscriptionIn, db: Session = Depends(get_db), user: User = Depends(staff_or_admin)):
    sub = _new_sub(db, data)
    audit(db, user, "create", "subscription", sub.id)
    db.commit()
    return sub


@router.post("/from-template/{tid}", response_model=SubscriptionOut, status_code=201)
def from_template(tid: int, data: FromTemplateIn, db: Session = Depends(get_db),
                  user: User = Depends(staff_or_admin)):
    t = db.get(QuotationTemplate, tid)
    if not t:
        raise HTTPException(404, "Template not found")
    body = SubscriptionIn(customer_id=data.customer_id, plan_id=t.plan_id, start_date=data.start_date,
                          payment_terms=data.payment_terms, payment_terms_days=data.payment_terms_days,
                          lines=[SubLineIn(product_id=l.product_id, variant_id=l.variant_id, quantity=l.quantity)
                                 for l in t.lines])
    sub = _new_sub(db, body, valid_until=date.today() + timedelta(days=t.validity_days))
    audit(db, user, "create_from_template", "subscription", sub.id)
    db.commit()
    return sub


@router.get("")
def list_subs(status: SubStatus | None = None, customer_id: int | None = None,
              page: int = Query(1, ge=1), size: int = Query(25, ge=1, le=100),
              db: Session = Depends(get_db), user: User = Depends(current_user)):
    stmt = select(Subscription)
    if user.role == Role.customer:                  # portal: only my own
        stmt = stmt.where(Subscription.customer_id == user.id)
    elif customer_id:
        stmt = stmt.where(Subscription.customer_id == customer_id)
    if status:
        stmt = stmt.where(Subscription.status == status)
    total = db.scalar(select(func.count()).select_from(stmt.subquery()))
    rows = db.scalars(stmt.order_by(Subscription.id.desc()).offset((page - 1) * size).limit(size)).unique().all()
    return {"total": total, "page": page, "size": size,
            "items": [SubscriptionOut.model_validate(r) for r in rows]}


def _owned(db: Session, sid: int, user: User) -> Subscription:
    sub = db.get(Subscription, sid)
    if not sub or (user.role == Role.customer and sub.customer_id != user.id):
        raise HTTPException(404, "Subscription not found")      # 404, not 403: don't leak existence
    return sub


@router.get("/{sid}", response_model=SubscriptionOut)
def get_one(sid: int, db: Session = Depends(get_db), user: User = Depends(current_user)):
    return _owned(db, sid, user)


@router.post("/{sid}/status", response_model=SubscriptionOut)
def change_status(sid: int, data: StatusIn, db: Session = Depends(get_db), user: User = Depends(current_user)):
    sub = _owned(db, sid, user)
    new = data.status
    if new not in TRANSITIONS[sub.status]:
        raise HTTPException(409, f"Cannot move from {sub.status.value} to {new.value}")
    if new == SubStatus.paused and not sub.plan.pausable:
        raise HTTPException(409, "This plan cannot be paused")
    if new == SubStatus.closed and not sub.plan.closable:
        raise HTTPException(409, "This plan cannot be closed early")
    if user.role == Role.customer and new not in (SubStatus.paused, SubStatus.active, SubStatus.closed):
        raise HTTPException(403, "Customers can only pause, resume or close their subscription")
    if user.role == Role.customer and new == SubStatus.active and sub.status != SubStatus.paused:
        raise HTTPException(403, "Customers can only resume a paused subscription")
    if new == SubStatus.active and sub.next_billing_date is None:
        sub.next_billing_date = sub.start_date          # billing starts on activation
    if new == SubStatus.closed:
        sub.next_billing_date = None
    audit(db, user, f"status:{sub.status.value}->{new.value}", "subscription", sub.id)
    sub.status = new
    db.commit()
    return sub


@router.post("/{sid}/renew", response_model=SubscriptionOut, status_code=201)
def renew(sid: int, db: Session = Depends(get_db), user: User = Depends(staff_or_admin)):
    old = db.get(Subscription, sid)
    if not old:
        raise HTTPException(404, "Subscription not found")
    if not old.plan.renewable:
        raise HTTPException(409, "This plan cannot be renewed")
    data = SubscriptionIn(customer_id=old.customer_id, plan_id=old.plan_id,
                          start_date=old.expiry_date or date.today(), payment_terms=old.payment_terms,
                          payment_terms_days=old.payment_terms_days, discount_id=old.discount_id,
                          lines=[SubLineIn(product_id=l.product_id, variant_id=l.variant_id,
                                           quantity=l.quantity, tax_id=l.tax_id) for l in old.lines])
    sub = _new_sub(db, data)
    audit(db, user, "renew", "subscription", sub.id, f"from {old.number}")
    db.commit()
    return sub
