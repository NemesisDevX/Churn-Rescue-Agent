# pcm16/16k chunks come in over the dashboard ws, get forwarded to
# assemblyai realtime, finals get pushed back into the fsm
# any failure -> ui stays on the webspeech path
from __future__ import annotations

import asyncio
import json
import os
from typing import Any, AsyncIterator

AAI_URL = (
    "wss://api.assemblyai.com/v2/realtime/ws"
    "?sample_rate=16000&language_detection=true"
)


def stt_available() -> bool:
    return bool(os.environ.get("ASSEMBLYAI_API_KEY"))


class AssemblyStream:
    # one session per call

    def __init__(self) -> None:
        self.ws: Any = None
        self.dead = False

    async def connect(self) -> bool:
        key = os.environ.get("ASSEMBLYAI_API_KEY")
        if not key:
            return False
        try:
            import websockets
            self.ws = await asyncio.wait_for(
                websockets.connect(
                    AAI_URL,
                    additional_headers={"Authorization": key},
                ),
                timeout=6.0,
            )
            # session_begins handshake
            hello = json.loads(await asyncio.wait_for(self.ws.recv(), 5))
            return hello.get("message_type") in (None, "SessionBegins",
                                                 "session_begins")
        except Exception:
            self.ws = None
            return False

    async def send_pcm(self, chunk: bytes) -> None:
        if self.ws is None or self.dead:
            return
        try:
            await self.ws.send(chunk)
        except Exception:
            self.dead = True

    async def transcripts(self) -> AsyncIterator[str]:
        # yields FinalTranscript text till the socket drops
        if self.ws is None:
            return
        try:
            async for raw in self.ws:
                try:
                    msg = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                if (msg.get("message_type") == "FinalTranscript"
                        and msg.get("text")):
                    yield msg["text"]
        except Exception:
            pass
        self.dead = True

    async def close(self) -> None:
        if self.ws is not None:
            try:
                await self.ws.send(json.dumps({"terminate_session": True}))
                await self.ws.close()
            except Exception:
                pass
            self.ws = None
