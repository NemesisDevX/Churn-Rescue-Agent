# Churn-Rescue-Agent

**Autonomous Revenue Protection at machine speed.** Churn-Rescue-Agent is a
zero-latency voice AI platform that detects cancellation risk, dials a
**concurrent swarm** of retention calls, reasons on a Groq LPU in
sub-500ms, counters competitor objections with live OSINT intel, negotiates
LTV-capped incentives, and closes the save with a signed PDF addendum +
Stripe checkout pushed to the customer's phone — *before they hang up*.

Every minute of churn risk left undialed is ARR walking out the door. This
agent turns "cancellation request received" into "retention offer signed"
in a single phone call — and briefs your CEO on exactly which competitors
are attacking your base when the swarm lands.

Pure Python + vanilla HTML/JS/CSS. **No Node.js. No build step.**

## System Blueprint

```mermaid
flowchart TB
    subgraph OPS["🖥 MISSION CONTROL — vanilla JS dashboard"]
        direction LR
        ROSTER["At-Risk Roster<br/>10 enterprise accounts<br/>risk-sorted"]
        SWARM{{"⚡ SWARM DIAL"}}
        COL1["Call Column 1<br/>FSM · sentiment · transcript"]
        COL2["Call Column 2<br/>FSM · sentiment · transcript"]
        COL3["Call Column 3<br/>FSM · sentiment · transcript"]
        OVR["⚠ MANUAL OVERRIDE<br/>critical-churn button"]
        PHONE["📱 iPhone Mockup<br/>WhatsApp + PDF + Stripe CTA"]
        CEO["Post-Call Strategic<br/>Insights overlay<br/>ARR · win-rate · threat matrix"]
        TOAST["SYSTEM: Live OSINT<br/>toast"]
    end

    subgraph EDGE["🌐 ASGI EDGE — Starlette + Uvicorn :8000"]
        REST["/api/customers · /api/calls/*"]
        WSS["/ws — JSON telemetry bus<br/>+ PCM16 audio uplink<br/>+ capabilities handshake"]
        FILES["/contracts — signed PDF serving"]
        AGG["swarm_complete aggregator<br/>ARR · retention · threat matrix"]
    end

    subgraph BRAIN["🧠 REASONING TIER"]
        FSM1["RetentionAgent FSM #1"]
        FSM2["RetentionAgent FSM #2"]
        FSM3["RetentionAgent FSM #3"]
        GROQ["Groq LPU<br/>llama3-70b-8192<br/>sub-500ms · same-language<br/>(EN / ES / FR / AR)"]
        OSINT["osint.py<br/>live web scrape<br/>unknown-vendor intel"]
        BC["battlecards.json<br/>Salesforce · HubSpot ·<br/>Linear · Acme"]
    end

    subgraph EARS["👂 REAL-TIME STT"]
        AAI["AssemblyAI realtime ws<br/>language_detection=true"]
        WSP["browser Web Speech<br/>(zero-dep fallback)"]
    end

    subgraph VOICE["🗣 EMOTIONAL TTS"]
        SYS["System.Speech<br/>sentiment→rate/volume<br/>angry: −2/85 · close: +1/100"]
        WEBSPK["speechSynthesis<br/>(live dashboard voice)"]
    end

    subgraph CLOSE["💰 OMNICHANNEL CLOSE"]
        PDF["contracts.py<br/>zero-dep PDF writer<br/>Subscription Addendum"]
        STRIPE["Stripe Retention<br/>Checkout URL"]
        WA["WhatsApp dispatch<br/>wamid.simulated.*"]
    end

    DB[("SQLite CRM<br/>customers · call_log")]

    ROSTER --> SWARM
    SWARM -->|concurrent| WSS
    WSS --> FSM1 & FSM2 & FSM3
    FSM1 & FSM2 & FSM3 -->|state_change / transcript /<br/>sentiment / offer events| WSS
    WSS --> COL1 & COL2 & COL3
    FSM1 & FSM2 & FSM3 -.->|sentiment ≤ −0.90| OVR
    OVR -->|override → HUMAN_TAKEOVER| WSS
    FSM1 & FSM2 & FSM3 -->|chat.completions via urllib| GROQ
    FSM1 & FSM2 & FSM3 -->|known vendor| BC
    FSM1 & FSM2 & FSM3 -->|unknown vendor| OSINT
    OSINT -.->|brief → Groq ctx| GROQ
    OSINT --> TOAST
    EARS -->|FinalTranscript| WSS
    FSM1 & FSM2 & FSM3 -->|agent lines| VOICE
    FSM1 & FSM2 & FSM3 -->|OMNICHANNEL_CLOSE| CLOSE
    PDF --> FILES --> PHONE
    STRIPE & WA --> PHONE
    FSM1 & FSM2 & FSM3 --> DB
    DB --> REST --> ROSTER
    WSS --> AGG --> CEO
```

