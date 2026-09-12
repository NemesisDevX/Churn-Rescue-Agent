"""Twilio WhatsApp client for omnichannel retention confirmations.

When CALL-E reports that a customer accepted the retention discount, we
fire an immediate WhatsApp message from the configured sandbox number so
there is a written record of the offer.
"""
from __future__ import annotations

import logging

from twilio.base.exceptions import TwilioException
from twilio.rest import Client

from .config import settings
from .db import Customer

logger = logging.getLogger(__name__)


class TwilioWhatsAppClient:
    """Small, safe wrapper around Twilio's messages API."""

    def __init__(self) -> None:
        self._client: Client | None = None

        if (
            settings.twilio_account_sid
            and settings.twilio_auth_token
            and settings.twilio_whatsapp_from
        ):
            try:
                self._client = Client(
                    settings.twilio_account_sid,
                    settings.twilio_auth_token,
                )
                logger.info("Twilio WhatsApp client initialized")
            except Exception as exc:
                logger.error("Failed to initialize Twilio client: %s", exc)
        else:
            logger.warning("Twilio credentials incomplete; WhatsApp confirmations disabled")

    @property
    def is_configured(self) -> bool:
        return self._client is not None

    @staticmethod
    def _whatsapp_number(raw: str) -> str:
        """Ensure a number is prefixed with `whatsapp:` for Twilio."""
        cleaned = raw.strip()
        if cleaned.startswith("whatsapp:"):
            return cleaned
        if not cleaned.startswith("+"):
            # Best-effort fallback; in production this needs country logic.
            cleaned = "+1" + cleaned.lstrip("1")
        return f"whatsapp:{cleaned}"

    def build_message(self, customer: Customer, discount: float | None) -> str:
        """Return the WhatsApp confirmation text for a given customer/offer."""
        return (
            f"Hi {customer.name}, this is Churn-Rescue. "
            f"We confirm the {discount or 0:.0f}% retention discount has been applied "
            "to your subscription. Thanks for staying with us! "
            "Reply if you have any questions."
        )

    def send_retention_confirmation(
        self,
        customer: Customer,
        discount: float | None,
    ) -> str | None:
        """Send a WhatsApp message confirming the accepted retention discount."""
        if not self.is_configured:
            logger.warning(
                "WhatsApp confirmation skipped for %s: Twilio not configured",
                customer.id,
            )
            return None

        if not customer.phone:
            logger.error("Customer %s has no phone; cannot send WhatsApp", customer.id)
            return None

        body = self.build_message(customer, discount)

        to = self._whatsapp_number(customer.phone)
        from_ = self._whatsapp_number(settings.twilio_whatsapp_from)

        try:
            message = self._client.messages.create(  # type: ignore[attr-defined]
                body=body,
                from_=from_,
                to=to,
            )
            logger.info(
                "WhatsApp confirmation sent to %s | sid=%s | discount=%s%%",
                customer.id,
                message.sid,
                discount,
            )
            return message.sid
        except TwilioException as exc:
            logger.error(
                "Twilio WhatsApp send failed for %s: %s",
                customer.id,
                exc,
            )
            return None
        except Exception as exc:
            logger.exception(
                "Unexpected error sending WhatsApp to %s: %s",
                customer.id,
                exc,
            )
            return None


# Module-level singleton. Safe to import anywhere; it is a no-op when unconfigured.
twilio_client = TwilioWhatsAppClient()
