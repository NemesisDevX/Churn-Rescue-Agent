"""FastAPI server for Churn-Rescue.

Endpoints:
  - POST /webhook/cancel-subscription  -> triggers a CALL-E retention call
  - POST /calle/webhook                -> receives terminal call events
  - GET  /dashboard                    -> live command-center UI
  - WS   /ws                           -> live sentiment + transcript feed
  - GET  /health                       -> smoke test
  - GET  /customers                    -> list mock CRM records
"""
from __future__ import annotations

import asyncio
import json
import logging
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import BackgroundTasks, FastAPI, HTTPException, Request, WebSocket
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field

from .calle_client import CalleApiError, CalleClient
from .config import settings
from .conversation import ConversationEngine
from .crm import Customer, CustomerRepository, repo
from .db import init_db
from .twilio_client import twilio_client
from .websocket import manager

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Pydantic request / response models
# ---------------------------------------------------------------------------
class CancelSubscriptionRequest(BaseModel):
    user_id: str | None = Field(default=None, description="CRM customer id")
    phone: str | None = Field(default=None, description="E.164 phone number")
    raw_reason: str | None = Field(default=None, description="Optional raw reason from UI")


class CancelSubscriptionResponse(BaseModel):
    status: str
    call_id: str | None
    customer_id: str | None
    offered_max_discount: float | None
    message: str


class CustomerOut(BaseModel):
    id: str
    name: str
    phone: str
    subscription_tier: str
    ltv: float | None
    churn_status: str
    allowed_discount: float | None


class WebhookPayload(BaseModel):
    """Strict top-level validation for CALL-E terminal events.

    The `data` field is intentionally a generic dict; CALL-E's call object
    is large and versioned, so we validate the envelope strictly and inspect
    the payload later.
    """
    model_config = ConfigDict(extra="ignore")

    id: str
    type: str
    created_at: str
    data: dict[str, Any]


# ---------------------------------------------------------------------------
# App lifespan: logging, DB, and client setup
# ---------------------------------------------------------------------------
def setup_logging() -> None:
    level = getattr(logging, settings.log_level.upper(), logging.INFO)
    logging.basicConfig(
        level=level,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )


@asynccontextmanager
async def lifespan(app: FastAPI):
    setup_logging()
    init_db(settings.database_path)

    # FIXME: connection timeout sometimes on slow networks. We pass a shorter
    # connect timeout to the HTTP client and retry in `calle_client.py`.
    app.state.calle = CalleClient(
        api_key=settings.calle_api_key,
        base_url=settings.calle_base_url,
        timeout=settings.call_timeout_seconds,
    )
    logger.info(
        "Churn-Rescue starting | env=%s | db=%s | calle=%s | twilio=%s",
        settings.app_env,
        settings.database_path,
        settings.calle_base_url,
        twilio_client.is_configured,
    )
    yield
    await app.state.calle.close()
    logger.info("Churn-Rescue shutdown complete")


app = FastAPI(
    title="Churn-Rescue",
    description="Autonomous outbound voice retention agent for the CALL-E hackathon.",
    version="0.2.0",
    lifespan=lifespan,
)

static_dir = Path(__file__).parent / "static"
app.mount("/static", StaticFiles(directory=static_dir), name="static")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _e164(phone: str) -> str:
    """Light E.164 normalization; CALL-E is strict about this."""
    cleaned = "".join(ch for ch in phone if ch.isdigit() or ch == "+")
    if not cleaned.startswith("+"):
        # Best-effort default; in production this needs real country logic.
        cleaned = "+1" + cleaned.lstrip("1")
    return cleaned


def _extract_transcript(call_payload: dict[str, Any]) -> list[dict[str, Any]]:
    turns: list[dict[str, Any]] = []
    for recipient in call_payload.get("recipients", []):
        for attempt in recipient.get("attempts", []):
            turns.extend(attempt.get("transcript_turns", []))
    return turns


def _get_calle_from_state() -> CalleClient | None:
    # NOTE: FastAPI's `app` is a module singleton; in the polling task started
    # before `lifespan` ends, `app.state.calle` is alive.
    return getattr(app.state, "calle", None)


