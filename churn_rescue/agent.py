"""Retention call FSM. One instance = one call.

IDLE -> ENGAGE_LISTEN -> COUNTER_INTEL -> INCENTIVE_AUTH -> OMNICHANNEL_CLOSE

Public methods return event dicts; server.py broadcasts them over /ws.
"""
from __future__ import annotations

import json
import re
import uuid
from enum import Enum
from pathlib import Path
from typing import Any

from .contracts import render_addendum
from .db import Customer
from .llm import SYSTEM_PROMPT, groq_reply, llm_available
from .osint import fetch_company_brief

BATTLECARDS_PATH = Path(__file__).resolve().parent / "battlecards.json"

MAX_DISCOUNT_PCT = 25.0


class AgentState(str, Enum):
    IDLE = "IDLE"
    ENGAGE_LISTEN = "ENGAGE_LISTEN"
    COUNTER_INTEL = "COUNTER_INTEL"
    INCENTIVE_AUTH = "INCENTIVE_AUTH"
    OMNICHANNEL_CLOSE = "OMNICHANNEL_CLOSE"
    HUMAN_TAKEOVER = "HUMAN_TAKEOVER"


# sentiment at or below this flags the call for manual override
CRITICAL_CHURN_THRESHOLD = -0.90


# lexicon sentiment, no external NLP dep
_POSITIVE = {
    "love", "like", "enjoy", "happy", "great", "good", "excellent", "amazing",
    "awesome", "satisfied", "perfect", "fine", "okay", "yes", "sure",
    "absolutely", "definitely", "willing", "interested", "appreciate",
    "grateful", "helpful", "fair", "reasonable", "thanks", "thank",
}
_NEGATIVE = {
    "hate", "dislike", "angry", "frustrated", "terrible", "awful", "horrible",
    "bad", "worst", "disappointed", "annoyed", "pissed", "upset", "unhappy",
    "poor", "useless", "broken", "slow", "expensive", "overpriced", "cancel",
    "canceling", "cancelling", "quit", "leave", "leaving", "refund", "scam",
    "garbage", "worthless", "fail", "failed", "wrong", "unacceptable",
    "ridiculous", "joke", "nightmare", "sick", "tired", "fed",
}
_INTENSIFIERS = {
    "very", "really", "extremely", "incredibly", "so", "totally",
    "absolutely", "completely", "utterly", "highly", "super", "freaking",
}


def score_sentiment(text: str) -> float:
    """[-1.0, 1.0]; handles negation + intensifiers."""
    tokens = re.findall(r"[a-zA-Z']+", text.lower())
    score, negate, boost = 0.0, 1.0, 1.0
    for tok in tokens:
        if tok in _INTENSIFIERS:
            boost = 1.5
            continue
        if tok in {"not", "no", "never", "don't", "doesn't", "didn't", "isn't",
                   "aren't", "wasn't", "won't", "can't", "cannot"}:
            negate = -1.0
            continue
        if tok in _POSITIVE:
            score += 0.35 * negate * boost
        elif tok in _NEGATIVE:
            score -= 0.45 * negate * boost
        negate, boost = 1.0, 1.0
    return round(max(-1.0, min(1.0, score)), 3)


def sentiment_label(score: float) -> str:
    if score >= 0.15:
        return "positive"
    if score <= -0.15:
        return "negative"
    return "neutral"


# intent patterns
_CANCEL_RE = re.compile(
    r"\b(cancel|cancelling|canceling|leav(e|ing)|switch(ing)?|quit|"
    r"unsubscribe|moving to|walk(ing)? away|done with|end(ing)? (our|the|my) "
    r"(contract|subscription|account))\b",
    re.IGNORECASE,
)
_PRICE_RE = re.compile(
    r"\b(too (expensive|much|pricey)|expensive|pricey|overpriced|cheaper|"
    r"price|pricing|cost(s|ing)?|budget|afford|discount|bill|invoice|"
    r"paying too much|not enough|better offer|more)\b",
    re.IGNORECASE,
)
_ACCEPT_RE = re.compile(
    r"\b(deal|accept(ed)?|i'?ll (take|stay|keep)|sounds (good|fair|great)|"
    r"let'?s do it|sign me up|okay[, ]*(i'?ll|let'?s|fine)|alright|"
    r"you'?ve got a deal|that works|works for me|i'?m in|agreed)\b",
    re.IGNORECASE,
)
_REJECT_RE = re.compile(
    r"\b(still (want|going|need) to (cancel|leave|switch)|just cancel|"
    r"no thanks|not interested|don'?t bother|forget it|goodbye|"
    r"too late|made up my mind|final answer)\b",
    re.IGNORECASE,
)

