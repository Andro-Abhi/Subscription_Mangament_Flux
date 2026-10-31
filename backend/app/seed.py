"""python -m app.seed --n 2000   -> realistic demo data incl. thousands of subscriptions."""
import argparse
import random
from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy import select

from .database import Base, SessionLocal, engine
from .models import (BillingPeriod, Discount, DiscountKind, Invoice, InvStatus, Payment, Plan,
                     Product, ProductVariant, QuotationTemplate, Role, SubStatus, Subscription,
                     Tax, TemplateLine, User)
from .routers.payments import record_payment
from .routers.subscriptions import _new_sub
from .schemas import SubLineIn, SubscriptionIn
from .security import hash_password
from .services.billing import run_billing


def main(n: int):
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    random.seed(7)
    with SessionLocal() as db:
        pw = hash_password("Admin@12345")
        admin = User(email="admin@brightloop.com", name="Alice Admin", hashed_password=pw, role=Role.admin)
        staff = User(email="staff@brightloop.com", name="Sam Staff", hashed_password=pw, role=Role.staff)
        demo = User(email="customer@shop.com", name="Demo Shop", hashed_password=pw, role=Role.customer)
        db.add_all([admin, staff, demo]); db.flush()

        gst = Tax(name="GST 18%", rate_percent=Decimal("18")); db.add(gst)
        erp = Product(name="Brightloop ERP", type="software", sales_price=Decimal("1500"), cost_price=Decimal("600"),
                      notes="Core ERP suite",
                      variants=[ProductVariant(attribute="Brand", value="Odoo", extra_price=Decimal("560")),
                                ProductVariant(attribute="Brand", value="Custom", extra_price=Decimal("0"))])
        crm = Product(name="CRM Add-on", type="add-on", sales_price=Decimal("400"), cost_price=Decimal("120"))
        sup = Product(name="Priority Support", type="service", sales_price=Decimal("250"), cost_price=Decimal("90"))
        db.add_all([erp, crm, sup]); db.flush()
        monthly = Plan(name="Business Monthly", price=Decimal("0"), billing_period=BillingPeriod.monthly,
                       min_quantity=1, pausable=True, closable=True, renewable=True)
        yearly = Plan(name="Enterprise Yearly", price=Decimal("0"), billing_period=BillingPeriod.yearly,
                      min_quantity=1, closable=True, renewable=True, auto_close=True)
        weekly = Plan(name="Flex Weekly", price=Decimal("0"), billing_period=BillingPeriod.weekly,
                      pausable=True)
        db.add_all([monthly, yearly, weekly]); db.flush()
        db.add(Discount(name="Volume 10% (10+ seats)", kind=DiscountKind.percent, value=Decimal("10"),
                        min_quantity=10, product_id=erp.id))
        db.add(Discount(name="Launch Rs.100 off", kind=DiscountKind.fixed, value=Decimal("100"),
                        end_date=date.today() + timedelta(days=60), usage_limit=50))
        t = QuotationTemplate(name="Starter bundle", validity_days=30, plan_id=monthly.id,
                              lines=[TemplateLine(product_id=erp.id, quantity=1), TemplateLine(product_id=sup.id, quantity=1)])
        db.add(t); db.flush()

        customers = [demo]
        for i in range(max(20, n // 10)):
            c = User(email=f"client{i}@example.com", name=f"Client {i}", hashed_password=pw, role=Role.customer)
            customers.append(c)
        db.add_all(customers[1:]); db.flush()

        plans = [monthly, yearly, weekly]
        for i in range(n):
            plan = random.choices(plans, weights=[6, 3, 1])[0]
            start = date.today() - timedelta(days=random.randint(0, 400))
            lines = [SubLineIn(product_id=erp.id, variant_id=random.choice(erp.variants).id,
                               quantity=random.choice([1, 2, 5, 10, 12]), tax_id=gst.id)]
            if random.random() < .5:
                lines.append(SubLineIn(product_id=crm.id, quantity=random.randint(1, 4), tax_id=gst.id))
            sub = _new_sub(db, SubscriptionIn(customer_id=random.choice(customers).id, plan_id=plan.id,
                                              start_date=start, lines=lines))
            roll = random.random()
            if roll < .85:
                sub.status, sub.next_billing_date = SubStatus.active, sub.start_date
            elif roll < .92:
                sub.status = SubStatus.quotation
            else:
                sub.status = SubStatus.closed
            if i % 500 == 0:
                db.flush()
        db.commit()

        print("billing:", run_billing(db, auto_confirm=True))
        # ~70% of invoices get paid (some partial) so the dashboard has overdue + outstanding mix
        for inv in db.scalars(select(Invoice).where(Invoice.status == InvStatus.confirmed)).unique().all():
            r = random.random()
            if r < .6:
                record_payment(db, inv, random.choice(["card", "bank_transfer", "upi"]), inv.total,
                               inv.invoice_date + timedelta(days=random.randint(0, 10)), "Seed payment")
            elif r < .7:
                record_payment(db, inv, "bank_transfer", (inv.total / 2).quantize(Decimal("0.01")), None, "Partial")
        db.commit()
    print(f"Seeded {n} subscriptions. Logins (password Admin@12345): admin@brightloop.com, "
          f"staff@brightloop.com, customer@shop.com")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(); ap.add_argument("--n", type=int, default=2000)
    main(ap.parse_args().n)