async def _broadcast_live(
    call_id: str,
    customer: Customer,
    status: str,
    engine: ConversationEngine,
    turn: dict[str, Any] | None = None,
    accepted: str | None = None,
    completed: bool = False,
) -> None:
    """Push live updates to every connected dashboard client."""
    message_type = "call_completed" if completed else ("transcript_turn" if turn else "status")
    payload = {
        "type": message_type,
        "call_id": call_id,
        "customer_id": customer.id,
        "customer_name": customer.name,
        "status": status,
        "sentiment_label": engine.current_sentiment,
        "sentiment_score": engine.current_sentiment_score,
        "offered_discount": engine.offered_discount,
        "accepted": accepted,
    }
    if turn:
        payload["turn"] = turn

    await manager.broadcast(payload)


# ---------------------------------------------------------------------------
# Background polling for the call outcome
# ---------------------------------------------------------------------------
async def _poll_call_outcome(
    call_id: str,
    customer: Customer,
    retention_id: int,
    engine: ConversationEngine,
) -> None:
    """Poll CALL-E until the call is terminal, then update the CRM."""
    calle = _get_calle_from_state()
    if calle is None:
        logger.error("No CALLE client available for polling; call %s orphaned", call_id)
        return

    deadline = asyncio.get_event_loop().time() + settings.call_max_poll_seconds

    while asyncio.get_event_loop().time() < deadline:
        try:
            call = await calle.get_call(call_id)
        except CalleApiError as exc:
            logger.warning("Polling call %s failed: %s", call_id, exc)
            await asyncio.sleep(settings.call_poll_interval_seconds)
            continue

        status = call.get("status")

        # Continuously analyze *new* transcript turns. We slice against the
        # engine's log so we never re-process or re-broadcast the same turn.
        all_turns = _extract_transcript(call)
        existing_count = len(engine.transcript_log)
        new_turns = all_turns[existing_count:]

        for turn in new_turns:
            engine.process_transcript_turn(turn)

            # Push the turn to the live command center. If the speaker is the
            # customer, the sentiment reflects VADER's real-time analysis.
            await _broadcast_live(
                call_id=call_id,
                customer=customer,
                status=status,
                engine=engine,
                turn=turn,
            )

        if new_turns:
            # Persist the running sentiment read so the DB reflects the latest.
            await repo.update_retention_call(
                retention_id,
                status=status,
                sentiment_label=engine.current_sentiment,
                sentiment_score=engine.current_sentiment_score,
            )

        if status in ("completed", "failed", "canceled"):
            await _finalize_call(call, customer, retention_id, engine)
            return

        # Non-terminal states: queued, in_progress (includes post-call
        # processing). Sleep and try again, but keep dashboards informed.
        if not new_turns:
            await _broadcast_live(call_id, customer, status, engine)
        await asyncio.sleep(settings.call_poll_interval_seconds)

    logger.warning("Call %s polling hit the %ss deadline", call_id, settings.call_max_poll_seconds)
    await repo.update_retention_call(retention_id, status="polling_timeout")


async def _finalize_call(
    call_payload: dict[str, Any],
    customer: Customer,
    retention_id: int,
    engine: ConversationEngine,
) -> None:
    """Persist the terminal call outcome, update churn status, and fire WhatsApp."""
    status = call_payload.get("status", "unknown")
    result = engine.process_call_result(call_payload)

    resolved = result.get("resolved", False)
    churn_status = "saved" if resolved else "churned"

    await repo.update_retention_call(
        retention_id,
        status=status,
        started_at=call_payload.get("created_at"),
        completed_at=call_payload.get("completed_at"),
        transcript_json=json.dumps(result.get("transcript", [])),
        result_json=json.dumps(result),
        cancellation_reason=result.get("cancellation_reason"),
        sentiment_label=result.get("sentiment"),
        sentiment_score=result.get("sentiment_score"),
        offered_discount=result.get("discount_offered"),
        accepted_discount=result.get("accepted"),
        churn_resolved=1 if resolved else 0,
    )

    await repo.update_churn_status(customer.id, churn_status)

    # Omnichannel: if the customer accepted the discount, build and send a
    # WhatsApp confirmation, and push the message to the command center.
    if resolved and result.get("discount_offered"):
        whatsapp_body = twilio_client.build_message(customer, result["discount_offered"])
        await manager.broadcast(
            {
                "type": "whatsapp",
                "call_id": call_payload.get("id", "unknown"),
                "customer_id": customer.id,
                "customer_name": customer.name,
                "body": whatsapp_body,
                "twilio_configured": twilio_client.is_configured,
                "status": "sent" if twilio_client.is_configured else "simulated",
            }
        )
        twilio_client.send_retention_confirmation(customer, result["discount_offered"])

    await _broadcast_live(
        call_id=call_payload.get("id", "unknown"),
        customer=customer,
        status=status,
        engine=engine,
        accepted=result.get("accepted"),
        completed=True,
    )

    logger.info(
        "Call %s finalized | customer=%s | status=%s | resolved=%s | reason=%s | offer=%s | accepted=%s",
        call_payload.get("id"),
        customer.id,
        status,
        resolved,
        result.get("cancellation_reason"),
        result.get("discount_offered"),
        result.get("accepted"),
    )