# named vendor after a churn verb: "moving to Zenith", "Acme quoted us"
_UNKNOWN_AFTER = re.compile(
    r"\b(?:moving|switching|migrat\w*|going|leaving|choosing|signing|"
    r"rolling out|moving over)\s+(?:over\s+)?(?:to|for|with)\s+"
    r"([A-Za-z][\w-]{2,})\b",
    re.IGNORECASE,
)
_UNKNOWN_BEFORE = re.compile(
    r"\b([A-Za-z][\w-]{2,})\s+(?:quoted|offered|promised|pitched|demoed)\b",
    re.IGNORECASE,
)
_NOT_VENDORS = {
    "the", "our", "their", "your", "another", "someone", "something",
    "somewhere", "else", "cheaper", "better", "them", "a", "an", "it",
}


def _load_battlecards() -> dict[str, dict[str, Any]]:
    with open(BATTLECARDS_PATH, encoding="utf-8") as f:
        return json.load(f)


class RetentionAgent:
    """Drives one retention call end-to-end."""

    def __init__(self, customer: Customer):
        self.customer = customer
        self.state = AgentState.IDLE
        self.call_active = False
        self.sentiment = 0.0
        self.transcript: list[dict[str, str]] = []
        self.transitions: list[dict[str, str]] = []
        self.battlecards = _load_battlecards()
        self.countered_competitors: set[str] = set()
        self.authorized_discount = self._authorize_discount()
        self.offer_levels = self._build_offer_ladder()
        self.offer_index = -1
        self.current_offer: float | None = None
        self.outcome: str | None = None
        self.dispatch: dict[str, Any] | None = None
        self.critical_flagged = False
        self.human_override = False
        self.contracts_dir: Path | None = None
        self.min_sentiment = 0.0
        self.osint_targets: list[str] = []

    # incentive math
    def _authorize_discount(self) -> float:
        """10% base + LTV boost ($5k -> +1%, cap +10) + risk boost (cap +5),
        hard-capped at 25%."""
        ltv_boost = min(10.0, max(0.0, self.customer.ltv) / 5000.0)
        risk_boost = min(5.0, max(0.0, self.customer.churn_risk_score) * 5.0)
        return round(min(MAX_DISCOUNT_PCT, 10.0 + ltv_boost + risk_boost), 1)

    def _build_offer_ladder(self) -> list[float]:
        """Open conservative, close at the ceiling."""
        cap = self.authorized_discount
        ladder = sorted({round(cap * f, 1) for f in (0.6, 0.8, 1.0)})
        return [x for x in ladder if x > 0]

    # event helpers
    def _ev_state(self, old: AgentState) -> dict[str, Any]:
        self.transitions.append({"from": old.value, "to": self.state.value})
        return {"type": "state_change", "from": old.value, "to": self.state.value}

    def _goto(self, new: AgentState, events: list[dict[str, Any]]) -> None:
        old = self.state
        self.state = new
        events.append(self._ev_state(old))

    def _ev_agent(self, text: str) -> dict[str, Any]:
        self.transcript.append({"speaker": "agent", "text": text})
        return {"type": "transcript", "speaker": "agent", "text": text}

    # groq LPU generation; every dynamic line keeps its static twin as
    # the fallback for missing keys / api errors
    def _llm_messages(self, hint: str) -> list[dict[str, str]]:
        c = self.customer
        ctx = (
            f" Account: {c.company_name} ({c.plan} plan, ${c.mrr:,.0f}/mo, "
            f"contract ends {c.contract_end_date}). Competitor threat: "
            f"{c.competitor_threat or 'none'}. Hard discount ceiling: "
            f"{self.authorized_discount:.0f}%; live offer on the table: "
            f"{(self.current_offer or 0):.0f}%."
        )
        msgs = [{"role": "system", "content": SYSTEM_PROMPT + ctx}]
        for t in self.transcript[-8:]:
            msgs.append({
                "role": "assistant" if t["speaker"] == "agent" else "user",
                "content": t["text"],
            })
        msgs.append({"role": "user", "content": f"[director note: {hint}]"})
        return msgs

    def _ev_dynamic(self, hint: str, fallback: str) -> dict[str, Any]:
        reply = groq_reply(self._llm_messages(hint))
        return self._ev_agent(reply or fallback)

    def start_call(self) -> list[dict[str, Any]]:
        """IDLE -> ENGAGE_LISTEN; emits the greeting the TTS layer speaks."""
        events: list[dict[str, Any]] = []
        self.call_active = True
        self._goto(AgentState.ENGAGE_LISTEN, events)
        c = self.customer
        events.append(self._ev_agent(
            f"Hi {c.contact_name.split()[0]}, this is Maya from your dedicated "
            f"success team. I saw the cancellation request come through for "
            f"{c.company_name} and I wanted to reach out personally before "
            f"anything was finalized. What's driving the decision?"
        ))
        return events

    def handle_utterance(self, text: str) -> list[dict[str, Any]]:
        """One customer utterance in, events out."""
        events: list[dict[str, Any]] = []
        if not self.call_active:
            return events

        self.transcript.append({"speaker": "customer", "text": text})
        events.append({"type": "transcript", "speaker": "customer", "text": text})

        self.sentiment = score_sentiment(text)
        self.min_sentiment = min(self.min_sentiment, self.sentiment)
        events.append({
            "type": "sentiment",
            "score": self.sentiment,
            "label": sentiment_label(self.sentiment),
        })

        # trip the manual-override flag once; UI flashes the button
        if (not self.critical_flagged
                and self.sentiment <= CRITICAL_CHURN_THRESHOLD
                and not self.human_override):
            self.critical_flagged = True
            events.append({"type": "critical_churn", "score": self.sentiment})

        if self.state != AgentState.ENGAGE_LISTEN:
            return events

        competitor = self._detect_competitor(text)
        wants_cancel = bool(_CANCEL_RE.search(text))
        price_objection = bool(_PRICE_RE.search(text))
        accepts = bool(_ACCEPT_RE.search(text))
        rejects = bool(_REJECT_RE.search(text))
        offer_active = self.current_offer is not None

        # competitor first -- known battlecard, else live OSINT on an
        # unfamiliar vendor name, else the generic card
        unknown = self._detect_unknown_competitor(text)
        if (competitor and competitor != "_generic"
                and competitor not in self.countered_competitors):
            self._counter_intel(competitor, events)
        elif unknown and unknown not in self.countered_competitors:
            self._osint_intel(unknown, events)
        elif competitor and competitor not in self.countered_competitors:
            self._counter_intel(competitor, events)

        # hard "no" while an offer is live kills the call
        if rejects and offer_active:
            events.append(self._ev_dynamic(
                "They firmly rejected the offer. Close the call gracefully "
                "and leave the door open.",
                "Understood -- I'm sorry we couldn't change your mind today. "
                "Your feedback goes straight to our product leadership, and "
                "the door stays open if anything changes. Thank you for your "
                "time with us."
            ))
            self._end_call("lost", events)
            return events

        # yes + live offer -> close it
        if accepts and offer_active:
            self._omnichannel_close(events)
            return events

        # cancel/price pressure, or a warm yes with no offer yet -> authorize
        if wants_cancel or price_objection or (accepts and not offer_active):
            self._incentive_auth(events)
            return events

        # nothing actionable -- probe
        if competitor:  # named but already countered
            events.append(self._ev_dynamic(
                "They raised the competitor again after your counter. Ask "
                "what single thing would make staying an easy decision.",
                "Fair point. Beyond the platform comparison, what's the one "
                "thing that would make staying an easy decision?"
            ))
        elif accepts:  # offer_active guard above makes this a safety net
            self._incentive_auth(events)
        else:
            events.append(self._ev_dynamic(
                "No clear objection yet. Ask a short open question to draw "
                "out what is driving the cancellation.",
                self._probe_line()
            ))
        return events

    def takeover(self) -> list[dict[str, Any]]:
        """Operator hits MANUAL OVERRIDE -> HUMAN_TAKEOVER, bridge line, back
        to ENGAGE_LISTEN with the human (VP) driving."""
        events: list[dict[str, Any]] = []
        if not self.call_active:
            return events
        self.human_override = True
        self._goto(AgentState.HUMAN_TAKEOVER, events)
        events.append(self._ev_agent(
            "I completely understand. Let me bridge in our VP of Sales "
            "right now to resolve this."
        ))
        events.append({"type": "takeover", "by": "vp_of_sales"})
        self._goto(AgentState.ENGAGE_LISTEN, events)
        return events

    def end_summary(self) -> dict[str, Any]:
        return {
            "customer_id": self.customer.customer_id,
            "outcome": self.outcome,
            "discount_pct": self.current_offer,
            "states_visited": sorted({t["from"] for t in self.transitions}
                                     | {t["to"] for t in self.transitions}),
            "transcript": self.transcript,
        }

    # state behaviors
    def _counter_intel(self, key: str, events: list[dict[str, Any]]) -> None:
        self._goto(AgentState.COUNTER_INTEL, events)
        card = self.battlecards.get(key) or self.battlecards["_generic"]
        self.countered_competitors.add(key)
        events.append({
            "type": "battlecard",
            "competitor": card["competitor"],
            "weakness": card["weakness"],
            "pricing_angle": card["pricing_angle"],
            "proof_points": card["proof_points"],
        })
        events.append(self._ev_dynamic(
            f"Counter their {card['competitor']} objection in their own "
            f"words. Intel: {card['weakness']} Pricing angle: "
            f"{card['pricing_angle']}",
            card["rebuttal"]
        ))
        self._goto(AgentState.ENGAGE_LISTEN, events)

    def _incentive_auth(self, events: list[dict[str, Any]]) -> None:
        self._goto(AgentState.INCENTIVE_AUTH, events)
        if self.offer_index < len(self.offer_levels) - 1:
            self.offer_index += 1
            self.current_offer = self.offer_levels[self.offer_index]
            c = self.customer
            new_mrr = c.mrr * (1 - self.current_offer / 100.0)
            events.append({
                "type": "offer",
                "discount_pct": self.current_offer,
                "authorized_max": self.authorized_discount,
                "mrr_before": c.mrr,
                "mrr_after": round(new_mrr, 2),
            })
            events.append(self._ev_dynamic(
                f"Offer them exactly {self.current_offer:.0f}% off -- "
                f"${c.mrr:,.0f} down to ${new_mrr:,.0f}/mo through "
                f"{c.contract_end_date}. Ask if that works and mention you "
                f"can text the checkout link now.",
                f"Here's what I'm authorized to do for you: a {self.current_offer:.0f}% "
                f"retention credit on your {c.plan} plan -- that takes you from "
                f"${c.mrr:,.0f} to ${new_mrr:,.0f} a month, locked in through "
                f"{c.contract_end_date}. I can send the checkout link to your "
                f"phone right now. Does that work?"
            ))
        else:
            events.append(self._ev_dynamic(
                f"You are at the hard ceiling of {self.authorized_discount:.0f}%. "
                f"Tell them firmly but warmly this is the strongest offer "
                f"finance authorized.",
                f"I wish I could go further, but {self.authorized_discount:.0f}% is "
                f"the hard ceiling finance has authorized for your account -- "
                f"it's the strongest offer I can put on the table."
            ))
        self._goto(AgentState.ENGAGE_LISTEN, events)

    def _omnichannel_close(self, events: list[dict[str, Any]]) -> None:
        self._goto(AgentState.OMNICHANNEL_CLOSE, events)
        c = self.customer
        discount = self.current_offer or self.authorized_discount
        self.current_offer = discount
        stripe_url = (
            f"https://checkout.stripe.com/c/pay/cs_sim_retention_"
            f"{c.customer_id}_{int(round(discount))}pct"
        )
        pdf = render_addendum(c, discount, out_dir=self.contracts_dir) \
            if self.contracts_dir else render_addendum(c, discount)
        whatsapp_body = (
            f"Hi {c.contact_name.split()[0]} - thanks for staying with us! "
            f"Your {discount:.0f}% retention credit is confirmed for "
            f"{c.company_name} ({c.plan} plan, through {c.contract_end_date}). "
            f"Complete it securely here: {stripe_url} "
            f"-- signed addendum attached as PDF."
        )
        self.dispatch = {
            "stripe_checkout_url": stripe_url,
            "pdf": pdf,
            "whatsapp": {
                "channel": "whatsapp",
                "to": c.phone,
                "template": "retention_offer_v1",
                "body": whatsapp_body,
                "message_id": f"wamid.simulated.{uuid.uuid4().hex[:20]}",
                "status": "queued",
            },
        }
        events.append({"type": "dispatch", **self.dispatch})
        events.append(self._ev_dynamic(
            f"They accepted. Confirm the {discount:.0f}% credit checkout "
            f"link and PDF addendum are on their phone via WhatsApp, and "
            f"thank {c.contact_name.split()[0]} by name.",
            f"Done -- I've just sent a secure checkout link with your "
            f"{discount:.0f}% credit to your phone on WhatsApp. It takes "
            f"thirty seconds to confirm and your service continues "
            f"uninterrupted. Thank you for giving us the chance to make "
            f"this right, {c.contact_name.split()[0]}."
        ))
        self._end_call("saved", events)

    def _end_call(self, outcome: str, events: list[dict[str, Any]]) -> None:
        self.outcome = outcome
        self.call_active = False
        self._goto(AgentState.IDLE, events)
        events.append({
            "type": "call_ended",
            "outcome": outcome,
            "discount_pct": self.current_offer,
        })

    def _osint_intel(self, name: str, events: list[dict[str, Any]]) -> None:
        """Unknown vendor -> COUNTER_INTEL via live web scrape + Groq."""
        self._goto(AgentState.COUNTER_INTEL, events)
        self.countered_competitors.add(name)
        self.osint_targets.append(name)
        events.append({"type": "osint", "competitor": name,
                       "status": "scraping"})
        brief = fetch_company_brief(name) if llm_available() else None
        events.append({"type": "osint", "competitor": name,
                       "status": "done" if brief else "offline",
                       "snippet": (brief or "")[:160]})
        hint = (
            f"They named {name}, a competitor with no battlecard on file. "
            f"Say you're pulling up {name}'s live pricing right now, then "
            f"pivot to our consolidation value."
        )
        if brief:
            hint += f" Live intel on {name}: {brief}"
        events.append(self._ev_dynamic(
            hint,
            f"I'm pulling up {name}'s live pricing right now -- while that "
            f"loads, here's what I can tell you: consolidating on us removes "
            f"the double-stack cost entirely, and your team's workflows stay "
            f"exactly where they are."
        ))
        self._goto(AgentState.ENGAGE_LISTEN, events)

    # utterance classification
    def _detect_competitor(self, text: str) -> str | None:
        lower = text.lower()
        for key in self.battlecards:
            if key != "_generic" and key in lower:
                return key
        if re.search(r"\b(competitor|alternative|another (tool|platform|vendor)|"
                     r"the other guys)\b", lower):
            return "_generic"
        return None

    def _detect_unknown_competitor(self, text: str) -> str | None:
        """A vendor name with no battlecard -- fresh OSINT target."""
        for rx in (_UNKNOWN_AFTER, _UNKNOWN_BEFORE):
            m = rx.search(text)
            if m:
                name = m.group(1)
                if (name.lower() not in _NOT_VENDORS
                        and name.lower() not in self.battlecards):
                    return name.title()
        return None

    def _probe_line(self) -> str:
        probes = [
            "I hear you. Can you tell me a bit more about what's not working?",
            "That's helpful context. Is it mainly about the product, the "
            "pricing, or how the team is using it?",
            "Understood. If we could fix the top one or two things, would "
            "that change the picture?",
        ]
        n = len([t for t in self.transcript if t["speaker"] == "agent"])
        return probes[min(n, len(probes) - 1)]
