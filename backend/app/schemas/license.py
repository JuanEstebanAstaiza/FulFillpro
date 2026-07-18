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
    active: Optional[bool] = None
    count_toward_global: Optional[bool] = None
    enforce_daily_limit: Optional[bool] = None
    features: Optional[dict[str, Any]] = None
    notes: Optional[str] = None


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