# ---------------------------------------------------------------------------
# Public endpoints
# ---------------------------------------------------------------------------
@app.get("/")
async def root() -> dict[str, Any]:
    return {"service": "Churn-Rescue", "dashboard": "/dashboard"}


@app.get("/health")
async def health() -> dict[str, Any]:
    return {
        "ok": True,
        "env": settings.app_env,
        "calle_configured": bool(settings.calle_api_key),
        "twilio_configured": twilio_client.is_configured,
    }


@app.get("/customers")
async def list_customers() -> list[CustomerOut]:
    customers = await repo.list_all()
    return [
        CustomerOut(
            id=c.id,
            name=c.name,
            phone=c.phone,
            subscription_tier=c.subscription_tier,
            ltv=c.ltv,
            churn_status=c.churn_status,
            allowed_discount=c.allowed_discount,
        )
        for c in customers
    ]


@app.get("/dashboard")
async def dashboard() -> FileResponse:
    """Serve the live command-center dashboard."""
    return FileResponse(static_dir / "index.html")


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket) -> None:
    """WebSocket feed for the live dashboard."""
    await manager.connect(websocket)
    try:
        while True:
            # Clients do not need to send anything; we just keep the socket open.
            await websocket.receive_text()
    except Exception as exc:
        logger.debug("WebSocket disconnected: %s", exc)
    finally:
        manager.disconnect(websocket)


