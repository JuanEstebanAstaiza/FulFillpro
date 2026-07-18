from __future__ import annotations

from datetime import datetime
from typing import Any, Optional
from uuid import UUID

from pydantic import BaseModel, Field


class OrderFileOut(BaseModel):
    id: UUID
    kind: str
    filename: str
    size_bytes: int
    created_at: Optional[datetime] = None

    model_config = {"from_attributes": True}


class OrderOut(BaseModel):
    id: UUID
    status: str
    client_code: str
    original_filename: str
    row_count: int
    priority_count: int
    total_risk: float
    meta: dict[str, Any] = Field(default_factory=dict)
    error_message: str = ""
    counted_toward_quota: bool = True
    device_id: str = ""
    created_at: Optional[datetime] = None
    processed_at: Optional[datetime] = None
    files: list[OrderFileOut] = Field(default_factory=list)

    model_config = {"from_attributes": True}


class OrderListResponse(BaseModel):
    items: list[OrderOut]
    total: int
    page: int
    page_size: int
