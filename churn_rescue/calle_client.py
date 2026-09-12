"""Direct HTTP client for the CALL-E Developer API.

We deliberately avoid the official SDK in this service so we can control
retries, timeouts, and logging explicitly. The official SDK is a great
choice for a normal Python service; here we want the retry logic to be
visible and tunable.
"""
from __future__ import annotations

import json
import logging
from typing import Any

import httpx
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
    before_sleep_log,
)

logger = logging.getLogger(__name__)


class CalleApiError(Exception):
    """Raised when the CALL-E API returns an error or cannot be reached."""

    def __init__(self, message: str, status_code: int | None = None, body: str | None = None):
        super().__init__(message)
        self.status_code = status_code
        self.body = body


class CalleClient:
    """Async client for the CALL-E REST API."""

    def __init__(
        self,
        api_key: str,
        base_url: str = "https://api.heycall-e.com",
        timeout: float = 30.0,
    ) -> None:
        if not api_key:
            logger.warning("CALLE_API_KEY is empty; outbound calls will fail")

        self._api_key = api_key
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout

        self._client = httpx.AsyncClient(
            base_url=self._base_url,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
            timeout=httpx.Timeout(timeout, connect=timeout / 3),
        )

    async def close(self) -> None:
        await self._client.aclose()

    @retry(
        retry=retry_if_exception_type((httpx.TimeoutException, httpx.ConnectError, httpx.NetworkError)),
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        before_sleep=before_sleep_log(logger, logging.WARNING),
        reraise=True,
    )
    async def create_call(
        self,
        payload: dict[str, Any],
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        """POST /v1/calls to queue an outbound call task."""
        headers: dict[str, str] = {}
        if idempotency_key:
            headers["Idempotency-Key"] = idempotency_key

        try:
            response = await self._client.post(
                "/v1/calls",
                json=payload,
                headers=headers,
            )
            response.raise_for_status()
        except httpx.TimeoutException as exc:
            # FIXME: connection timeout sometimes on slow networks. We retry,
            # but if this keeps firing the base URL or ISP path may need review.
            logger.error("CALL-E create_call timed out after %.1fs: %s", self._timeout, exc)
            raise CalleApiError("CALL-E create call timed out") from exc
        except httpx.ConnectError as exc:
            logger.error("CALL-E create_call connection error: %s", exc)
            raise CalleApiError("CALL-E connection failed") from exc
        except httpx.HTTPStatusError as exc:
            status = exc.response.status_code
            body = exc.response.text[:500]
            logger.error(
                "CALL-E create_call HTTP %s: %s",
                status,
                body,
            )
            raise CalleApiError(
                f"CALL-E returned HTTP {status}",
                status_code=status,
                body=body,
            ) from exc
        except httpx.RequestError as exc:
            logger.error("CALL-E create_call request error: %s", exc)
            raise CalleApiError(f"CALL-E request failed: {exc}") from exc

        try:
            return response.json()
        except json.JSONDecodeError as exc:
            logger.error("CALL-E create_call returned invalid JSON: %s", response.text[:500])
            raise CalleApiError("CALL-E returned invalid JSON") from exc

    async def get_call(self, call_id: str) -> dict[str, Any]:
        """GET /v1/calls/{call_id}."""
        try:
            response = await self._client.get(f"/v1/calls/{call_id}")
            response.raise_for_status()
            return response.json()
        except httpx.HTTPStatusError as exc:
            status = exc.response.status_code
            body = exc.response.text[:500]
            logger.error("CALL-E get_call HTTP %s: %s", status, body)
            raise CalleApiError(
                f"CALL-E returned HTTP {status}",
                status_code=status,
                body=body,
            ) from exc
        except (httpx.TimeoutException, httpx.ConnectError) as exc:
            logger.error("CALL-E get_call transport error: %s", exc)
            raise CalleApiError("CALL-E call lookup failed") from exc

    async def list_events(
        self,
        call_id: str,
        cursor: str | None = None,
        limit: int = 50,
    ) -> dict[str, Any]:
        """GET /v1/calls/{call_id}/events."""
        params: dict[str, Any] = {"limit": limit}
        if cursor:
            params["cursor"] = cursor

        try:
            response = await self._client.get(
                f"/v1/calls/{call_id}/events",
                params=params,
            )
            response.raise_for_status()
            return response.json()
        except httpx.HTTPStatusError as exc:
            status = exc.response.status_code
            body = exc.response.text[:500]
            logger.error("CALL-E list_events HTTP %s: %s", status, body)
            raise CalleApiError(
                f"CALL-E returned HTTP {status}",
                status_code=status,
                body=body,
            ) from exc
        except (httpx.TimeoutException, httpx.ConnectError) as exc:
            logger.error("CALL-E list_events transport error: %s", exc)
            raise CalleApiError("CALL-E events lookup failed") from exc
