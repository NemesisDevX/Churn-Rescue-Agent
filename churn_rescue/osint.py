# wiki summary scrape for vendors we don't have a battlecard for
# None on any failure -- the static line covers it
from __future__ import annotations

import json
import urllib.parse
import urllib.request

WIKI_SUMMARY = "https://en.wikipedia.org/api/rest_v1/page/summary/{name}"


def fetch_company_brief(name: str, timeout: float = 3.0) -> str | None:
    # FIXME: wikipedia is a placeholder, swap for a real pricing feed later
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