## V4 provider layer (Groq + AssemblyAI)

## V4 provider layer (Groq + AssemblyAI)

Optional real-provider mode — set env keys and the same pipeline upgrades
in place; unset (or any API error) it falls back to the deterministic
static lines and browser Web Speech, so the demo never breaks.

| Piece | Provider | Module | Fallback |
| --- | --- | --- | --- |
| Brain (response gen) | Groq `llama3-70b-8192` via stdlib `urllib` → `api.groq.com/openai/v1/chat/completions` | `churn_rescue/llm.py` | Static FSM lines |
| Ears (STT) | AssemblyAI realtime `wss://api.assemblyai.com/v2/realtime/ws` (PCM16/16 kHz over `/ws`, `language_detection=true`) | `churn_rescue/stt.py` + `static/pcm-worklet.js` | Browser Web Speech API |
| OSINT (unknown vendors) | Wikipedia summary via stdlib `urllib`, injected into the Groq context | `churn_rescue/osint.py` | Static lookup line |
| Voice (TTS) | `System.Speech` with sentiment→rate/volume modulation (angry → −2/85, accepting → +1/100) | `churn_rescue/tts.py` + `speak()` in the dashboard | Neutral voice |

V5 extras: when the last live call resolves, the server broadcasts
`swarm_complete` — the dashboard dims into a *Post-Call Strategic Insights*
boardroom panel (ARR saved, retention rate, competitor threat matrix). A
vendor named with no battlecard triggers a glowing `SYSTEM: Live OSINT`
toast while Maya stalls for the scrape.

```powershell
$env:GROQ_API_KEY = "gsk_..."        # dynamic, same-language responses
$env:ASSEMBLYAI_API_KEY = "aai_..."  # real streaming STT instead of browser SR
python -m churn_rescue.server
```

On `/ws` connect the server announces `{"type":"capabilities","llm":…,"stt":…}`
and the dashboard picks its mic path automatically. Maya's Groq system prompt
forces same-language replies (English/Spanish/French/Arabic) under 2 sentences.

## Architecture

```text
┌─────────────────────────────────────────────────────────────────┐
│  Browser — Mission Control (churn_rescue/static/index.html)     │
│  roster │ 3x call columns │ FSM tracker │ sentiment │ phone     │
└───────────────┬───────────────────────────────▲─────────────────┘
                │ REST + WebSocket (JSON events)│ PCM16 audio
┌───────────────▼───────────────────────────────┴─────────────────┐
│  churn_rescue/server.py — Starlette ASGI app (127.0.0.1:8000)   │
│  /api/customers │ /api/calls/* │ /ws telemetry+stt │ /contracts │
└──────┬───────────────────┬──────────────────┬───────────────────┘
       │                   │                  │
┌──────▼───────┐   ┌───────▼───────┐   ┌──────▼───────────────────┐
│ agent.py     │   │ llm.py        │   │ stt.py                   │
│ RetentionAgent│──▶│ Groq urllib   │   │ AssemblyAI realtime      │
│ swarm FSMs   │   │ (fallback:    │   │ (fallback: Web Speech)   │
└──────┬───────┘   │ static lines) │   └──────────────────────────┘
       │           └───────────────┘
       │ reads
       ├── battlecards.json — Salesforce / HubSpot / Linear / Acme
       ├── contracts.py — zero-dep PDF addendum writer
       └── db.py — SQLite: customers, call_log
```

## The Voice FSM

| State | Behavior |
| --- | --- |
| `IDLE` | Awaiting trigger. `start_call()` arms the agent. |
| `ENGAGE_LISTEN` | Scores every utterance (−1.0…+1.0 sentiment), classifies intent: competitor / cancel / price / accept / reject. |
| `COUNTER_INTEL` | Competitor named → loads its battlecard from `battlecards.json` and voices the rebuttal with proof points. |
| `INCENTIVE_AUTH` | Computes the LTV-based discount ceiling and voices an escalating offer. |
| `OMNICHANNEL_CLOSE` | On acceptance: mints a mock Stripe Retention Checkout URL and queues a simulated WhatsApp webhook payload, then ends the call. |

