from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


class CreateOrderRequest(BaseModel):
    customer_id: str = Field(min_length=1, max_length=100)
    amount_cents: int = Field(gt=0, le=100_000_000)


class OutcomeRequest(BaseModel):
    reason: str | None = Field(default=None, max_length=500)


class OrderView(BaseModel):
    order_id: str
    customer_id: str
    amount_cents: int
    lifecycle_status: str
    active_state: str
    machine_version: int
    definition_fingerprint: str
    aggregate_digest: str
    duplicate: bool = False
    migration_applied: bool = False


class OutboxView(BaseModel):
    effect_id: str
    order_id: str
    sequence: int
    event_name: str
    correlation_id: str
    payload: dict[str, Any]
    delivery_status: Literal["pending", "delivered"]
    delivery_attempts: int


class DeliverySummary(BaseModel):
    attempted: int
    newly_delivered: int


class DebugView(BaseModel):
    order: OrderView
    inbox: list[dict[str, Any]]
    outbox: list[OutboxView]
    migration_audits: list[dict[str, Any]]
