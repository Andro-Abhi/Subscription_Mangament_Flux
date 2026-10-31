from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, EmailStr, Field

from .models import BillingPeriod, DiscountKind, InvStatus, Role, SubStatus


class ORM(BaseModel):
    model_config = ConfigDict(from_attributes=True)


# ---- auth / users
class RegisterIn(BaseModel):
    email: EmailStr
    password: str
    name: str = ""

class StaffIn(RegisterIn):
    pass

class LoginIn(BaseModel):
    email: EmailStr
    password: str

class ForgotIn(BaseModel):
    email: EmailStr

class ResetIn(BaseModel):
    token: str
    new_password: str

class UserOut(ORM):
    id: int
    email: str
    name: str
    role: Role

class TokenOut(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: UserOut


# ---- catalog
class VariantIn(BaseModel):
    attribute: str
    value: str
    extra_price: Decimal = Decimal("0")

class VariantOut(VariantIn, ORM):
    id: int

class ProductIn(BaseModel):
    name: str
    type: str = "software"
    sales_price: Decimal = Field(ge=0)
    cost_price: Decimal = Field(default=Decimal("0"), ge=0)
    notes: str = ""
    variants: list[VariantIn] = []

class ProductOut(ORM):
    id: int
    name: str
    type: str
    sales_price: Decimal
    cost_price: Decimal
    notes: str
    variants: list[VariantOut]

class PlanIn(BaseModel):
    name: str
    price: Decimal = Field(ge=0)
    billing_period: BillingPeriod
    min_quantity: int = Field(default=1, ge=1)
    start_date: date | None = None
    end_date: date | None = None
    auto_close: bool = False
    closable: bool = True
    pausable: bool = False
    renewable: bool = True

class PlanOut(PlanIn, ORM):
    id: int

class TaxIn(BaseModel):
    name: str
    rate_percent: Decimal = Field(ge=0, le=100)
    active: bool = True

class TaxOut(TaxIn, ORM):
    id: int

class DiscountIn(BaseModel):
    name: str
    kind: DiscountKind
    value: Decimal = Field(gt=0)
    min_purchase: Decimal = Decimal("0")
    min_quantity: int = 0
    start_date: date | None = None
    end_date: date | None = None
    usage_limit: int | None = None
    product_id: int | None = None

class DiscountOut(DiscountIn, ORM):
    id: int
    used_count: int

class TemplateLineIn(BaseModel):
    product_id: int
    variant_id: int | None = None
    quantity: int = 1

class TemplateIn(BaseModel):
    name: str
    validity_days: int = 30
    plan_id: int
    lines: list[TemplateLineIn]

class TemplateOut(ORM):
    id: int
    name: str
    validity_days: int
    plan_id: int
    lines: list[TemplateLineIn]


# ---- subscriptions
class SubLineIn(BaseModel):
    product_id: int
    variant_id: int | None = None
    quantity: int = Field(default=1, ge=1)
    tax_id: int | None = None

class SubscriptionIn(BaseModel):
    customer_id: int
    plan_id: int
    start_date: date
    expiry_date: date | None = None
    payment_terms: str = "Net 15"
    payment_terms_days: int = 15
    discount_id: int | None = None
    lines: list[SubLineIn]

class FromTemplateIn(BaseModel):
    customer_id: int
    start_date: date
    payment_terms: str = "Net 15"
    payment_terms_days: int = 15

class SubLineOut(ORM):
    id: int
    product_id: int
    variant_id: int | None
    description: str
    quantity: int
    unit_price: Decimal
    tax_id: int | None

class SubscriptionOut(ORM):
    id: int
    number: str
    customer_id: int
    plan_id: int
    start_date: date
    expiry_date: date | None
    quote_valid_until: date | None
    payment_terms: str
    status: SubStatus
    next_billing_date: date | None
    discount_id: int | None
    lines: list[SubLineOut]

class StatusIn(BaseModel):
    status: SubStatus


# ---- invoices / payments
class InvLineOut(ORM):
    description: str
    quantity: int
    unit_price: Decimal
    discount: Decimal
    tax_rate: Decimal
    tax_amount: Decimal
    amount: Decimal

class InvoiceOut(ORM):
    id: int
    number: str
    subscription_id: int
    customer_id: int
    period_start: date
    period_end: date
    invoice_date: date
    due_date: date
    status: InvStatus
    subtotal: Decimal
    discount_total: Decimal
    tax_total: Decimal
    total: Decimal
    amount_paid: Decimal
    lines: list[InvLineOut]

class PaymentIn(BaseModel):
    invoice_id: int
    method: str
    amount: Decimal = Field(gt=0)
    paid_on: date | None = None
    note: str = ""

class PaymentOut(PaymentIn, ORM):
    id: int
    paid_on: date

class Page(BaseModel):
    total: int
    page: int
    size: int
    items: list