### Incentive math (hard cap: 25%)

```text
authorized = min(25, 10 + min(10, ltv / 5000) + min(5, churn_risk_score * 5))
offer ladder = 60% → 80% → 100% of authorized ceiling
```

A $148k-LTV / 0.92-risk account authorizes ~24.6%; a $4k starter authorizes ~12%.
The agent opens at 60% of the ceiling and climbs rungs only under pressure —
margin is protected unless the account is genuinely about to walk.

## Call lifecycle

```mermaid
sequenceDiagram
    autonumber
    participant TRG as Churn Trigger<br/>(dashboard / webhook)
    participant UI as Mission Control UI<br/>(vanilla JS)
    participant WS as /ws telemetry<br/>(Starlette)
    participant API as REST API<br/>/api/calls/*
    participant CRM as SQLite CRM<br/>(customers, call_log)
    participant FSM as RetentionAgent<br/>FSM engine
    participant BC as battlecards.json<br/>(competitor intel)
    participant STT as Web Speech STT<br/>(browser)
    participant TTS as speechSynthesis<br/>(browser TTS)
    participant SP as Stripe Checkout<br/>(simulated)
    participant WA as WhatsApp webhook<br/>(simulated)

    Note over TRG,WA: ── PHASE 1: TRIGGER & PROFILE LOOKUP ──
    TRG->>API: POST /api/calls/start {customer_id}
    API->>CRM: SELECT account (company, plan, mrr,<br/>churn_risk_score, ltv, competitor_threat)
    CRM-->>API: Customer profile
    API->>CRM: UPDATE status = 'in_call'
    API->>FSM: RetentionAgent(customer)
    Note right of FSM: authorize ceiling:<br/>min(25%, 10 + LTV/5k + risk×5)
    API->>FSM: start_call()
    FSM-->>WS: state_change: IDLE → ENGAGE_LISTEN
    FSM-->>WS: transcript(agent): personalized greeting
    WS-->>UI: broadcast events to all dashboards
    UI->>TTS: speak(agent greeting)
    TTS-->>TRG: agent voice: "What's driving the decision?"

    Note over TRG,WA: ── PHASE 2: LISTEN / COUNTER / AUTHORIZE LOOP ──
    loop until accept, hard reject, or hangup
        TRG->>STT: customer speaks objection
        STT->>UI: interim + final transcript<br/>(fallback: typed input)
        UI->>WS: {type:"utterance", text}
        WS->>FSM: handle_utterance(text)
        FSM->>FSM: lexicon sentiment → score −1.0…+1.0
        FSM->>FSM: classify intent:<br/>competitor / cancel / price / accept / reject
        FSM-->>WS: sentiment event → live needle
        alt competitor named (Salesforce / HubSpot / Linear / Acme)
            FSM->>FSM: ENGAGE_LISTEN → COUNTER_INTEL
            FSM->>BC: lookup battlecard
            BC-->>FSM: weakness, proof_points,<br/>pricing_angle, rebuttal
            FSM-->>WS: battlecard event + rebuttal line
            FSM->>FSM: COUNTER_INTEL → ENGAGE_LISTEN
        end
        alt cancel / price pressure (or warm accept, no offer)
            FSM->>FSM: ENGAGE_LISTEN → INCENTIVE_AUTH
            FSM->>FSM: next rung on offer ladder<br/>(60% → 80% → 100% of ceiling)
            FSM-->>WS: offer {discount_pct, mrr_before, mrr_after}<br/>+ spoken offer
            FSM->>FSM: INCENTIVE_AUTH → ENGAGE_LISTEN
        else hard reject with live offer
            FSM-->>WS: transcript(agent): polite close-out
            FSM->>FSM: → IDLE, outcome = lost
            FSM-->>WS: call_ended(lost)
        end
        WS-->>UI: transcript bubbles + FSM node glow + sentiment
        UI->>TTS: speak(agent lines)
    end

    Note over TRG,WA: ── PHASE 3: OMNICHANNEL CLOSE ──
    TRG->>STT: "Fine, I'll take the deal"
    STT->>UI: final transcript
    UI->>WS: {type:"utterance"}
    WS->>FSM: handle_utterance → accept intent + live offer
    FSM->>FSM: ENGAGE_LISTEN → OMNICHANNEL_CLOSE
    FSM->>SP: mint retention checkout URL<br/>cs_sim_retention_{acct}_{pct}
    FSM->>WA: queue payload {to: phone,<br/>template: retention_offer_v1,<br/>body: offer + stripe link,<br/>message_id: wamid.simulated.*}
    FSM-->>WS: dispatch {stripe_checkout_url, whatsapp}
    FSM-->>WS: transcript(agent): confirmation read-out
    FSM->>FSM: OMNICHANNEL_CLOSE → IDLE, outcome = saved
    FSM-->>WS: call_ended(saved, discount_pct)
    WS-->>UI: WhatsApp phone card + Stripe chip pop live
    UI->>TTS: speak(confirmation)
    WS->>CRM: INSERT call_log (outcome, discount,<br/>states_visited, transcript)
    WS->>CRM: UPDATE status = 'saved'
    WS-->>UI: customer_status event → roster badge flips to SAVED
```

