"""ASGI server: dashboard + roster API + /ws telemetry channel.

Supports N concurrent retention calls (swarm mode) -- every agent gets a
call_id and every outbound event carries it so the UI can route per column.

    python -m churn_rescue.server   ->  http://127.0.0.1:8000
"""
from __future__ import annotations

import asyncio
import base64
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
from .contracts import CONTRACTS_DIR
from .llm import llm_available
from .stt import AssemblyStream, stt_available
from .db import (
    DEFAULT_DB_PATH,
    get_customer,
    init_db,
    list_customers,
    log_call,
    set_customer_status,
)

logger = logging.getLogger("churn_rescue.server")

STATIC_DIR = Path(__file__).resolve().parent / "static"

agents: dict[str, RetentionAgent] = {}   # call_id -> live FSM
stt_sessions: dict[str, AssemblyStream] = {}  # call_id -> live STT pipe
_ws_clients: set[WebSocket] = set()


async def run_utterance(agent: RetentionAgent, text: str) -> None:
    """handle_utterance can block on Groq urllib -- keep it off the loop."""
    events = await asyncio.to_thread(agent.handle_utterance, text)
    await emit(agent, events)


async def start_stt(call_id: str) -> None:
    """Bridge browser mic chunks -> AssemblyAI -> FSM utterances."""
    stream = AssemblyStream()
    if not await stream.connect():
        await broadcast({"type": "stt_status", "call_id": call_id,
                         "mode": "webspeech"})
        return
    stt_sessions[call_id] = stream
    await broadcast({"type": "stt_status", "call_id": call_id,
                     "mode": "assemblyai"})
    async for text in stream.transcripts():
        agent = agents.get(call_id)
        if agent is None or not agent.call_active:
            break
        await run_utterance(agent, text)
    stt_sessions.pop(call_id, None)


async def stop_stt(call_id: str) -> None:
    stream = stt_sessions.pop(call_id, None)
    if stream is not None:
        await stream.close()


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


async def emit(agent: RetentionAgent, events: list[dict[str, Any]]) -> None:
    """Stamp call_id on each event, broadcast, persist on termination."""
    call_id = agent.customer.customer_id
    for event in events:
        event.setdefault("call_id", call_id)
        await broadcast(event)
        if event["type"] == "call_ended":
            log_call(
                customer_id=call_id,
                outcome=agent.outcome or "unknown",
                discount_pct=agent.current_offer,
                states_visited=agent.end_summary()["states_visited"],
                transcript=agent.transcript,
                db_path=DEFAULT_DB_PATH,
            )
            status = "saved" if agent.outcome == "saved" else "churned"
            if agent.outcome == "abandoned":
                status = "at_risk"
            set_customer_status(call_id, status, DEFAULT_DB_PATH)
            await broadcast({
                "type": "customer_status",
                "call_id": call_id,
                "customer_id": call_id,
                "status": status,
            })
            agents.pop(call_id, None)
            await stop_stt(call_id)


async def start_agent(customer_id: str) -> RetentionAgent | None:
    """Arm an FSM for one account and fire the greeting. No-op if live."""
    if customer_id in agents and agents[customer_id].call_active:
        return None
    customer = get_customer(customer_id, DEFAULT_DB_PATH)
    if customer is None:
        return None
    set_customer_status(customer_id, "in_call", DEFAULT_DB_PATH)
    await broadcast({
        "type": "customer_status",
        "call_id": customer_id,
        "customer_id": customer_id,
        "status": "in_call",
    })
    agent = RetentionAgent(customer)
    agents[customer_id] = agent
    await emit(agent, agent.start_call())
    return agent


# REST API
async def api_customers(request) -> JSONResponse:
    customers = [vars(c) for c in list_customers(DEFAULT_DB_PATH)]
    return JSONResponse({"customers": customers})


