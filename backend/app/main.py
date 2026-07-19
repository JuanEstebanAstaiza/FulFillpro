from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text

from backend.app.config import get_settings
from backend.app.core.security import hash_password
from backend.app.database import Base, SessionLocal, engine
from backend.app.models import *  # noqa: F401,F403
from backend.app.models.license import License
from backend.app.models.user import User
from backend.app.routers import admin, analytics, auth, company, health, legal, licenses, orders, process
from backend.app.services import legal_service, license_service, storage_service

settings = get_settings()

app = FastAPI(
    title="FulfillPro API",
    version="2.1.0",
    docs_url="/api/docs" if not settings.is_production else None,
    redoc_url=None,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(health.router)
app.include_router(auth.router)
app.include_router(licenses.router)
app.include_router(orders.router)
app.include_router(process.router)
app.include_router(admin.router)
app.include_router(legal.router)
app.include_router(company.router)
app.include_router(analytics.router)


def _safe_alter(sql: str) -> None:
    try:
        with engine.begin() as conn:
            conn.execute(text(sql))
    except Exception:
        pass


def ensure_schema() -> None:
    Base.metadata.create_all(bind=engine)
    # Columnas nuevas sin migraciones formales
    _safe_alter("ALTER TABLE users ADD COLUMN IF NOT EXISTS must_accept_terms BOOLEAN DEFAULT TRUE")
    _safe_alter("ALTER TABLE users ADD COLUMN IF NOT EXISTS terms_accepted_at TIMESTAMP")
    _safe_alter("ALTER TABLE users ADD COLUMN IF NOT EXISTS created_by_id VARCHAR(36)")
    _safe_alter("ALTER TABLE licenses ADD COLUMN IF NOT EXISTS analytics_enabled BOOLEAN DEFAULT TRUE")
    _safe_alter(
        "ALTER TABLE licenses ADD COLUMN IF NOT EXISTS analytics_weeks_retention INTEGER DEFAULT 12"
    )
    _safe_alter(
        "ALTER TABLE licenses ADD COLUMN IF NOT EXISTS analytics_max_events_per_week INTEGER DEFAULT 50000"
    )
    _safe_alter(
        "ALTER TABLE licenses ADD COLUMN IF NOT EXISTS analytics_storage_mb INTEGER DEFAULT 200"
    )
    # Quitar FK de source_order_id si existía (analítica no depende del ciclo de vida de orders)
    _safe_alter(
        "ALTER TABLE analytics_sale_events DROP CONSTRAINT IF EXISTS analytics_sale_events_source_order_id_fkey"
    )


def seed_database() -> None:
    ensure_schema()
    storage_service.ensure_storage_root()
    with SessionLocal() as db:
        legal_service.seed_legal_documents(db)

        admin = db.query(User).filter(User.email == settings.admin_email.lower()).first()
        if not admin:
            admin = User(
                email=settings.admin_email.lower(),
                password_hash=hash_password(settings.admin_password),
                full_name=settings.admin_name,
                role="admin",
                client_code="FULFILLPRO",
                company_name="FulfillPro",
                is_active=True,
                must_accept_terms=False,
                terms_accepted_at=None,
            )
            db.add(admin)
            db.commit()
        else:
            admin.role = "admin"
            admin.must_accept_terms = False
            db.commit()

        admin = db.query(User).filter(User.email == settings.admin_email.lower()).first()

        # Empresa demo + company_admin demo (no el platform admin)
        demo_admin = db.query(User).filter(User.email == "empresa@demo.com").first()
        if not demo_admin:
            demo_admin = User(
                email="empresa@demo.com",
                password_hash=hash_password("DemoEmpresa2026!"),
                full_name="Admin Empresa Demo",
                role="company_admin",
                client_code="DEMO",
                company_name="Demo interno",
                is_active=True,
                must_accept_terms=True,
            )
            db.add(demo_admin)
            db.commit()
            db.refresh(demo_admin)

        if db.query(License).filter(License.code == "DEMO-TRIAL").count() == 0:
            license_service.create_license(
                db,
                {
                    "code": "DEMO-TRIAL",
                    "template": "trial",
                    "label": "Demo prueba gratuita",
                    "company_name": "Demo interno",
                    "notes": "Licencia demo.",
                    "owner_user_id": demo_admin.id if demo_admin else None,
                },
            )

        if db.query(License).filter(License.code == "DEMO-001").count() == 0:
            license_service.create_license(
                db,
                {
                    "code": "DEMO-001",
                    "type": "standard",
                    "label": "Demo estándar",
                    "company_name": "Demo interno",
                    "max_devices": 99,
                    "limit_uses": 200,
                    "daily_limit": 20,
                    "expiry": date.today() + timedelta(days=365),
                    "owner_user_id": demo_admin.id if demo_admin else None,
                },
            )

        # Siempre reasignar demos a la empresa demo (no al platform admin)
        if demo_admin:
            for code in ("DEMO-TRIAL", "DEMO-001"):
                lic = db.query(License).filter(License.code == code).first()
                if lic:
                    lic.owner_user_id = demo_admin.id
                    lic.company_name = lic.company_name or "Demo interno"
                    lic.active = True
            demo_admin.client_code = "DEMO"
            demo_admin.company_name = demo_admin.company_name or "Demo interno"
            demo_admin.role = "company_admin"
            db.commit()


@app.on_event("startup")
def on_startup() -> None:
    seed_database()


FRONTEND_DIR = Path(__file__).resolve().parents[2] / "frontend"
if not FRONTEND_DIR.exists():
    FRONTEND_DIR = Path("frontend")

if FRONTEND_DIR.exists():
    app.mount("/assets", StaticFiles(directory=str(FRONTEND_DIR / "assets")), name="assets")
    app.mount("/css", StaticFiles(directory=str(FRONTEND_DIR / "css")), name="css")
    app.mount("/js", StaticFiles(directory=str(FRONTEND_DIR / "js")), name="js")

    @app.get("/")
    def index():
        return FileResponse(FRONTEND_DIR / "index.html")

    @app.get("/ops")
    def ops_login():
        """Ruta oculta: acceso administrador de plataforma FulfillPro."""
        return FileResponse(FRONTEND_DIR / "ops.html")

    @app.get("/ops/panel")
    def ops_panel():
        return FileResponse(FRONTEND_DIR / "admin.html")

    @app.get("/admin")
    def admin_redirect():
        # No exponer panel en /admin público
        return RedirectResponse(url="/ops", status_code=302)

else:

    @app.get("/")
    def root():
        return {"status": "FulfillPro API v2 activa", "docs": "/api/docs"}