## Quick start

```powershell
pip install -r requirements.txt   # starlette + uvicorn only, frozen
python seed_customers.py          # 10 enterprise accounts -> data/churn_rescue.db
python -m churn_rescue.server     # http://127.0.0.1:8000
```

Open `http://127.0.0.1:8000` and hit **⚡ SWARM DIAL — TOP 3** to launch three
concurrent retention calls, or **CALL NOW** on a single high-risk row. Type in
a column's input (or click 🎙 for voice) to play the customer.

### Suggested demo script (as the customer)

1. `"This is the worst garbage nightmare ever — we're canceling and moving to Zenith."`
   → sentiment ≤ −0.90 → column flashes red, `MANUAL OVERRIDE` glows; Zenith has
   no battlecard → live OSINT scrape fires with a toast.
2. Click **⚠ MANUAL OVERRIDE** → `HUMAN_TAKEOVER`, Maya bridges in the VP of Sales.
3. `"Honestly it's just too expensive."` → offer escalates a rung.
4. `"Fine, I'll take the deal."` → `OMNICHANNEL_CLOSE`: Stripe link + PDF
   addendum land on the iPhone mockup; when the last call ends the
   **Post-Call Strategic Insights** boardroom overlay fades in.

## API

| Method | Path | Purpose |
| --- | --- | --- |
| `GET` | `/` | Mission Control dashboard |
| `GET` | `/api/customers` | At-risk roster, sorted by churn risk |
| `POST` | `/api/calls/start` | `{customer_id}` → arms FSM, streams events |
| `POST` | `/api/calls/utterance` | `{call_id, text}` — REST fallback for utterances |
| `POST` | `/api/calls/override` | `{call_id}` → `HUMAN_TAKEOVER`, VP bridge line |
| `POST` | `/api/calls/end` | Abort active call |
| `GET` | `/contracts/<file>` | Signed PDF addenda |
| `WS` | `/ws` | Telemetry + `start_call` / `swarm` / `utterance` / `override` / `end_call` / `stt_begin` / `stt_chunk` / `stt_end` |

## Verification

```powershell
python -m unittest tests.test_fsm_flow -v
```

Headless simulation of a full angry-customer call — asserts **100% FSM state
coverage**, transition ordering, battlecard selection, discount cap
enforcement, and dispatch payload integrity. No server or browser needed.

## Project structure

```text
churn_rescue/
  agent.py            # FSM engine: swarm FSMs, intents, sentiment, OSINT routing
  llm.py              # Groq llama3-70b-8192 via stdlib urllib (static fallback)
  stt.py              # AssemblyAI realtime session bridge (Web Speech fallback)
  osint.py            # zero-shot competitor brief scrape (urllib, no SDK)
  tts.py              # System.Speech synth w/ sentiment→rate/volume modulation
  contracts.py        # zero-dependency PDF Subscription Addendum writer
  battlecards.json    # competitor intel: Salesforce, HubSpot, Linear, Acme
  db.py               # SQLite schema + helpers (customers, call_log)
  server.py           # Starlette app: REST + WS telemetry/stt + swarm aggregator
  static/index.html   # Mission Control: 3-col swarm UI, iPhone mockup, CEO overlay
  static/pcm-worklet.js # AudioWorklet: Float32 → PCM16 for the STT uplink
seed_customers.py     # seeds 10 enterprise accounts
tests/test_fsm_flow.py# headless full-call simulation + provider fallback tests
requirements.txt      # frozen, pure-Python deps only
```

## Production note (CALL-E integration)

The FSM is telephony-agnostic: `handle_utterance(text) -> events` is a pure
function of transcript text. To run on real PSTN calls, point CALL-E's live
transcript webhook at `/api/calls/utterance` and stream `agent` transcript
events back as the TTS leg — the browser simulation stands in for that voice
path during the demo.