@app.post(
    "/webhook/cancel-subscription",
    response_model=CancelSubscriptionResponse,
    status_code=202,
)
async def cancel_subscription_webhook(
    payload: CancelSubscriptionRequest,
    background_tasks: BackgroundTasks,
) -> CancelSubscriptionResponse:
    """Webhook called when a customer clicks 'Cancel Subscription'.

    We immediately look them up, compute a dynamic discount from their LTV,
    and queue a CALL-E retention call. The HTTP response returns as soon as
    CALL-E accepts the task; a background task polls for the final result.
    """
    if not payload.user_id and not payload.phone:
        logger.error("Cancel webhook missing both user_id and phone")
        raise HTTPException(status_code=422, detail="Provide user_id or phone")

    if not settings.calle_api_key:
        logger.error("Cancel webhook rejected: CALLE_API_KEY not configured")
        raise HTTPException(
            status_code=503,
            detail="CALL-E integration is not configured",
        )

    customer: Customer | None = None
    if payload.user_id:
        customer = await repo.get_by_id(payload.user_id)
    if not customer and payload.phone:
        normalized = _e164(payload.phone)
        customer = await repo.get_by_phone(normalized)

    if not customer:
        logger.error(
            "Cancel webhook received for unknown customer user_id=%s phone=%s",
            payload.user_id,
            payload.phone,
        )
        raise HTTPException(status_code=404, detail="Customer not found in mock CRM")

    # Ensure LTV is materialized. If the CRM already has it, use it;
    # otherwise derive it from monthly_amount * months_active.
    if customer.ltv is None:
        customer.ltv = customer.monthly_amount * customer.months_active
        await repo.update_ltv(customer.id, customer.ltv)

    engine = ConversationEngine(customer)
    max_discount = engine.compute_max_discount(customer.ltv)
    await repo.update_allowed_discount(customer.id, max_discount)
    engine.max_discount = max_discount

    # Store the retention attempt so we can correlate webhook/polling later.
    retention_id = await repo.create_retention_call(customer.id, status="initiated")

    task_text = engine.build_task_prompt()
    result_schema = engine.build_result_schema()

    phone = _e164(customer.phone)
    calle_payload = {
        "task": task_text,
        "recipients": [
            {
                "phones": [phone],
                "locale": "en-US",
                "region": "US",
            }
        ],
        "result_schema": result_schema,
        "metadata": {
            "customer_id": customer.id,
            "retention_id": str(retention_id),
            "source": "churn-rescue",
            "raw_reason": payload.raw_reason,
        },
        "webhook_url": settings.calle_webhook_url,
    }

    # Idempotency key ties the call to this customer+retention attempt.
    idempotency_key = f"churn-rescue:{customer.id}:{retention_id}:{uuid.uuid4().hex[:8]}"

    calle = _get_calle_from_state()
    try:
        call = await calle.create_call(calle_payload, idempotency_key=idempotency_key)
    except CalleApiError as exc:
        logger.error("CALL-E create_call failed for customer %s: %s", customer.id, exc)
        await repo.update_retention_call(retention_id, status="call_failed")
        raise HTTPException(
            status_code=502,
            detail=f"Failed to initiate retention call: {exc}",
        ) from exc
    except Exception as exc:
        # Defensive catch-all. Should not happen, but we never want a 500
        # without logging.
        logger.exception("Unexpected error creating CALL-E call: %s", exc)
        await repo.update_retention_call(retention_id, status="call_failed")
        raise HTTPException(status_code=500, detail="Internal error initiating call") from exc

    call_id = call.get("id")
    await repo.update_retention_call(
        retention_id,
        call_e_id=call_id,
        status=call.get("status", "queued"),
        task=task_text,
    )

    # Detach the long polling into the background so the webhook returns fast.
    # For hackathon demos this keeps the browser responsive.
    background_tasks.add_task(
        _poll_call_outcome,
        call_id,
        customer,
        retention_id,
        engine,
    )

    logger.info(
        "Retention call queued | customer=%s | call_id=%s | max_discount=%.2f%%",
        customer.id,
        call_id,
        max_discount,
    )

    return CancelSubscriptionResponse(
        status="accepted",
        call_id=call_id,
        customer_id=customer.id,
        offered_max_discount=max_discount,
        message="Retention call accepted by CALL-E",
    )


@app.post("/calle/webhook")
async def calle_webhook(payload: WebhookPayload, request: Request) -> dict[str, Any]:
    """Receive terminal CALL-E call events.

    CALL-E sends `call.completed`, `call.failed`, or
    `call.result_validation_failed` here. We use `CALL-E-Event-Id` to
    deduplicate at-least-once deliveries.
    """
    event_id = request.headers.get("CALL-E-Event-Id")
    logger.info(
        "Received CALL-E webhook | event_id=%s | type=%s",
        event_id,
        payload.type,
    )

    if event_id and await repo.is_event_processed(event_id):
        logger.info("Duplicate CALL-E event %s ignored", event_id)
        return {"ok": True}

    call_data = payload.data
    metadata = call_data.get("metadata", {})
    customer_id = metadata.get("customer_id")
    retention_id_raw = metadata.get("retention_id")

    if not customer_id or not retention_id_raw:
        logger.warning("CALL-E webhook missing customer_id/retention_id metadata")
        # Still acknowledge so CALL-E stops retrying.
        return {"ok": True}

    try:
        retention_id = int(retention_id_raw)
    except (TypeError, ValueError):
        logger.error("Invalid retention_id in webhook metadata: %r", retention_id_raw)
        return {"ok": True}

    customer = await repo.get_by_id(customer_id)
    if not customer:
        logger.error("Webhook references unknown customer %s", customer_id)
        return {"ok": True}

    # Rebuild the engine state to process the terminal result.
    engine = ConversationEngine(customer)
    engine.max_discount = customer.allowed_discount or engine.compute_max_discount(customer.ltv)

    await _finalize_call(call_data, customer, retention_id, engine)

    if event_id:
        await repo.mark_event_processed(event_id)

    return {"ok": True}
