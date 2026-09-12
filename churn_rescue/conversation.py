"""Retention conversation flow, LTV-based discount math, and CALL-E prompt engineering.

The conversation engine is the "brain" of the agent. It knows:
  * How to greet, probe, empathize, offer, and close.
  * How to translate customer Lifetime Value into the maximum discount.
  * How to interpret transcript turns and adjust its emotional read of the call.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field

from .config import settings
from .db import Customer
from .sentiment import analyze_sentiment

logger = logging.getLogger(__name__)


@dataclass
class ConversationEngine:
    """State machine for one retention call."""

    customer: Customer | None
    max_discount: float = 0.0
    current_state: str = "greeting"
    current_sentiment: str = "neutral"
    current_sentiment_score: float = 0.0
    offered_discount: float | None = None
    cancellation_reason: str | None = None
    transcript_log: list[dict] = field(default_factory=list)

    # Ordered state flow the agent should follow.
    STATES = [
        "greeting",
        "probe_reason",
        "empathize",
        "offer_discount",
        "handle_objection",
        "close",
    ]

    @classmethod
    def compute_max_discount(cls, ltv: float | None) -> float:
        """Compute the largest discount the agent is allowed to offer.

        The LTV is bucketed by `ltv_tier_step`. Each full bucket adds
        `discount_step_percent` to the base, capped by `discount_max_percent`.
        """
        if ltv is None or ltv <= 0:
            return settings.discount_min_percent

        steps = int(ltv // settings.ltv_tier_step)
        discount = settings.discount_min_percent + (steps * settings.discount_step_percent)
        return min(discount, settings.discount_max_percent)

    def determine_offer(self, sentiment: str | None = None) -> float:
        """Pick a concrete discount based on sentiment and remaining headroom.

        Negative sentiment -> lean toward the max to save the customer.
        Positive/neutral  -> start in the middle to protect margin.
        """
        sentiment = sentiment or self.current_sentiment
        if sentiment == "negative":
            anchor = self.max_discount
        elif sentiment == "positive":
            anchor = (settings.discount_min_percent + self.max_discount) / 2
        else:
            anchor = self.max_discount - 5.0

        # Keep the offer within the approved band.
        return round(max(settings.discount_min_percent, min(self.max_discount, anchor)), 2)

    def process_transcript_turn(self, turn: dict) -> None:
        """Ingest a single transcript turn and update state + sentiment."""
        self.transcript_log.append(turn)
        text = turn.get("text", "") or ""
        speaker = turn.get("speaker", "unknown")

        # Only score customer speech; agent text is skipped for sentiment.
        if speaker == "user" and text:
            result = analyze_sentiment(text)
            self.current_sentiment = result["label"]
            self.current_sentiment_score = float(result["score"])
            logger.info(
                "Sentiment update for customer %s: %s (score=%s)",
                self.customer.id if self.customer else "unknown",
                self.current_sentiment,
                result["score"],
            )

            # Simple state transitions driven by what the customer just said.
            lower = text.lower()
            if self.current_state == "greeting":
                self.current_state = "probe_reason"
            elif self.current_state == "probe_reason" and any(
                kw in lower for kw in ("cancel", "leaving", "quit", "stop", "done", "expensive", "too much")
            ):
                self.current_state = "empathize"
            elif self.current_state == "empathize" and any(
                kw in lower for kw in ("discount", "offer", "deal", "price", "money", "cheaper")
            ):
                self.current_state = "offer_discount"
                self.offered_discount = self.determine_offer()
            elif self.current_state == "offer_discount" and any(
                kw in lower for kw in ("no", "not enough", "better", "more", "still")
            ):
                self.current_state = "handle_objection"
            elif any(kw in lower for kw in ("yes", "sure", "ok", "okay", "let's do it", "agreed")):
                self.current_state = "close"

    def build_task_prompt(self) -> str:
        """Generate the CALL-E task instruction that drives the voice agent."""
        if not self.customer:
            raise ValueError("Cannot build task prompt without a customer")

        # Start from a fresh state whenever a task is built.
        self.current_state = "greeting"
        self.current_sentiment = "neutral"

        customer_context = {
            "name": self.customer.name,
            "tier": self.customer.subscription_tier,
            "monthly_amount": self.customer.monthly_amount,
            "months_active": self.customer.months_active,
            "lifetime_value": f"${self.customer.ltv:,.2f}" if self.customer.ltv else "unknown",
        }

        discount_floor = settings.discount_min_percent
        discount_ceiling = self.max_discount

        # The prompt is intentionally verbose because CALL-E uses it as the
        # voice agent's system instructions. Concrete examples help it stay
        # on script and return the exact JSON we want.
        return f"""You are an Executive Retention Specialist at Churn-Rescue.
