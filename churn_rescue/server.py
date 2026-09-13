"""ASGI server: dashboard + roster API + /ws telemetry channel.

    python -m churn_rescue.server   ->  http://127.0.0.1:8000
"""
from __future__ import annotations

import asyncio
import json
import logging
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from starlette.applications import Starlette
from starlette.responses import FileResponse, JSONResponse
from starlette.routing import Mount, Route, WebSocketRoute
from starlette.staticfiles import StaticFiles
from starlette.websockets import WebSocket, WebSocketDisconnect

from .agent import RetentionAgent
from .db import (
    DEFAULT_DB_PATH,
    Customer,
    get_customer,
    init_db,
    list_customers,
    log_call,
    set_customer_status,
)

logger = logging.getLogger("churn_rescue.server")

STATIC_DIR = Path(__file__).resolve().parent / "static"

# one call at a time keeps demo state unambiguous
active_agent: RetentionAgent | None = None
_ws_clients: set[WebSocket] = set()


async def broadcast(event: dict[str, Any]) -> None:
    """Fan out to all dashboards; a stalled socket must not starve the rest."""
    payload = json.dumps(event)
    clients = list(_ws_clients)

    async def _send(ws: WebSocket) -> WebSocket | None:
        try:
            await asyncio.wait_for(ws.send_text(payload), timeout=2.0)
            return None
        except Exception:
            return ws

    for ws in await asyncio.gather(*(_send(ws) for ws in clients)):
        if ws is not None:
            _ws_clients.discard(ws)


async def emit(events: list[dict[str, Any]]) -> None:
    """Broadcast FSM events; persist call_log row when the call ends."""
    for event in events:
        await broadcast(event)
        if event["type"] == "call_ended" and active_agent is not None:
            log_call(
                customer_id=active_agent.customer.customer_id,
                outcome=active_agent.outcome or "unknown",
                discount_pct=active_agent.current_offer,
                states_visited=active_agent.end_summary()["states_visited"],
                transcript=active_agent.transcript,
                db_path=DEFAULT_DB_PATH,
            )
            set_customer_status(
                active_agent.customer.customer_id,
                "saved" if active_agent.outcome == "saved" else "churned",
                DEFAULT_DB_PATH,
            )
            await broadcast({
                "type": "customer_status",
                "customer_id": active_agent.customer.customer_id,
                "status": "saved" if active_agent.outcome == "saved" else "churned",
            })


# REST API
async def api_customers(request) -> JSONResponse:
    customers = [vars(c) for c in list_customers(DEFAULT_DB_PATH)]
    return JSONResponse({"customers": customers})


async def api_start_call(request) -> JSONResponse:
    global active_agent
    body = await request.json()
    customer_id = body.get("customer_id")
    customer = get_customer(customer_id, DEFAULT_DB_PATH)
    if customer is None:
        return JSONResponse({"error": "unknown customer_id"}, status_code=404)
    if active_agent is not None and active_agent.call_active:
        return JSONResponse({"error": "a call is already in progress"}, status_code=409)

    set_customer_status(customer.customer_id, "in_call", DEFAULT_DB_PATH)
    await broadcast({
        "type": "customer_status",
        "customer_id": customer.customer_id,
        "status": "in_call",
    })

    active_agent = RetentionAgent(customer)
    events = active_agent.start_call()
    await emit(events)
    return JSONResponse({"call_id": customer.customer_id, "events": events})


async def api_utterance(request) -> JSONResponse:
    """Text fallback when Web Speech API is unavailable."""
    if active_agent is None or not active_agent.call_active:
        return JSONResponse({"error": "no active call"}, status_code=409)
    body = await request.json()
    events = active_agent.handle_utterance(body.get("text", ""))
    await emit(events)
    return JSONResponse({"events": events})


async def api_end_call(request) -> JSONResponse:
    global active_agent
    if active_agent is not None and active_agent.call_active:
        events: list[dict[str, Any]] = []
        active_agent._end_call("abandoned", events)
        await emit(events)
    active_agent = None
    return JSONResponse({"status": "ended"})


async def dashboard(request) -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


# ws telemetry
async def ws_telemetry(websocket: WebSocket) -> None:
    global active_agent
    await websocket.accept()
    _ws_clients.add(websocket)
    try:
        while True:
            message = json.loads(await websocket.receive_text())
            mtype = message.get("type")

            if mtype == "utterance":
                if active_agent is not None and active_agent.call_active:
                    await emit(active_agent.handle_utterance(message.get("text", "")))
            elif mtype == "start_call":
                customer_id = message.get("customer_id")
                customer = get_customer(customer_id, DEFAULT_DB_PATH)
                if customer and not (active_agent and active_agent.call_active):
                    set_customer_status(customer.customer_id, "in_call", DEFAULT_DB_PATH)
                    active_agent = RetentionAgent(customer)
                    await emit(active_agent.start_call())
            elif mtype == "end_call":
                if active_agent is not None and active_agent.call_active:
                    events: list[dict[str, Any]] = []
                    active_agent._end_call("abandoned", events)
                    await emit(events)
                active_agent = None
            elif mtype == "ping":
                await websocket.send_text(json.dumps({"type": "pong"}))
    except WebSocketDisconnect:
        pass
    finally:
        _ws_clients.discard(websocket)


routes = [
    Route("/", dashboard),
    Route("/api/customers", api_customers, methods=["GET"]),
    Route("/api/calls/start", api_start_call, methods=["POST"]),
    Route("/api/calls/utterance", api_utterance, methods=["POST"]),
    Route("/api/calls/end", api_end_call, methods=["POST"]),
    WebSocketRoute("/ws", ws_telemetry),
    Mount("/static", app=StaticFiles(directory=STATIC_DIR), name="static"),
]

@asynccontextmanager
async def lifespan(app):
    init_db(DEFAULT_DB_PATH)
    yield


app = Starlette(routes=routes, lifespan=lifespan)


def main() -> None:
    import uvicorn

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
    )
    uvicorn.run(app, host="127.0.0.1", port=8000, log_level="warning")


if __name__ == "__main__":
    main()
