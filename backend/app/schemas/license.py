from __future__ import annotations

from datetime import date, datetime
from typing import Any, Optional
from uuid import UUID

from pydantic import BaseModel, Field


class LicenseCreate(BaseModel):
    code: Optional[str] = None  # si vacío, se genera
    label: str = ""
    type: str = "standard"  # trial | standard | pro | enterprise | custom
    company_name: str = ""
    owner_user_id: Optional[UUID] = None
    max_devices: int = 3
    limit_uses: int = 0  # 0 = ilimitado
    daily_limit: int = 0  # 0 = sin límite diario
    duration_days: Optional[int] = None  # genera expiry desde hoy
    expiry: Optional[date] = None
    count_toward_global: bool = True
    enforce_daily_limit: bool = True
    features: dict[str, Any] = Field(default_factory=dict)
    notes: str = ""
    # Plantillas rápidas
    template: Optional[str] = None  # trial | standard | pro | enterprise


class LicenseUpdate(BaseModel):
    label: Optional[str] = None
    type: Optional[str] = None
    company_name: Optional[str] = None
    owner_user_id: Optional[UUID] = None
    max_devices: Optional[int] = None
    limit_uses: Optional[int] = None
    daily_limit: Optional[int] = None
    expiry: Optional[date] = None
    # Extiende vigencia sin recrear licencia (días desde max(hoy, expiry actual))
    extend_days: Optional[int] = None
    # Si se envía, calcula expiry según expiry_policy
    duration_days: Optional[int] = None
    # replace_from_today | extend | set_absolute | keep
    expiry_policy: Optional[str] = None
    active: Optional[bool] = None
    count_toward_global: Optional[bool] = None
    enforce_daily_limit: Optional[bool] = None
    features: Optional[dict[str, Any]] = None
    notes: Optional[str] = None
    append_note: Optional[str] = None
    reset_uses: Optional[bool] = None
    # Aplica cupos/tipo/analytics de plantilla (no cambia código ni dueño)
    apply_template: Optional[str] = None


class LicenseChangePlan(BaseModel):
    """
    Cambio de plan en caliente (misma licencia / mismo código).
    Ej.: mensual (30d) → anual (365d) sin desactivar ni crear otra.
    """

    template: Optional[str] = None  # trial | standard | pro | enterprise
    type: Optional[str] = None
    label: Optional[str] = None
    company_name: Optional[str] = None
    max_devices: Optional[int] = None
    limit_uses: Optional[int] = None
    daily_limit: Optional[int] = None
    # Tiempo de vigencia
    duration_days: Optional[int] = None
    extend_days: Optional[int] = None
    expiry: Optional[date] = None
    # replace_from_today (default si duration_days) | extend | set_absolute | keep
    expiry_policy: str = "extend"
    count_toward_global: Optional[bool] = None
    enforce_daily_limit: Optional[bool] = None
    active: Optional[bool] = True
    notes: Optional[str] = None
    append_note: Optional[str] = None
    reset_uses: bool = False
    # Si true y hay template, sobrescribe límites del template (salvo overrides explícitos)
    apply_template_quotas: bool = True


class DeviceOut(BaseModel):
    id: UUID
    device_id: str
    device_name: str
    device_fingerprint: str
    first_seen: Optional[datetime] = None
    last_seen: Optional[datetime] = None
    is_active: bool

    model_config = {"from_attributes": True}


class LicenseOut(BaseModel):
    id: UUID
    code: str
    label: str
    type: str
    company_name: str
    owner_user_id: Optional[UUID] = None
    max_devices: int
    limit_uses: int
    uses: int
    daily_limit: int
    uses_today: int = 0
    expiry: Optional[date] = None
    days_left: Optional[int] = None
    active: bool
    count_toward_global: bool
    enforce_daily_limit: bool
    features: dict[str, Any] = Field(default_factory=dict)
    notes: str = ""
    devices_count: int = 0
    devices: list[DeviceOut] = Field(default_factory=list)
    created_at: Optional[datetime] = None
    last_access: Optional[datetime] = None
    assigned_at: Optional[datetime] = None

    model_config = {"from_attributes": True}


class ActivateRequest(BaseModel):
    code: str
    device_id: str = Field(..., description="Identificador del equipo/PC (asignable)")
    device_name: str = ""
    device_fingerprint: str = ""
    device_soft: str = ""


class ValidateDeviceRequest(BaseModel):
    code: str
    device_id: str
    device_fingerprint: str = ""
    device_soft: str = ""


class LicenseTemplateCreate(BaseModel):
    slug: Optional[str] = None
    name: str
    description: str = ""
    license_type: str = "standard"
    label_default: str = ""
    max_devices: int = 5
    limit_uses: int = 0
    daily_limit: int = 0
    duration_days: int = 30
    count_toward_global: bool = True
    enforce_daily_limit: bool = True
    analytics_enabled: bool = True
    analytics_weeks_retention: int = 12
    analytics_max_events_per_week: int = 50000
    analytics_storage_mb: int = 200
    features: dict[str, Any] = Field(default_factory=dict)
    is_active: bool = True
    sort_order: int = 100


class LicenseTemplateUpdate(BaseModel):
    slug: Optional[str] = None
    name: Optional[str] = None
    description: Optional[str] = None
    license_type: Optional[str] = None
    label_default: Optional[str] = None
    max_devices: Optional[int] = None
    limit_uses: Optional[int] = None
    daily_limit: Optional[int] = None
    duration_days: Optional[int] = None
    count_toward_global: Optional[bool] = None
    enforce_daily_limit: Optional[bool] = None
    analytics_enabled: Optional[bool] = None
    analytics_weeks_retention: Optional[int] = None
    analytics_max_events_per_week: Optional[int] = None
    analytics_storage_mb: Optional[int] = None
    features: Optional[dict[str, Any]] = None
    is_active: Optional[bool] = None
    sort_order: Optional[int] = None