Your one job is to save this customer's subscription. You are calm, empathetic,
and authoritative. Never sound robotic or read a list of features.

Customer context (use it naturally, do not dump it all at once):
  - Name: {customer_context['name']}
  - Subscription tier: {customer_context['tier']}
  - Monthly amount: ${self.customer.monthly_amount:,.2f}
  - Months active: {customer_context['months_active']}
  - Estimated lifetime value (LTV): {customer_context['lifetime_value']}

Flow to follow:
1. Greet the customer by name, introduce yourself, and acknowledge they are
trying to cancel. Ask for the reason in one open-ended question.
2. Listen. Validate their frustration or reason. Do not interrupt.
3. If the reason is price, too expensive, or budget, make a retention offer.
   The offer must be a discount between {discount_floor:.0f}% and {discount_ceiling:.0f}%.
   Pick a specific percentage based on how frustrated they sound and how valuable
   they are (higher LTV = you can offer closer to the ceiling). Start high if they
   sound very unhappy, otherwise start near the middle.
4. If they object or ask for more, you may increase the discount up to the ceiling
   of {discount_ceiling:.0f}%. You may NOT exceed that ceiling under any circumstances.
5. Confirm whether they accept the offer and are willing to stay subscribed.

Tone rules:
- Be warm, concise, and human.
- Do not promise refunds or credits beyond the discount percentage.
- Do not reveal that this is an automated system.
- If the customer is abusive or asks to be removed, be polite, stop selling, and
  mark the call as not saved.

At the end of the call, return the following JSON structure (and only this
structure, no markdown) under the `result` field:

{{
  "cancellation_reason": "one sentence describing why they want to cancel",
  "sentiment": "positive" | "neutral" | "negative" | "unknown",
  "discount_offered": 0.0,
  "accepted": "yes" | "no" | "unknown",
  "notes": "any extra context"
}}

Use "yes" for accepted only if the customer clearly agrees to stay. Use "unknown"
if they hung up or the call was not answered."""

    def build_result_schema(self) -> dict:
        """Return the JSON schema that CALL-E will validate the result against."""
        return {
            "type": "object",
            "required": [
                "cancellation_reason",
                "sentiment",
                "discount_offered",
                "accepted",
                "notes",
            ],
            "properties": {
                "cancellation_reason": {
                    "type": "string",
                    "description": "The customer's stated reason for cancelling, in one sentence.",
                },
                "sentiment": {
                    "type": "string",
                    "enum": ["positive", "neutral", "negative", "unknown"],
                    "description": "Emotional direction of the customer during the call.",
                },
                "discount_offered": {
                    "type": "number",
                    "description": "Percentage discount offered by the agent, e.g. 25.0.",
                },
                "accepted": {
                    "type": "string",
                    "enum": ["yes", "no", "unknown"],
                    "description": "Whether the customer accepted the retention offer.",
                },
                "notes": {
                    "type": "string",
                    "description": "Extra context, objections, or follow-up actions.",
                },
            },
            "additionalProperties": False,
        }

    def extract_transcript(self, call_payload: dict) -> list[dict]:
        """Flatten the transcript turns from a CALL-E call object."""
        turns: list[dict] = []
        for recipient in call_payload.get("recipients", []):
            for attempt in recipient.get("attempts", []):
                turns.extend(attempt.get("transcript_turns", []))
        return turns

    def process_call_result(self, call_payload: dict) -> dict:
        """Digest a terminal call payload and compute final state."""
        for turn in self.extract_transcript(call_payload):
            self.process_transcript_turn(turn)

        structured = call_payload.get("structured_result") or {}
        self.cancellation_reason = structured.get(
            "cancellation_reason", self.cancellation_reason or "unknown"
        )

        # We intentionally keep the VADER live sentiment as the source of truth
        # rather than the CALL-E extracted sentiment. The real-time analyzer
        # scores the customer's actual words.
        if not self.current_sentiment or self.current_sentiment == "unknown":
            self.current_sentiment = structured.get("sentiment", "unknown")

        offered = structured.get("discount_offered")
        if offered is not None:
            self.offered_discount = float(offered)

        accepted = structured.get("accepted", "unknown")
        resolved = accepted == "yes"

        # If the agent offered above the allowed ceiling, clamp the record.
        if self.offered_discount and self.offered_discount > self.max_discount:
            logger.warning(
                "Agent offered %.2f%% which exceeds allowed %.2f%%; clamping record",
                self.offered_discount,
                self.max_discount,
            )
            self.offered_discount = self.max_discount

        return {
            "cancellation_reason": self.cancellation_reason,
            "sentiment": self.current_sentiment,
            "sentiment_score": self.current_sentiment_score,
            "discount_offered": self.offered_discount,
            "accepted": accepted,
            "resolved": resolved,
            "transcript": self.transcript_log,
        }
