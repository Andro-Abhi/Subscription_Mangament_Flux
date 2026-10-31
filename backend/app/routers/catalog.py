"""Products, variants, plans, taxes, discounts, quotation templates.
Writes are ADMIN-only (templates: staff too). Reads: any logged-in user."""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..database import get_db
from ..deps import admin_only, audit, current_user, staff_or_admin
from ..models import (Discount, Plan, Product, ProductVariant, QuotationTemplate, Tax,
                      TemplateLine, User)
from ..schemas import (DiscountIn, DiscountOut, PlanIn, PlanOut, ProductIn, ProductOut,
                       TaxIn, TaxOut, TemplateIn, TemplateOut)

router = APIRouter(tags=["catalog"])


def _get(db, model, id_):
    obj = db.get(model, id_)
    if not obj:
        raise HTTPException(404, f"{model.__name__} not found")
    return obj


# ---------- products
@router.post("/products", response_model=ProductOut, status_code=201)
def create_product(data: ProductIn, db: Session = Depends(get_db), admin: User = Depends(admin_only)):
    p = Product(**data.model_dump(exclude={"variants"}))
    p.variants = [ProductVariant(**v.model_dump()) for v in data.variants]
    db.add(p); db.flush()
    audit(db, admin, "create", "product", p.id)
    db.commit()
    return p


@router.get("/products", response_model=list[ProductOut])
def list_products(db: Session = Depends(get_db), _: User = Depends(current_user)):
    return db.scalars(select(Product).order_by(Product.name).limit(500)).all()


@router.put("/products/{pid}", response_model=ProductOut)
def update_product(pid: int, data: ProductIn, db: Session = Depends(get_db), admin: User = Depends(admin_only)):
    p = _get(db, Product, pid)
    for k, v in data.model_dump(exclude={"variants"}).items():
        setattr(p, k, v)
    p.variants = [ProductVariant(**v.model_dump()) for v in data.variants]
    audit(db, admin, "update", "product", p.id)
    db.commit()
    return p


@router.delete("/products/{pid}", status_code=204)
def delete_product(pid: int, db: Session = Depends(get_db), admin: User = Depends(admin_only)):
    db.delete(_get(db, Product, pid)); db.commit()


# ---------- plans
@router.post("/plans", response_model=PlanOut, status_code=201)
def create_plan(data: PlanIn, db: Session = Depends(get_db), admin: User = Depends(admin_only)):
    if data.start_date and data.end_date and data.end_date < data.start_date:
        raise HTTPException(422, "end_date cannot be before start_date")
    p = Plan(**data.model_dump()); db.add(p); db.flush()
    audit(db, admin, "create", "plan", p.id); db.commit()
    return p


@router.get("/plans", response_model=list[PlanOut])
def list_plans(db: Session = Depends(get_db), _: User = Depends(current_user)):
    return db.scalars(select(Plan).limit(500)).all()


@router.put("/plans/{pid}", response_model=PlanOut)
def update_plan(pid: int, data: PlanIn, db: Session = Depends(get_db), admin: User = Depends(admin_only)):
    p = _get(db, Plan, pid)
    for k, v in data.model_dump().items():
        setattr(p, k, v)
    db.commit()
    return p


# ---------- taxes
@router.post("/taxes", response_model=TaxOut, status_code=201)
def create_tax(data: TaxIn, db: Session = Depends(get_db), admin: User = Depends(admin_only)):
    t = Tax(**data.model_dump()); db.add(t); db.commit()
    return t


@router.get("/taxes", response_model=list[TaxOut])
def list_taxes(db: Session = Depends(get_db), _: User = Depends(current_user)):
    return db.scalars(select(Tax)).all()


# ---------- discounts (admin only)
@router.post("/discounts", response_model=DiscountOut, status_code=201)
def create_discount(data: DiscountIn, db: Session = Depends(get_db), admin: User = Depends(admin_only)):
    if data.kind.value == "percent" and data.value > 100:
        raise HTTPException(422, "Percentage cannot exceed 100")
    d = Discount(**data.model_dump()); db.add(d); db.flush()
    audit(db, admin, "create", "discount", d.id); db.commit()
    return d


@router.get("/discounts", response_model=list[DiscountOut])
def list_discounts(db: Session = Depends(get_db), _: User = Depends(staff_or_admin)):
    return db.scalars(select(Discount)).all()


# ---------- quotation templates
@router.post("/templates", response_model=TemplateOut, status_code=201)
def create_template(data: TemplateIn, db: Session = Depends(get_db), user: User = Depends(staff_or_admin)):
    t = QuotationTemplate(name=data.name, validity_days=data.validity_days, plan_id=data.plan_id)
    t.lines = [TemplateLine(**l.model_dump()) for l in data.lines]
    db.add(t); db.commit()
    return t


@router.get("/templates", response_model=list[TemplateOut])
def list_templates(db: Session = Depends(get_db), _: User = Depends(staff_or_admin)):
    return db.scalars(select(QuotationTemplate)).all()
