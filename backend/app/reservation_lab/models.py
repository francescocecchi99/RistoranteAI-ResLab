from __future__ import annotations

from datetime import date, time
from typing import Any, Literal

from pydantic import BaseModel, Field


class Preferences(BaseModel):
    preferred_area: str | None = None
    special_requests: str | None = None


class BookingRequest(BaseModel):
    restaurant_id: str
    date: date
    time: time
    customer_name: str
    phone_number: str | None = None
    party_size: int = Field(gt=0)
    high_chairs_requested: int = Field(default=0, ge=0)
    preferences: Preferences = Field(default_factory=Preferences)
    original_agent_text: str | None = None


class CancelReservationRequest(BaseModel):
    reservation_id: str


class AgentResponse(BaseModel):
    available: bool
    area: str | None
    head_seats_used: int | None


class DBReservationPayload(BaseModel):
    reservation_id: str | None = None
    restaurant_id: str
    service_date: date
    requested_time: time
    meal_period: str
    turn_index: int
    customer_name: str
    phone_number: str | None
    party_size: int
    assigned_tables: list[str]
    assigned_merge_id: str | None
    head_seats_used: int
    preferences: dict[str, Any]
    status: Literal["pending", "confirmed", "cancelled", "rejected"] = "pending"


class AvailabilityResponse(BaseModel):
    agent_response: AgentResponse
    db_preview: DBReservationPayload | None = None
    reason: str | None = None
    debug: dict[str, Any] | None = None


class CreateReservationResponse(BaseModel):
    agent_response: AgentResponse
    db_saved: DBReservationPayload | None = None
    reason: str | None = None
    debug: dict[str, Any] | None = None

