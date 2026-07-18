from __future__ import annotations

from datetime import datetime
from typing import Optional
from uuid import UUID

from pydantic import BaseModel, Field


class LoginRequest(BaseModel):
    email: str = Field(min_length=3)
    password: str


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    needs_consent: bool = False
    role: str = ""


class RefreshRequest(BaseModel):
    refresh_token: str


class UserCreate(BaseModel):
    email: str = Field(min_length=3)
    password: str = Field(min_length=8)
    full_name: str = ""
    role: str = "employee"
    client_code: str = ""
    company_name: str = ""


class RegisterRequest(BaseModel):
    """Registro del administrador de empresa (compra / onboarding) con código de licencia."""

    email: str = Field(min_length=3)
    password: str = Field(min_length=8)
    full_name: str = ""
    license_code: str = Field(min_length=3, description="Código de licencia de la empresa")
    as_company_admin: bool = True


class UserOut(BaseModel):
    id: UUID
    email: str
    full_name: str
    role: str
    client_code: str
    company_name: str
    is_active: bool
    must_accept_terms: bool = True
    terms_accepted_at: Optional[datetime] = None
    needs_consent: bool = False
    created_at: Optional[datetime] = None
    last_login: Optional[datetime] = None

    model_config = {"from_attributes": True}
