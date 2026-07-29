from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from typing import Annotated, Any, Literal

from fastapi import FastAPI, Header, HTTPException, status

from .database import Database
from .definitions import DefinitionRegistry
from .schemas import (
    CreateOrderRequest,
    DebugView,
    DeliverySummary,
    OrderView,
    OutboxView,
    OutcomeRequest,
)
from .service import (
    CommandRejectedError,
    CommandResult,
    DuplicateConflictError,
    OrderNotFoundError,
    OrderService,
)
from .settings import Settings

EventName = Literal[
    "payment_accepted",
    "payment_rejected",
    "fulfillment_started",
    "fulfillment_succeeded",
    "fulfillment_failed",
    "fulfillment_cancelled",
    "fulfillment_cancellation_failed",
    "cancel_requested",
]


def create_app(settings: Settings | None = None) -> FastAPI:
    effective = settings or Settings.from_environment()
    database = Database(effective.database_path)
    definitions = DefinitionRegistry.load()
    service = OrderService(database, definitions, effective.definition_version)

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        database.initialize()
        yield

    app = FastAPI(
        title="Determa State order service",
        version="0.1.0",
        lifespan=lifespan,
    )
    app.state.order_service = service

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.post("/orders", response_model=OrderView, status_code=status.HTTP_201_CREATED)
    def create_order(
        request: CreateOrderRequest,
        idempotency_key: Annotated[str, Header(alias="Idempotency-Key", min_length=1)],
    ) -> dict[str, object]:
        return _command(
            lambda: service.create_order(
                event_id=idempotency_key,
                customer_id=request.customer_id,
                amount_cents=request.amount_cents,
            )
        )

    @app.get("/orders/{order_id}", response_model=OrderView)
    def get_order(order_id: str) -> dict[str, object]:
        try:
            return service.get_order(order_id)
        except OrderNotFoundError as exc:
            raise HTTPException(status_code=404, detail="order not found") from exc

    @app.post("/orders/{order_id}/events/{event_name}", response_model=OrderView)
    def apply_event(
        order_id: str,
        event_name: EventName,
        request: OutcomeRequest,
        idempotency_key: Annotated[str, Header(alias="Idempotency-Key", min_length=1)],
    ) -> dict[str, object]:
        payload: dict[str, object] = {}
        requires_reason = event_name in {
            "payment_rejected",
            "fulfillment_failed",
            "fulfillment_cancellation_failed",
        }
        if requires_reason and not request.reason:
            raise HTTPException(status_code=422, detail="reason is required for this outcome")
        if request.reason is not None:
            payload["reason"] = request.reason
        return _command(
            lambda: service.apply_event(
                order_id=order_id,
                event_id=idempotency_key,
                event_name=event_name,
                payload=payload,
            )
        )

    @app.get("/admin/outbox", response_model=list[OutboxView])
    def list_outbox(order_id: str | None = None) -> list[dict[str, object]]:
        return service.list_outbox(order_id)

    @app.post("/admin/outbox/deliver", response_model=DeliverySummary)
    def deliver_outbox() -> dict[str, int]:
        return service.deliver_outbox()

    @app.post("/admin/orders/{order_id}/migrate", response_model=OrderView)
    def migrate_order(order_id: str) -> dict[str, object]:
        return _command(lambda: service.migrate_order(order_id))

    @app.get("/admin/orders/{order_id}/debug", response_model=DebugView)
    def debug_order(order_id: str) -> dict[str, object]:
        try:
            return service.debug_order(order_id)
        except OrderNotFoundError as exc:
            raise HTTPException(status_code=404, detail="order not found") from exc

    return app


def _command(call: Callable[[], CommandResult]) -> dict[str, Any]:
    try:
        result = call()
    except OrderNotFoundError as exc:
        raise HTTPException(status_code=404, detail="order not found") from exc
    except DuplicateConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except CommandRejectedError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    view = dict(result.view)
    view["duplicate"] = result.duplicate
    view["migration_applied"] = result.migration_applied
    return view
