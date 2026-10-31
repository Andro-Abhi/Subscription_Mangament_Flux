from contextlib import asynccontextmanager

from apscheduler.schedulers.background import BackgroundScheduler
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .config import settings
from .database import Base, SessionLocal, engine
from .routers import auth, catalog, invoices, payments, reports, subscriptions
from .services.billing import run_billing


def nightly_billing():
    with SessionLocal() as db:
        print("[scheduler] billing:", run_billing(db))


@asynccontextmanager
async def lifespan(app: FastAPI):
    Base.metadata.create_all(engine)       # use Alembic migrations if you have time
    scheduler = BackgroundScheduler()
    scheduler.add_job(nightly_billing, "cron", hour=2, minute=0, id="billing", replace_existing=True)
    scheduler.start()
    yield
    scheduler.shutdown(wait=False)


app = FastAPI(title="Brightloop Subscription Management", version="1.0.0", lifespan=lifespan,
              description="Plans, subscriptions, recurring billing, payments and revenue reporting.")
app.add_middleware(CORSMiddleware, allow_origins=[settings.FRONTEND_URL, "http://localhost:3000"],
                   allow_credentials=True, allow_methods=["*"], allow_headers=["*"])

for r in (auth.router, catalog.router, subscriptions.router, invoices.router, payments.router, reports.router):
    app.include_router(r)


@app.get("/health", tags=["meta"])
def health():
    return {"status": "ok"}
