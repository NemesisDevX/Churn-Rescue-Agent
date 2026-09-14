# groq over stdlib urllib -- app control kills the sdk deps here
# any failure -> None so the static fsm lines always win
from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from typing import Any

GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"
GROQ_MODEL = "llama3-70b-8192"
GROQ_TIMEOUT_S = 4.0

SYSTEM_PROMPT = (
    "You are Maya, an elite enterprise retention agent. Respond dynamically "
    "to the user's exact objection. Negotiate fiercely but politely. YOU MUST "
    "RESPOND IN THE EXACT SAME LANGUAGE the user speaks (e.g., if they speak "
    "Arabic, reply in Arabic). Keep responses under 2 sentences to maintain "
    "sub-500ms voice latency."
)


def llm_available() -> bool:
    return bool(os.environ.get("GROQ_API_KEY"))


def groq_reply(messages: list[dict[str, str]],
               timeout: float = GROQ_TIMEOUT_S) -> str | None:
    # None = "use the static line"
    key = os.environ.get("GROQ_API_KEY")
    if not key:
        return None
    req = urllib.request.Request(
        GROQ_URL,
        data=json.dumps({
            "model": GROQ_MODEL,
            "messages": messages,
            "max_tokens": 90,
            "temperature": 0.6,
        }).encode(),
        headers={
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data: dict[str, Any] = json.loads(resp.read())
        text = data["choices"][0]["message"]["content"].strip()
        return text or None
    except (urllib.error.URLError, OSError, KeyError, IndexError,
            json.JSONDecodeError, ValueError):
        return None
