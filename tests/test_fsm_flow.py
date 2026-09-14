"""Headless full-call sim: asserts 100% FSM state + transition coverage.

Run:  python -m unittest tests.test_fsm_flow -v
"""
from __future__ import annotations

import asyncio
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from churn_rescue import llm, stt
from churn_rescue.agent import AgentState, RetentionAgent, MAX_DISCOUNT_PCT
from churn_rescue.db import Customer


def make_customer(**overrides) -> Customer:
    base = dict(
        customer_id="acct_test",
        company_name="Northwind Traders",
        contact_name="Sarah Chen",
        phone="+14155550101",
        plan="Enterprise",
        mrr=4200.0,
        churn_risk_score=0.92,
        ltv=148000.0,
        contract_end_date="2026-10-01",
        competitor_threat="Salesforce",
    )
    base.update(overrides)
    return Customer(**base)


def states_seen(agent: RetentionAgent) -> set[str]:
    out = set()
    for t in agent.transitions:
        out.add(t["from"])
        out.add(t["to"])
    return out


def events_of(events: list[dict], etype: str) -> list[dict]:
    return [e for e in events if e.get("type") == etype]


class TestAngryCustomerFlow(unittest.TestCase):
    """fury -> battlecards -> escalation -> save."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.agent = RetentionAgent(make_customer())
        self.agent.contracts_dir = Path(self._tmp.name)
        # pin the no-key env so tests always exercise the static fallback
        self._env = {k: os.environ.pop(k, None)
                     for k in ("GROQ_API_KEY", "ASSEMBLYAI_API_KEY")}

    def tearDown(self) -> None:
        self._tmp.cleanup()
        for k, v in self._env.items():
            if v is not None:
                os.environ[k] = v

    def test_full_save_flow(self) -> None:
        agent = self.agent

        # IDLE -> ENGAGE_LISTEN, greeting emitted
        events = agent.start_call()
        self.assertEqual(agent.state, AgentState.ENGAGE_LISTEN)
        self.assertTrue(agent.call_active)
        greeting = events_of(events, "transcript")[0]
        self.assertEqual(greeting["speaker"], "agent")
        self.assertIn("Northwind Traders", greeting["text"])

        # utt 1: rage + competitor + price -> COUNTER_INTEL -> INCENTIVE_AUTH
        events = agent.handle_utterance(
            "I'm furious. We're canceling and moving to Salesforce - "
            "your pricing is a joke and the product is slow. "
            "Absolute garbage, a total nightmare, completely useless."
        )
        visited = [e["to"] for e in events_of(events, "state_change")]
        self.assertIn("COUNTER_INTEL", visited)
        self.assertIn("INCENTIVE_AUTH", visited)
        self.assertEqual(agent.state, AgentState.ENGAGE_LISTEN)

        card = events_of(events, "battlecard")[0]
        self.assertEqual(card["competitor"], "Salesforce")
        self.assertIn("implementation", card["weakness"].lower())

        offer1 = events_of(events, "offer")[0]
        self.assertLessEqual(offer1["discount_pct"], MAX_DISCOUNT_PCT)
        self.assertLess(offer1["discount_pct"], offer1["authorized_max"])

        sent = events_of(events, "sentiment")[0]
        self.assertLess(sent["score"], 0.0, "angry utterance must score negative")

        # sentiment below -0.90 must trip the critical-churn flag
        crit = events_of(events, "critical_churn")
        self.assertEqual(len(crit), 1)
        self.assertLessEqual(crit[0]["score"], -0.90)

        # operator hits MANUAL OVERRIDE -> HUMAN_TAKEOVER -> back to listening
        events = agent.takeover()
        visited = [e["to"] for e in events_of(events, "state_change")]
        self.assertIn("HUMAN_TAKEOVER", visited)
        self.assertEqual(visited[-1], "ENGAGE_LISTEN")
        self.assertTrue(agent.human_override)

        # utt 2: lowball rejection -> escalate
        events = agent.handle_utterance(
            "That's not enough. It's still too expensive."
        )
        offer2 = events_of(events, "offer")[0]
        self.assertGreater(offer2["discount_pct"], offer1["discount_pct"])
        self.assertEqual(agent.state, AgentState.ENGAGE_LISTEN)

        # utt 3: second competitor + push to the cap
        events = agent.handle_utterance(
            "HubSpot quoted us way cheaper. You need to do better than that."
        )
        card2 = events_of(events, "battlecard")[0]
        self.assertEqual(card2["competitor"], "HubSpot")
        offer3 = events_of(events, "offer")[0]
        self.assertEqual(offer3["discount_pct"], offer3["authorized_max"])
        self.assertLessEqual(offer3["authorized_max"], MAX_DISCOUNT_PCT)

        # utt 4: accepts -> OMNICHANNEL_CLOSE -> IDLE
        events = agent.handle_utterance("Fine, I'll take the deal.")
        visited = [e["to"] for e in events_of(events, "state_change")]
        self.assertIn("OMNICHANNEL_CLOSE", visited)
        self.assertEqual(agent.state, AgentState.IDLE)
        self.assertFalse(agent.call_active)
        self.assertEqual(agent.outcome, "saved")

        dispatch = events_of(events, "dispatch")[0]
        self.assertIn("checkout.stripe.com", dispatch["stripe_checkout_url"])
        pdf = dispatch["pdf"]
        self.assertTrue(pdf["filename"].endswith(".pdf"))
        self.assertTrue(Path(pdf["path"]).exists())
        self.assertTrue(Path(pdf["path"]).read_bytes().startswith(b"%PDF-1.4"))
        wa = dispatch["whatsapp"]
        self.assertEqual(wa["to"], "+14155550101")
        self.assertIn(str(int(round(offer3["discount_pct"]))), wa["body"])
        self.assertTrue(wa["message_id"].startswith("wamid.simulated."))

        ended = events_of(events, "call_ended")[0]
        self.assertEqual(ended["outcome"], "saved")

        # 100% FSM coverage
        seen = states_seen(agent)
        expected = {s.value for s in AgentState}
        self.assertEqual(seen, expected, f"missing states: {expected - seen}")

        transitions = {(t["from"], t["to"]) for t in agent.transitions}
        required = {
            ("IDLE", "ENGAGE_LISTEN"),
            ("ENGAGE_LISTEN", "COUNTER_INTEL"),
            ("COUNTER_INTEL", "ENGAGE_LISTEN"),
            ("ENGAGE_LISTEN", "INCENTIVE_AUTH"),
            ("INCENTIVE_AUTH", "ENGAGE_LISTEN"),
            ("ENGAGE_LISTEN", "HUMAN_TAKEOVER"),
            ("HUMAN_TAKEOVER", "ENGAGE_LISTEN"),
            ("ENGAGE_LISTEN", "OMNICHANNEL_CLOSE"),
            ("OMNICHANNEL_CLOSE", "IDLE"),
        }
        self.assertTrue(
            required.issubset(transitions),
            f"missing transitions: {required - transitions}",
        )

    def test_lost_call_path(self) -> None:
        agent = RetentionAgent(make_customer())
        agent.start_call()
        agent.handle_utterance("I want to cancel, it's too expensive.")
        self.assertIsNotNone(agent.current_offer)

        events = agent.handle_utterance("No thanks, just cancel. Final answer.")
        ended = events_of(events, "call_ended")[0]
        self.assertEqual(ended["outcome"], "lost")
        self.assertEqual(agent.state, AgentState.IDLE)
        self.assertFalse(agent.call_active)

    def test_discount_authorization_math(self) -> None:
        big = RetentionAgent(make_customer(ltv=148000.0, churn_risk_score=0.92))
        small = RetentionAgent(make_customer(ltv=4300.0, churn_risk_score=0.38))
        self.assertLessEqual(big.authorized_discount, MAX_DISCOUNT_PCT)
        self.assertLess(small.authorized_discount, big.authorized_discount)
        self.assertGreaterEqual(small.authorized_discount, 10.0)

    def test_generic_competitor_fallback(self) -> None:
        agent = RetentionAgent(make_customer(competitor_threat=None))
        agent.start_call()
        events = agent.handle_utterance(
            "We're evaluating a competitor and probably canceling."
        )
        card = events_of(events, "battlecard")[0]
        self.assertEqual(card["competitor"], "a competitor")

    def test_idle_agent_ignores_utterances(self) -> None:
        agent = RetentionAgent(make_customer())
        events = agent.handle_utterance("hello?")
        self.assertEqual(events, [])
        self.assertEqual(agent.state, AgentState.IDLE)


class TestProviderFallbacks(unittest.TestCase):
    """V4: Groq/AssemblyAI must degrade silently without keys or on errors."""

    def setUp(self) -> None:
        self._env = {k: os.environ.pop(k, None)
                     for k in ("GROQ_API_KEY", "ASSEMBLYAI_API_KEY")}

    def tearDown(self) -> None:
        for k, v in self._env.items():
            if v is not None:
                os.environ[k] = v

    def test_groq_none_without_key(self) -> None:
        self.assertFalse(llm.llm_available())
        self.assertIsNone(
            llm.groq_reply([{"role": "user", "content": "hi"}]))

    def test_groq_none_on_api_error(self) -> None:
        os.environ["GROQ_API_KEY"] = "sk-bad"
        self.assertTrue(llm.llm_available())
        with mock.patch.object(
                llm.urllib.request, "urlopen", side_effect=OSError("down")):
            self.assertIsNone(
                llm.groq_reply([{"role": "user", "content": "hi"}]))

    def test_dynamic_line_uses_static_fallback(self) -> None:
        agent = RetentionAgent(make_customer())
        agent.start_call()
        ev = agent._ev_dynamic("say anything", "STATIC-LINE")
        self.assertEqual(ev["type"], "transcript")
        self.assertEqual(ev["text"], "STATIC-LINE")

    def test_stt_none_without_key(self) -> None:
        self.assertFalse(stt.stt_available())
        self.assertFalse(asyncio.run(stt.AssemblyStream().connect()))


if __name__ == "__main__":
    unittest.main(verbosity=2)