async def api_start_call(request) -> JSONResponse:
    body = await request.json()
    agent = await start_agent(body.get("customer_id"))
    if agent is None:
        return JSONResponse(
            {"error": "unknown customer_id or call already live"}, status_code=409)
    return JSONResponse({"call_id": agent.customer.customer_id})


async def api_utterance(request) -> JSONResponse:
    """Text fallback when Web Speech API is unavailable."""
    body = await request.json()
    agent = agents.get(body.get("call_id"))
    if agent is None or not agent.call_active:
        return JSONResponse({"error": "no active call"}, status_code=409)
    await run_utterance(agent, body.get("text", ""))
    return JSONResponse({"ok": True})


async def api_override(request) -> JSONResponse:
    body = await request.json()
    agent = agents.get(body.get("call_id"))
    if agent is None or not agent.call_active:
        return JSONResponse({"error": "no active call"}, status_code=409)
    await emit(agent, agent.takeover())
    return JSONResponse({"ok": True})


async def api_end_call(request) -> JSONResponse:
    body = await request.json()
    agent = agents.get(body.get("call_id"))
    if agent is not None and agent.call_active:
        events: list[dict[str, Any]] = []
        agent._end_call("abandoned", events)
        await emit(agent, events)
    return JSONResponse({"status": "ended"})


async def dashboard(request) -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


# ws telemetry
async def ws_telemetry(websocket: WebSocket) -> None:
    await websocket.accept()
    _ws_clients.add(websocket)
    await websocket.send_text(json.dumps({
        "type": "capabilities",
        "llm": "groq" if llm_available() else "static",
        "stt": "assemblyai" if stt_available() else "webspeech",
    }))
    try:
        while True:
            message = json.loads(await websocket.receive_text())
            mtype = message.get("type")

            if mtype == "utterance":
                agent = agents.get(message.get("call_id"))
                if agent and agent.call_active:
                    await run_utterance(agent, message.get("text", ""))
            elif mtype == "stt_begin":
                asyncio.create_task(start_stt(message.get("call_id")))
            elif mtype == "stt_chunk":
                stream = stt_sessions.get(message.get("call_id"))
                if stream is not None:
                    try:
                        await stream.send_pcm(
                            base64.b64decode(message.get("data", "")))
                    except Exception:
                        pass
            elif mtype == "stt_end":
                await stop_stt(message.get("call_id"))
            elif mtype == "start_call":
                await start_agent(message.get("customer_id"))
            elif mtype == "swarm":
                for cid in message.get("customer_ids", []):
                    await start_agent(cid)
            elif mtype == "override":
                agent = agents.get(message.get("call_id"))
                if agent and agent.call_active:
                    await emit(agent, agent.takeover())
            elif mtype == "end_call":
                agent = agents.get(message.get("call_id"))
                if agent and agent.call_active:
                    events: list[dict[str, Any]] = []
                    agent._end_call("abandoned", events)
                    await emit(agent, events)
            elif mtype == "ping":
                await websocket.send_text(json.dumps({"type": "pong"}))
    except WebSocketDisconnect:
        pass
    finally:
        _ws_clients.discard(websocket)


CONTRACTS_DIR.mkdir(parents=True, exist_ok=True)

routes = [
    Route("/", dashboard),
    Route("/api/customers", api_customers, methods=["GET"]),
    Route("/api/calls/start", api_start_call, methods=["POST"]),
    Route("/api/calls/utterance", api_utterance, methods=["POST"]),
    Route("/api/calls/override", api_override, methods=["POST"]),
    Route("/api/calls/end", api_end_call, methods=["POST"]),
    WebSocketRoute("/ws", ws_telemetry),
    Mount("/contracts", app=StaticFiles(directory=CONTRACTS_DIR), name="contracts"),
    Mount("/static", app=StaticFiles(directory=STATIC_DIR), name="static"),
]


@asynccontextmanager
async def lifespan(app):
    init_db(DEFAULT_DB_PATH)
    CONTRACTS_DIR.mkdir(parents=True, exist_ok=True)
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
