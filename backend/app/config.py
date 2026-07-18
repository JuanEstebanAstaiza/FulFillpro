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

    storage_root: str = "./storage"
    cors_origins: str = "*"

    max_rows: int = 60000
    max_cant_cols: int = 60
    device_stale_days: int = 60

    rate_limit_login: int = 10
    rate_limit_process: int = 30

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
