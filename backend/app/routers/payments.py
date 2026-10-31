from datetime import date
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..database import get_db
from ..deps import audit, current_user, require_roles, staff_or_admin
from ..models import InvStatus, Invoice, Payment, Role, User
from ..schemas import PaymentIn, PaymentOut

router = APIRouter(tags=["payments"])


def record_payment(db: Session, inv: Invoice, method: str, amount: Decimal, paid_on: date | None, note: str) -> Payment:
    if inv.status != InvStatus.confirmed:
        raise HTTPException(409, "Payments can only be recorded against confirmed invoices")
    balance = inv.total - inv.amount_paid
    if amount > balance:
        raise HTTPException(422, f"Amount exceeds outstanding balance ({balance})")
    pay = Payment(invoice_id=inv.id, method=method, amount=amount, paid_on=paid_on or date.today(), note=note)
    db.add(pay)
    inv.amount_paid += amount
    if inv.amount_paid >= inv.total:
        inv.status = InvStatus.paid                     # Draft -> Confirmed -> Paid
    return pay


@router.post("/payments", response_model=PaymentOut, status_code=201)
def create_payment(data: PaymentIn, db: Session = Depends(get_db), user: User = Depends(staff_or_admin)):
    inv = db.get(Invoice, data.invoice_id)
    if not inv:
        raise HTTPException(404, "Invoice not found")
    pay = record_payment(db, inv, data.method, data.amount, data.paid_on, data.note)
    db.flush()
    audit(db, user, "payment", "invoice", inv.id, f"{data.amount} via {data.method}")
    db.commit()
    return pay


@router.post("/portal/invoices/{iid}/pay", response_model=PaymentOut, status_code=201)
def portal_pay(iid: int, method: str = "card", db: Session = Depends(get_db),
               user: User = Depends(require_roles(Role.customer))):
    """Customer self-service (mock gateway): pays the full balance of THEIR invoice."""
    inv = db.get(Invoice, iid)
    if not inv or inv.customer_id != user.id:
        raise HTTPException(404, "Invoice not found")
    pay = record_payment(db, inv, method, inv.total - inv.amount_paid, None, "Paid via customer portal")
    db.flush(); db.commit()
    return pay


@router.get("/payments")
def list_payments(invoice_id: int | None = None, method: str | None = None,
                  date_from: date | None = None, date_to: date | None = None,
                  page: int = Query(1, ge=1), size: int = Query(25, ge=1, le=100),
                  db: Session = Depends(get_db), user: User = Depends(staff_or_admin)):
    stmt = select(Payment)
    if invoice_id: stmt = stmt.where(Payment.invoice_id == invoice_id)
    if method: stmt = stmt.where(Payment.method == method)
    if date_from: stmt = stmt.where(Payment.paid_on >= date_from)
    if date_to: stmt = stmt.where(Payment.paid_on <= date_to)
    total = db.scalar(select(func.count()).select_from(stmt.subquery()))
    rows = db.scalars(stmt.order_by(Payment.id.desc()).offset((page - 1) * size).limit(size)).all()
    return {"total": total, "page": page, "size": size, "items": [PaymentOut.model_validate(r) for r in rows]}
