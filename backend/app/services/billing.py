"""Billing engine: subscription -> invoice. Idempotent by design.

Why nothing is billed twice or skipped:
  * `next_billing_date` on the subscription is the cursor; it only moves forward after an
    invoice for that exact period exists.
  * DB unique constraint (subscription_id, period_start) rejects duplicates even if two
    workers run at once (savepoint catches the IntegrityError and we simply skip).
  * Catch-up loop: if the job was down for 3 days, missed periods are generated, not lost.
"""
import calendar
import uuid
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..models import (BillingPeriod, Discount, DiscountKind, InvStatus, Invoice,
                      InvoiceLine, SubStatus, Subscription, Tax)

CENT = Decimal("0.01")


def q(x: Decimal) -> Decimal:
    return Decimal(x).quantize(CENT, rounding=ROUND_HALF_UP)


def add_months(d: date, months: int) -> date:
    m = d.month - 1 + months
    y, m = d.year + m // 12, m % 12 + 1
    return date(y, m, min(d.day, calendar.monthrange(y, m)[1]))


def add_period(d: date, period: BillingPeriod) -> date:
    if period == BillingPeriod.daily:
        return d + timedelta(days=1)
    if period == BillingPeriod.weekly:
        return d + timedelta(days=7)
    if period == BillingPeriod.monthly:
        return add_months(d, 1)
    return add_months(d, 12)


def monthly_factor(period: BillingPeriod) -> Decimal:
    return {BillingPeriod.daily: Decimal("30"), BillingPeriod.weekly: Decimal("4.345"),
            BillingPeriod.monthly: Decimal("1"), BillingPeriod.yearly: Decimal("0.0833333")}[period]


def discount_is_valid(d: Discount, on: date, qty: int, purchase: Decimal) -> bool:
    if d.start_date and on < d.start_date:
        return False
    if d.end_date and on > d.end_date:
        return False
    if d.usage_limit is not None and d.used_count >= d.usage_limit:
        return False
    if qty < (d.min_quantity or 0):
        return False
    if purchase < (d.min_purchase or 0):
        return False
    return True


def discount_amount(d: Discount, base: Decimal) -> Decimal:
    amt = base * d.value / 100 if d.kind == DiscountKind.percent else d.value
    return q(min(amt, base))


def generate_invoice(db: Session, sub: Subscription, period_start: date, today: date,
                     product_discounts: list[Discount], taxes: dict[int, Tax]) -> Invoice | None:
    period_end = add_period(period_start, sub.plan.billing_period) - timedelta(days=1)
    inv = Invoice(number="TMP-" + uuid.uuid4().hex[:12], subscription_id=sub.id, customer_id=sub.customer_id,
                  period_start=period_start, period_end=period_end, invoice_date=today,
                  due_date=today + timedelta(days=sub.payment_terms_days), status=InvStatus.draft)
    used: list[Discount] = []

    # 1) lines with product-level discounts (best valid one per line)
    rows = []
    for ln in sub.lines:
        gross = q(ln.unit_price * ln.quantity)
        best, best_amt = None, Decimal("0")
        for d in product_discounts:
            if d.product_id == ln.product_id and discount_is_valid(d, today, ln.quantity, gross):
                amt = discount_amount(d, gross)
                if amt > best_amt:
                    best, best_amt = d, amt
        if best:
            used.append(best)
        rows.append([ln, gross, best_amt])

    # 2) subscription-level discount, allocated proportionally so tax stays correct
    sub_disc = sub.discount_id and db.get(Discount, sub.discount_id)
    after_line = sum((r[1] - r[2] for r in rows), Decimal("0"))
    total_qty = sum(r[0].quantity for r in rows)
    if sub_disc and discount_is_valid(sub_disc, today, total_qty, after_line):
        total_sub_disc = discount_amount(sub_disc, after_line)
        used.append(sub_disc)
        remaining = total_sub_disc
        for i, r in enumerate(rows):
            base = r[1] - r[2]
            share = remaining if i == len(rows) - 1 else q(total_sub_disc * base / after_line) if after_line else Decimal("0")
            share = min(share, base)
            r[2] += share
            remaining -= share

    subtotal = discount_total = tax_total = Decimal("0")
    for ln, gross, disc in rows:
        net = gross - disc
        tax = taxes.get(ln.tax_id) if ln.tax_id else None
        rate = tax.rate_percent if tax else Decimal("0")
        tax_amt = q(net * rate / 100)
        inv.lines.append(InvoiceLine(description=ln.description, quantity=ln.quantity, unit_price=ln.unit_price,
                                     discount=disc, tax_rate=rate, tax_amount=tax_amt, amount=net))
        subtotal += gross
        discount_total += disc
        tax_total += tax_amt
    inv.subtotal, inv.discount_total, inv.tax_total = subtotal, discount_total, tax_total
    inv.total = subtotal - discount_total + tax_total

    try:
        with db.begin_nested():          # savepoint: a duplicate only rolls back this invoice
            db.add(inv)
            db.flush()
            inv.number = f"INV-{inv.id:06d}"
            for d in used:
                d.used_count += 1        # discount stops itself once usage_limit is reached
    except IntegrityError:
        return None                      # already billed for this period -> skip silently
    return inv


def run_billing(db: Session, today: date | None = None, auto_confirm: bool = False) -> dict:
    today = today or date.today()
    due = db.scalars(select(Subscription).where(Subscription.status == SubStatus.active,
                                                Subscription.next_billing_date <= today)).unique().all()
    product_discounts = db.scalars(select(Discount).where(Discount.product_id.is_not(None))).all()
    taxes = {t.id: t for t in db.scalars(select(Tax)).all()}
    created = skipped = closed = 0

    for sub in due:
        while sub.next_billing_date and sub.next_billing_date <= today:
            if sub.expiry_date and sub.next_billing_date > sub.expiry_date:
                break
            inv = generate_invoice(db, sub, sub.next_billing_date, today, product_discounts, taxes)
            if inv:
                created += 1
                if auto_confirm:
                    inv.status = InvStatus.confirmed
            else:
                skipped += 1
            sub.next_billing_date = add_period(sub.next_billing_date, sub.plan.billing_period)

        ended = (sub.expiry_date and sub.next_billing_date and sub.next_billing_date > sub.expiry_date) or \
                (sub.plan.auto_close and sub.plan.end_date and today > sub.plan.end_date)
        if ended:
            sub.status, sub.next_billing_date = SubStatus.closed, None
            closed += 1
    db.commit()
    return {"as_of": today.isoformat(), "subscriptions_due": len(due),
            "invoices_created": created, "skipped_duplicates": skipped, "subscriptions_closed": closed}
