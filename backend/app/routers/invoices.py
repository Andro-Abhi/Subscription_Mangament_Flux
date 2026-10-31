from datetime import date, datetime
from html import escape

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import HTMLResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..database import get_db
from ..deps import audit, current_user, staff_or_admin
from ..models import InvStatus, Invoice, Role, User
from ..schemas import InvoiceOut
from ..services.billing import run_billing
from ..services.mailer import send_email

router = APIRouter(tags=["invoices"])


@router.post("/billing/run")
def billing_run(auto_confirm: bool = False, as_of: date | None = None,
                db: Session = Depends(get_db), user: User = Depends(staff_or_admin)):
    """'Billing day' button: safe to click twice - duplicates are impossible."""
    result = run_billing(db, as_of, auto_confirm)
    audit(db, user, "billing_run", "invoice", None, str(result)); db.commit()
    return result


def _visible(db: Session, iid: int, user: User) -> Invoice:
    inv = db.get(Invoice, iid)
    if not inv or (user.role == Role.customer and (inv.customer_id != user.id or inv.status == InvStatus.draft)):
        raise HTTPException(404, "Invoice not found")
    return inv


@router.get("/invoices")
def list_invoices(status: InvStatus | None = None, overdue: bool = False, outstanding: bool = False,
                  customer_id: int | None = None, date_from: date | None = None, date_to: date | None = None,
                  page: int = Query(1, ge=1), size: int = Query(25, ge=1, le=100),
                  db: Session = Depends(get_db), user: User = Depends(current_user)):
    stmt = select(Invoice)
    if user.role == Role.customer:
        stmt = stmt.where(Invoice.customer_id == user.id, Invoice.status != InvStatus.draft)
    elif customer_id:
        stmt = stmt.where(Invoice.customer_id == customer_id)
    if status:
        stmt = stmt.where(Invoice.status == status)
    if overdue:
        stmt = stmt.where(Invoice.status == InvStatus.confirmed, Invoice.due_date < date.today())
    if outstanding:
        stmt = stmt.where(Invoice.status == InvStatus.confirmed)
    if date_from:
        stmt = stmt.where(Invoice.invoice_date >= date_from)
    if date_to:
        stmt = stmt.where(Invoice.invoice_date <= date_to)
    total = db.scalar(select(func.count()).select_from(stmt.subquery()))
    rows = db.scalars(stmt.order_by(Invoice.id.desc()).offset((page - 1) * size).limit(size)).unique().all()
    return {"total": total, "page": page, "size": size, "items": [InvoiceOut.model_validate(r) for r in rows]}


@router.get("/invoices/{iid}", response_model=InvoiceOut)
def get_invoice(iid: int, db: Session = Depends(get_db), user: User = Depends(current_user)):
    return _visible(db, iid, user)


@router.post("/invoices/{iid}/confirm", response_model=InvoiceOut)
def confirm(iid: int, db: Session = Depends(get_db), user: User = Depends(staff_or_admin)):
    inv = _visible(db, iid, user)
    if inv.status != InvStatus.draft:
        raise HTTPException(409, "Only draft invoices can be confirmed")
    inv.status = InvStatus.confirmed
    audit(db, user, "confirm", "invoice", inv.id); db.commit()
    return inv


@router.post("/invoices/{iid}/cancel", response_model=InvoiceOut)
def cancel(iid: int, db: Session = Depends(get_db), user: User = Depends(staff_or_admin)):
    inv = _visible(db, iid, user)
    if inv.status == InvStatus.paid or inv.amount_paid > 0:
        raise HTTPException(409, "Invoices with payments cannot be cancelled")
    inv.status = InvStatus.cancelled
    audit(db, user, "cancel", "invoice", inv.id); db.commit()
    return inv


@router.post("/invoices/{iid}/send")
def send(iid: int, db: Session = Depends(get_db), user: User = Depends(staff_or_admin)):
    inv = _visible(db, iid, user)
    if inv.status not in (InvStatus.confirmed, InvStatus.paid):
        raise HTTPException(409, "Confirm the invoice before sending it")
    send_email(inv.customer.email, f"Invoice {inv.number} from Brightloop",
               f"Hi {inv.customer.name or ''},\nInvoice {inv.number}: total {inv.total}, due {inv.due_date}.")
    inv.sent_at = datetime.utcnow()
    audit(db, user, "send", "invoice", inv.id); db.commit()
    return {"message": f"Invoice {inv.number} sent to {inv.customer.email}"}


@router.get("/invoices/{iid}/print", response_class=HTMLResponse)
def print_invoice(iid: int, db: Session = Depends(get_db), user: User = Depends(current_user)):
    """Print-ready HTML (browser 'Save as PDF'). Swap for WeasyPrint/ReportLab if time allows."""
    inv = _visible(db, iid, user)
    rows = "".join(f"<tr><td>{escape(l.description)}</td><td>{l.quantity}</td><td>{l.unit_price}</td>"
                   f"<td>{l.discount}</td><td>{l.tax_rate}%</td><td>{l.amount + l.tax_amount}</td></tr>"
                   for l in inv.lines)
    return f"""<html><head><title>{inv.number}</title><style>
    body{{font-family:Arial;max-width:800px;margin:40px auto}}table{{width:100%;border-collapse:collapse}}
    td,th{{border-bottom:1px solid #ddd;padding:8px;text-align:left}}</style></head><body>
    <h1>Brightloop Software</h1><h2>Invoice {inv.number}</h2>
    <p>Customer: {escape(inv.customer.name or inv.customer.email)}<br>Date: {inv.invoice_date} | Due: {inv.due_date}
    <br>Status: {inv.status.value.upper()}</p>
    <table><tr><th>Item</th><th>Qty</th><th>Unit</th><th>Discount</th><th>Tax</th><th>Total</th></tr>{rows}</table>
    <h3>Subtotal {inv.subtotal} &minus; Discount {inv.discount_total} + Tax {inv.tax_total} = {inv.total}</h3>
    <p>Paid: {inv.amount_paid} | Balance: {inv.total - inv.amount_paid}</p></body></html>"""
