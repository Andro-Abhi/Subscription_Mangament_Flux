# Brightloop Subscription Management System (Backend)

FastAPI + SQLAlchemy 2 + PostgreSQL (SQLite for quick dev). Covers every scene in the brief.

## Run (local)
```bash
cd backend
python -m venv .venv && source .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env
python -m app.seed --n 2000        # demo data (admin@brightloop.com / Admin@12345)
uvicorn app.main:app --reload      # API docs: http://localhost:8000/docs
pytest -q                          # tests
```
## Run (Docker + Postgres)
```bash
docker compose up --build
docker compose exec api python -m app.seed --n 5000
```

## Demo logins (password `Admin@12345`)
`admin@brightloop.com` · `staff@brightloop.com` · `customer@shop.com`

## Brief -> Implementation map
| Scene | Where |
|---|---|
| Sign up, unique email, password rules, reset link | `routers/auth.py`, `security.py` |
| Admin / Staff / Customer roles, only admin adds staff | `deps.py`, `POST /users/staff` |
| Products, variants (+560 for Odoo), plans, taxes, discounts | `routers/catalog.py` |
| Quotation templates -> subscription, status flow | `routers/subscriptions.py` |
| Billing day (no duplicates / skips) | `services/billing.py`, `POST /billing/run`, nightly cron |
| Discounts auto-applied, expire, usage limit | `services/billing.py` |
| Invoice confirm/cancel/send/print | `routers/invoices.py` |
| Payments, paid vs outstanding | `routers/payments.py` |
| Finance dashboard (MRR/ARR, overdue, filters) | `routers/reports.py` |

## Design decisions (say these to the judges)
- **Idempotent billing**: `next_billing_date` cursor + DB unique `(subscription_id, period_start)`.
- **Money is `Decimal`/NUMERIC**, never float.
- **Server-side state machines** for subscriptions and invoices (invalid transitions -> 409).
- **Auth**: bcrypt, JWT, hashed single-use reset tokens, no account enumeration on forgot-password.
- **Portal isolation**: customers get 404 for other people's records.
- **Performance**: indexes on status/customer/due_date/next_billing_date, pagination everywhere,
  dashboard = a few aggregate queries.
- **Audit log** table records who did what.

## Next steps (frontend / extras)
React + Vite pages: Login, Dashboard, Products/Plans, Subscriptions, Invoices, Payments, Customer Portal.
