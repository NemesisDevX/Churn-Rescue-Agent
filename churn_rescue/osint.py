"""Zero-shot competitor OSINT: public brief via stdlib urllib only.

Returns ``None`` on any failure (offline, 404, timeout) -- the FSM always
keeps its static/generic fallback path.
"""
from __future__ import annotations

import json
import urllib.parse
import urllib.request

WIKI_SUMMARY = "https://en.wikipedia.org/api/rest_v1/page/summary/{name}"


def fetch_company_brief(name: str, timeout: float = 3.0) -> str | None:
    """Short public description of an unknown competitor; None on failure."""
    if not name:
        return None
    try:
        url = WIKI_SUMMARY.format(name=urllib.parse.quote(name))
        req = urllib.request.Request(
            url, headers={"User-Agent": "churn-rescue-agent/1.0"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read())
        return (data.get("extract") or "")[:600] or None
    except Exception:
        return None
