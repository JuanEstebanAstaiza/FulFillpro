from functools import lru_cache
from typing import List

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_env: str = "development"
    database_url: str = "postgresql+psycopg://fulfillpro:fulfillpro@localhost:5432/fulfillpro"
    redis_url: str = "redis://localhost:6379/0"

    jwt_secret: str = "dev-secret-change-in-production"
    jwt_algorithm: str = "HS256"
    jwt_expire_minutes: int = 60
    jwt_refresh_expire_days: int = 14

    admin_email: str = "admin@fulfillpro.com"
    admin_password: str = "AdminFulfillPro2026!"
    admin_name: str = "Administrador"

    # Si false: no se permite registro sin código de licencia.
    # El alta CON código de licencia válido (onboarding de clientes) siempre está permitido.
    allow_public_register: bool = True
    seed_demo_users: bool = True

    storage_root: str = "./storage"
    storage_max_gb: int = 100  # política de capacidad de disco del despliegue
    cors_origins: str = "*"

    max_rows: int = 60000
    max_cant_cols: int = 60
    device_stale_days: int = 60

    rate_limit_login: int = 10
    # Encolado process: techos por usuario e IP (no es ejecución de Excel)
    # Encolar es barato; el techo real de RAM lo pone WORKER_CONCURRENCY
    rate_limit_process: int = 150  # por usuario / minuto (ráfagas de encolado)
    rate_limit_process_ip: int = 500  # por IP / minuto (100+ perfiles detrás de NAT)

    # Capacidad / pool (perfil 12 GB stack)
    uvicorn_workers: int = 4
    db_pool_size: int = 12
    db_max_overflow: int = 8
    process_max_queue: int = 500
    worker_concurrency: int = 4
    redis_max_connections: int = 100
    max_upload_mb: int = 25  # techo por archivo (estabilidad API bajo 100 subidas concurrentes)



    @property
    def cors_origin_list(self) -> List[str]:
        if self.cors_origins.strip() == "*":
            return ["*"]
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def is_production(self) -> bool:
        return self.app_env.lower() in {"production", "prod"}


@lru_cache
def get_settings() -> Settings:
    return Settings()
