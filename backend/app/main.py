from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from backend.app.config import get_settings
from backend.app.core.security import hash_password
from backend.app.database import Base, SessionLocal, engine
from backend.app.models import *  # noqa: F401,F403 — register models
from backend.app.models.license import License
from backend.app.models.user import User
from backend.app.routers import admin, auth, health, licenses, orders, process
from backend.app.services import license_service, storage_service

settings = get_settings()

app = FastAPI(
    title="FulfillPro API",
    version="2.0.0",
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


def seed_database() -> None:
    Base.metadata.create_all(bind=engine)
    storage_service.ensure_storage_root()
    with SessionLocal() as db:
        admin = db.query(User).filter(User.email == settings.admin_email.lower()).first()
        if not admin:
            admin = User(
                email=settings.admin_email.lower(),
                password_hash=hash_password(settings.admin_password),
                full_name=settings.admin_name,
                role="admin",
                client_code="ADMIN",
                company_name="FulfillPro",
                is_active=True,
            )
            db.add(admin)
            db.commit()

        if db.query(License).filter(License.code == "DEMO-TRIAL").count() == 0:
            license_service.create_license(
                db,
                {
                    "code": "DEMO-TRIAL",
                    "template": "trial",
                    "label": "Demo prueba gratuita",
                    "company_name": "Demo interno",
                    "notes": "Licencia de demostración: 50 órdenes, 3/día, 7 días, 3 equipos.",
                },
            )

        if db.query(License).filter(License.code == "DEMO-001").count() == 0:
            license_service.create_license(
                db,
                {
                    "code": "DEMO-001",
                    "type": "standard",
                    "label": "Demo estándar",
                    "max_devices": 5,
                    "limit_uses": 200,
                    "daily_limit": 20,
                    "expiry": date.today() + timedelta(days=365),
                },
            )


@app.on_event("startup")
def on_startup() -> None:
    seed_database()


# Frontend estático
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

    @app.get("/admin")
    def admin_page():
        return FileResponse(FRONTEND_DIR / "admin.html")
else:

    @app.get("/")
    def root():
        return {"status": "FulfillPro API v2 activa", "docs": "/api/docs"}
