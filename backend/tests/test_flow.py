from datetime import date, timedelta

from .conftest import login


def register(client, email, pw="Passw0rd!x"):
    return client.post("/auth/register", json={"email": email, "password": pw, "name": "Cust"})


def test_password_rules_and_unique_email(client):
    assert register(client, "a@x.com", "short1!").status_code == 422
    assert register(client, "a@x.com", "alllowercase!").status_code == 422
    assert register(client, "a@x.com").status_code == 201
    assert register(client, "a@x.com").status_code == 409


def test_only_admin_creates_staff_and_discounts(client, admin):
    cust = register(client, "c@x.com").json()
    h = {"Authorization": f"Bearer {cust['access_token']}"}
    assert client.post("/users/staff", headers=h, json={"email": "s@x.com", "password": "Passw0rd!x"}).status_code == 403
    assert client.post("/discounts", headers=h, json={"name": "d", "kind": "fixed", "value": 5}).status_code == 403
    assert client.post("/users/staff", headers=admin, json={"email": "s@x.com", "password": "Passw0rd!x"}).status_code == 201


def _setup(client, admin):
    p = client.post("/products", headers=admin, json={"name": "ERP", "sales_price": 1000, "cost_price": 400,
                    "variants": [{"attribute": "Brand", "value": "Odoo", "extra_price": 560}]}).json()
    plan = client.post("/plans", headers=admin, json={"name": "M", "price": 0, "billing_period": "monthly"}).json()
    tax = client.post("/taxes", headers=admin, json={"name": "GST", "rate_percent": 18}).json()
    cust = register(client, "c@x.com").json()
    return p, plan, tax, cust


def test_billing_is_idempotent_with_variant_price_and_tax(client, admin):
    p, plan, tax, cust = _setup(client, admin)
    sub = client.post("/subscriptions", headers=admin, json={
        "customer_id": cust["user"]["id"], "plan_id": plan["id"], "start_date": str(date.today()),
        "lines": [{"product_id": p["id"], "variant_id": p["variants"][0]["id"], "quantity": 1, "tax_id": tax["id"]}]}).json()
    for s in ("quotation", "confirmed", "active"):
        assert client.post(f"/subscriptions/{sub['id']}/status", headers=admin, json={"status": s}).status_code == 200
    assert client.post(f"/subscriptions/{sub['id']}/status", headers=admin, json={"status": "draft"}).status_code == 409

    r1 = client.post("/billing/run", headers=admin).json()
    r2 = client.post("/billing/run", headers=admin).json()
    assert r1["invoices_created"] == 1 and r2["invoices_created"] == 0     # nothing billed twice

    inv = client.get("/invoices", headers=admin).json()["items"][0]
    assert float(inv["subtotal"]) == 1560.0                                 # 1000 + 560 Odoo
    assert float(inv["tax_total"]) == 280.8 and float(inv["total"]) == 1840.8


def test_discount_auto_applies_and_payment_marks_paid(client, admin):
    p, plan, tax, cust = _setup(client, admin)
    client.post("/discounts", headers=admin, json={"name": "10%", "kind": "percent", "value": 10, "product_id": p["id"]})
    sub = client.post("/subscriptions", headers=admin, json={
        "customer_id": cust["user"]["id"], "plan_id": plan["id"], "start_date": str(date.today()),
        "lines": [{"product_id": p["id"], "quantity": 1}]}).json()
    for s in ("quotation", "confirmed", "active"):
        client.post(f"/subscriptions/{sub['id']}/status", headers=admin, json={"status": s})
    client.post("/billing/run?auto_confirm=true", headers=admin)
    inv = client.get("/invoices", headers=admin).json()["items"][0]
    assert float(inv["discount_total"]) == 100.0 and float(inv["total"]) == 900.0

    pay = client.post("/payments", headers=admin, json={"invoice_id": inv["id"], "method": "card", "amount": 900})
    assert pay.status_code == 201
    assert client.get(f"/invoices/{inv['id']}", headers=admin).json()["status"] == "paid"


def test_customer_sees_only_own_data(client, admin):
    p, plan, tax, cust = _setup(client, admin)
    other = register(client, "o@x.com").json()
    client.post("/subscriptions", headers=admin, json={
        "customer_id": cust["user"]["id"], "plan_id": plan["id"], "start_date": str(date.today()),
        "lines": [{"product_id": p["id"], "quantity": 1}]})
    h_other = {"Authorization": f"Bearer {other['access_token']}"}
    assert client.get("/subscriptions", headers=h_other).json()["total"] == 0
    assert client.get("/subscriptions/1", headers=h_other).status_code == 404
