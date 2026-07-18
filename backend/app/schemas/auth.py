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


class RefreshRequest(BaseModel):
    refresh_token: str


class UserCreate(BaseModel):
    email: str = Field(min_length=3)
    password: str = Field(min_length=8)
    full_name: str = ""
    role: str = "client"
    client_code: str = ""
    company_name: str = ""


class UserOut(BaseModel):
    id: UUID
    email: str
    full_name: str
    role: str
    client_code: str
    company_name: str
    is_active: bool
    created_at: Optional[datetime] = None
    last_login: Optional[datetime] = None

    model_config = {"from_attributes": True}
